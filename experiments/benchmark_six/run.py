"""Serial, isolated native official/FixedAssembly comparison; no candidate replay."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import closing
from dataclasses import asdict, is_dataclass
import hashlib
import importlib
import importlib.util
import json
import os
from pathlib import Path
import re
import signal
import socket
import sqlite3
import subprocess
import sys
import time
import traceback

CASE_ORDER = ('ABO_B075X2XZDD', 'Toys4K_dinosaur_020', 'Toys4K_robot_050',
              'Toys4K_dragon_007', 'ABO_B082JGPBLQ', 'Toys4K_bunny_004')
ARMS = ('official', 'ours')
MAX_ROUNDS = 10
SOURCE_REPAIR_LIMIT = 9
HERE = Path(__file__).resolve()
PARTITION_OBJECTIVE = dict(method='dapper_fdm_2015', alpha=.3, r_vox=.1,
                           orientation_set='axis_aligned_24', layout='independent_bed')
ASSEMBLY_REQUIREMENT = '''Use the native FixedAssembly / TabSlot manufacturing assembly API.
Preserve the requested appearance and function. Use the Planner's component and
connection plan; do not add a separate print-part decision stage. A single planned
print part needs no connector. Within each print part retain ordinary modeling;
between print parts use assembly.connect for both mating sides and placement.
Connections start from root_part, with at least one endpoint already placed.
Freeze scale, final dimensions, single-sided fit allowance, and physics settings.
The selected topology, overhang/partition, and applicable standing feedback share
the same source repair budget with native image/code review. FEA is disabled.
Use one initial complete source and at most nine source repairs in ten rounds.
Stop early under the native workflow policy; never regenerate a fresh candidate.
'''


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.writing')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=json_value) + '\n', encoding='utf-8')
    temporary.replace(path)
    return path


def json_value(value):
    if isinstance(value, Path):
        return str(value)
    if hasattr(value, 'raw_item'):
        raw = value.raw_item
        return {'type': getattr(value, 'type', type(value).__name__),
                'raw_item': raw.model_dump(mode='json') if hasattr(raw, 'model_dump') else raw}
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, 'model_dump'):
        return value.model_dump(mode='json')
    return str(value)


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), default=json_value).encode()).hexdigest()


def safe_reason(error):
    return re.sub(r'\bsk-[A-Za-z0-9_-]+', '[REDACTED]', str(error))[:1200]


def load_cases(root):
    payload = read_json(root / 'config/cases.json')
    rows = payload['cases'] if isinstance(payload, dict) else payload
    cases = {}
    for row in rows:
        cid = row.get('case_id', row.get('id'))
        case = {key: row[key] for key in ('requirement', 'input_image', 'final_size_mm',
                                         'voxel_pitch_mm', 'standing_applicable')}
        case['case_id'] = cid
        image = Path(case['input_image']).expanduser()
        case['input_image'] = str((root / image).resolve() if not image.is_absolute() else image.resolve())
        case['input_sha256'] = sha256(case['input_image'])
        if row.get('input_sha256') and row['input_sha256'] != case['input_sha256']:
            raise ValueError(f'frozen reference image changed: {cid}')
        if not case['requirement'].strip() or len(case['final_size_mm']) != 3 or any(float(n) <= 0 for n in case['final_size_mm']):
            raise ValueError(f'invalid common task: {cid}')
        if cid in cases:
            raise ValueError(f'duplicate case: {cid}')
        cases[cid] = case
    if tuple(cases) != CASE_ORDER:
        raise ValueError('cases must contain the six frozen cases in the prescribed order')
    if cases['Toys4K_dragon_007']['standing_applicable'] is not False:
        raise ValueError('dragon standing must be NOT_APPLICABLE')
    return cases


def load_envs(root):
    payload = read_json(root / 'config/envs.json')
    rows = payload.get('arms', payload)
    arms = {}
    for arm in ARMS:
        row = dict(rows[arm])
        row['code_root'] = str(Path(row.get('code_root', row.get('repo'))).resolve())
        for key in ('python', 'profile'):
            path = Path(row[key]).expanduser()
            # Resolving venv/bin/python follows its symlink and loses the venv.
            row[key] = str(path.absolute() if key == 'python' else path.resolve())
            if not Path(row[key]).is_file():
                raise FileNotFoundError(row[key])
        arms[arm] = row
    if arms['official']['code_root'] == arms['ours']['code_root']:
        raise ValueError('official and ours must use different code roots')
    return arms, payload.get('common_env', {})


def clean_environment(arm_env, common=None):
    env = {k: v for k, v in os.environ.items() if not k.lower().endswith('_proxy')
           and k.lower() not in {'all_proxy', 'http_proxy', 'https_proxy', 'no_proxy'}
           and k not in {'PYTHONPATH', 'PYTHONHOME', 'ADSL_GPU_RENDER_QUEUE',
                        'ADSL_RENDER_ENGINE', 'ADSL_RENDER_WIDTH', 'ADSL_RENDER_HEIGHT',
                        'ADSL_RENDER_SAMPLES'}}
    env.update({k: str(v) for k, v in (common or {}).items() if not k.lower().endswith('_proxy')})
    env.update({k: str(v) for k, v in arm_env.get('env', {}).items() if not k.lower().endswith('_proxy')})
    env.pop('ADSL_GPU_RENDER_QUEUE', None)
    env.update(PYTHONNOUSERSITE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONUNBUFFERED='1',
               LIBGL_ALWAYS_SOFTWARE='1', GALLIUM_DRIVER='llvmpipe',
               ADSL_RENDER_ENGINE='BLENDER_EEVEE', OPENAI_AGENTS_DISABLE_TRACING='1')
    if arm_env.get('pythonpath'):
        values = arm_env['pythonpath']
        env['PYTHONPATH'] = os.pathsep.join(values) if isinstance(values, list) else str(values)
    return env


def common_requirement(case):
    return case['requirement']


def request_fields(case, arm, physics):
    fields = dict(requirement=common_requirement(case), image_paths=[case['input_image']],
                  articulation=False, max_rounds=MAX_ROUNDS)
    if arm == 'ours':
        frozen = json.loads(json.dumps(physics))
        frozen.pop('fea', None)
        frozen.setdefault('overhang', {}).update(partition_objective=PARTITION_OBJECTIVE,
                                                partition_editable=True)
        fields.update(requirement=fields['requirement'] + '\n\n' + ASSEMBLY_REQUIREMENT,
                      fixed_assembly=dict(mm_per_unit=1., fit_offset_mm=.2,
                                          final_size_mm=case['final_size_mm'],
                                          validation_mode='visual_only', require_multiple_parts=False,
                                          physics=frozen),
                      repair_policy=dict(print_partition_editable=True, max_total_candidates=SOURCE_REPAIR_LIMIT,
                                         print_orientation_editable=bool(frozen['overhang'].get('orientation_editable', False))),
                      checker_names=['assembly_topology', 'assembly_overhang'] +
                                    (['assembly_standing'] if case['standing_applicable'] else []))
    return fields


def source_tree_hash(root):
    paths = []
    for name in ('adsl-agents', 'adsl-core'):
        directory = Path(root) / name
        paths.extend(p for p in directory.rglob('*') if p.is_file() and
                     p.suffix in {'.py', '.md', '.toml'} and '__pycache__' not in p.parts)
    if not paths:
        raise ValueError(f'no native source files under {root}')
    return digest({str(p.relative_to(root)): sha256(p) for p in sorted(paths)})


def frozen_fingerprint(root, cases, arms):
    return dict(config={name: sha256(root / 'config' / name) for name in ('cases.json', 'envs.json', 'physics.json')},
                inputs={cid: c['input_sha256'] for cid, c in cases.items()},
                sources={arm: source_tree_hash(e['code_root']) for arm, e in arms.items()},
                profiles={arm: sha256(e['profile']) for arm, e in arms.items()},
                runner=sha256(HERE), evaluator=sha256(HERE.with_name('evaluate.py')),
                review_builder=sha256(HERE.with_name('build_review.py')),
                budget={'initial_generation_limit': 1, 'source_repair_limit': SOURCE_REPAIR_LIMIT, 'max_rounds': MAX_ROUNDS})


def verify_module_origins(arm_env):
    paths = {}
    root = Path(arm_env['code_root']).resolve()
    for name in ('adsl.agents.service', 'adsl.agents.models', 'adsl.agents.utils.config',
                 'adsl.agents.utils.asset_executor', 'adsl.core', 'adsl.tools.render'):
        module = importlib.import_module(name)
        path = Path(module.__file__).resolve()
        if not path.is_relative_to(root):
            raise ValueError(f'module origin mismatch: {name}: {path} outside {root}')
        paths[name] = {'path': str(path), 'sha256': sha256(path)}
    return paths


def probe(root, arm, arms):
    import yaml
    from agents import AgentOutputSchema
    env = arms[arm]
    paths = verify_module_origins(env)
    profile = yaml.safe_load(Path(env['profile']).read_text(encoding='utf-8'))
    if profile['params']['base_url'].rstrip('/') != 'http://127.0.0.1:28317/v1':
        raise ValueError('both profiles must use the fixed localhost:28317 endpoint')
    if any(k.lower().endswith('_proxy') for k in os.environ):
        raise ValueError('proxy variables reached the isolated worker')
    models = importlib.import_module('adsl.agents.models')
    service = importlib.import_module('adsl.agents.service')
    prompts = importlib.import_module('adsl.agents.prompts')
    inputs = importlib.import_module('adsl.agents.utils.inputs')
    case = next(iter(load_cases(root).values()))
    fields = request_fields(case, arm, read_json(root / 'config/physics.json'))
    values = {k: v for k, v in fields.items() if k not in {'checker_names', 'repair_policy'}}
    values['image_paths'] = tuple(Path(p) for p in values['image_paths'])
    if arm == 'ours':
        values['repair_policy'] = models.RepairPolicy.model_validate(fields['repair_policy'])
        AgentOutputSchema(models.FixedAssemblyPlan)
        AgentOutputSchema(models.GradedImageCriticDecision)
        service.ObjectWorkflow._review_view_labels([Path(f'view_{n}.png') for n in range(8)], start_index=2)
    else:
        AgentOutputSchema(models.ObjectPlan)
    request = models.ObjectRequest(workspace=root / 'preflight/no_generation', task_id='compatibility_only', **values)
    service.ObjectWorkflow._validate_request(request)
    if not callable(service.ObjectWorkflow._runtime) or not prompts.object_prompt('planner', articulation=False):
        raise ValueError('native runtime/prompt helper API unavailable')
    # Exercise native input assembly with the real frozen image, without a model.
    if not inputs.user_input(request.requirement, request.image_paths):
        raise ValueError('native input helper did not preserve reference input')
    write_json(root / 'preflight' / f'{arm}_imports.json', dict(arm=arm, code_root=env['code_root'],
               python=sys.executable, modules=paths, proxy_environment='CLEARED', transport_client='native',
               cpu_software_render=True, schemas_and_input_helpers='VERIFIED_WITHOUT_API_CALL',
               render_configuration={'engine': 'BLENDER_EEVEE',
                   'width': int(os.environ.get('ADSL_RENDER_WIDTH', 1024)),
                   'height': int(os.environ.get('ADSL_RENDER_HEIGHT', 1024)),
                   'samples': int(os.environ.get('ADSL_RENDER_SAMPLES', 256)),
                   'native_render_kwargs_adapter': os.environ.get('ADSL_NATIVE_RENDER_KWARGS') == '1'},
               profile_max_retries=profile['params'].get('max_retries', 0)))
    return paths


def local_session_directory(root, case_id, arm, kind='generation'):
    key = digest(str(root.resolve()))[:16]
    return Path('/tmp') / f'adsl-six-sessions-{key}' / case_id / arm / kind


def audited_runtime(runtime, *, root, case_id, arm, kind='generation'):
    sessions = importlib.import_module('adsl.agents.utils.sessions')
    directory = local_session_directory(root, case_id, arm, kind)
    runtime.sessions = sessions.SessionManager(directory, f'six_{case_id}_{arm}_{kind}')
    evidence = root / 'jobs' / case_id / arm / ('generation' if kind == 'generation' else 'review') / 'role_calls'
    call = runtime.run
    counters = {'initial': 0, 'repairs': 0}
    runtime.six_shared_faults = []
    async def recorded_run(**kwargs):
        stage = kwargs['stage']
        initial = stage.startswith('initial_code')
        repair = any(stage.startswith(prefix) for prefix in ('assembly_repair:', 'debugger_patch:', 'critic_patch:', 'gate_patch:'))
        if initial:
            counters['initial'] += 1
        if repair:
            counters['repairs'] += 1
        if counters['initial'] > 1 or counters['repairs'] > SOURCE_REPAIR_LIMIT:
            raise ValueError('frozen source generation/repair budget exceeded')
        index = len(list(evidence.glob('*.json'))) + 1
        path = evidence / f'{index:03d}_{stage.replace(":", "_")}.json'
        agent = kwargs['agent']
        row = dict(stage=stage, role=kwargs['role'], agent=agent.name,
                   instructions=agent.instructions, input=kwargs['input'],
                   tools=[getattr(t, 'name', type(t).__name__) for t in agent.tools],
                   started_at=time.time(), status='RUNNING', budget=dict(counters))
        write_json(path, row)
        try:
            result = await call(**kwargs)
            row.update(status='COMPLETED', output=result.final_output,
                       new_items=[json_value(i) for i in result.new_items])
            return result
        except Exception as error:
            row.update(status='ERROR', error={'type': type(error).__name__, 'reason': safe_reason(error)})
            if shared_fault(error):
                runtime.six_shared_faults.append({'stage': stage, **row['error']})
            raise
        finally:
            row.update(finished_at=time.time(),
                       tool_events=[json_value(e) for e in getattr(kwargs.get('context'), 'events', [])])
            write_json(path, row)
    runtime.run = recorded_run
    runtime.six_counters = counters
    return runtime


def snapshot_sessions(runtime, target):
    if runtime is None or not runtime.sessions.database_path.is_file():
        return {'status': 'NOT_AVAILABLE'}
    source = runtime.sessions.database_path
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        with closing(sqlite3.connect(source.as_uri() + '?mode=ro', uri=True)) as database:
            with closing(sqlite3.connect(target)) as output:
                database.backup(output)
                if output.execute('PRAGMA quick_check').fetchone() != ('ok',):
                    raise sqlite3.DatabaseError('session snapshot quick_check failed')
        return {'status': 'SAVED', 'path': str(target), 'sha256': sha256(target), 'live_path': str(source)}
    except (OSError, sqlite3.Error) as error:
        return {'status': 'ERROR', 'live_path': str(source), 'reason': safe_reason(error)}


def shared_fault(error):
    names = {cls.__name__ for cls in type(error).__mro__}
    if names & {'APIError', 'APIConnectionError', 'APITimeoutError', 'RateLimitError', 'AuthenticationError',
                'PermissionDeniedError', 'InternalServerError', 'AssetInfrastructureError',
                'ImportError', 'ModuleNotFoundError', 'ConnectionError', 'TimeoutError'}:
        return True
    return any(s in str(error).lower() for s in ('module origin mismatch', 'frozen source', 'no space left',
               'database disk image is malformed', 'disk i/o error', 'shared_environment_unavailable'))


def artifact_hashes(job):
    paths = {Path(job[key]) for key in ('source', 'selected_glb', 'selected_manifest') if job.get(key)}
    paths.update(Path(p) for p in job.get('render_paths', []))
    paths.update(Path(job[key]) for key in ('native_partition_result', 'native_assembly_result', 'native_version_ledger')
                 if job.get(key))
    if job.get('selected_manifest'):
        paths.update(p for p in Path(job['selected_manifest']).parent.rglob('*') if p.is_file())
    return {str(path): sha256(path) for path in sorted(paths) if path.is_file()}


def hashes_match(hashes):
    return bool(hashes) and all(Path(p).is_file() and sha256(p) == expected for p, expected in hashes.items())


def generation_result(job, native, work):
    """Read only the native returned selection; offline metrics never enter here."""
    job.update(source=str(native.source_path), selected_glb=str(native.glb_path) if native.glb_path else None,
               selected_manifest=str(work / 'assembly/assembly_manifest.json') if
               job['arm'] == 'ours' and (work / 'assembly/assembly_manifest.json').is_file() else None,
               render_paths=[str(p) for p in native.render_paths], approved=bool(native.approved),
               approved_round=int(native.selected_round), usage=json_value(native.usage))
    usable = bool(native.glb_path and Path(native.glb_path).is_file())
    job['selected_source_sha256'] = sha256(native.source_path)
    job['selected_glb_sha256'] = sha256(native.glb_path) if usable else None
    job['selected_manifest_sha256'] = sha256(job['selected_manifest']) if job['selected_manifest'] else None
    job['generation'].update(status='COMPLETED' if usable else 'generation_failed',
                             reason=None if usable else 'native workflow returned no selected GLB')
    if job['arm'] == 'ours' and usable:
        job.update(native_assembly_result=str(work / 'assembly_result.json'),
                   native_version_ledger=str(work / 'assembly_versions.json'))
        selected_source_sha = job['selected_source_sha256']
        publication = read_json(job['native_assembly_result'])
        ledger = read_json(job['native_version_ledger'])
        if publication['version_id'] != ledger['retained'] or publication['source_sha256'] != selected_source_sha:
            raise ValueError('frozen source selected native publication mismatch')
        check = publication.get('reviews', {}).get('assembly_overhang', {})
        report_path = check.get('artifacts', {}).get('report')
        if report_path and Path(report_path).is_file():
            saved = read_json(report_path)
            bound_sha = saved.get('assumptions', {}).get('source_sha256')
            manifest_sha = saved.get('assumptions', {}).get('manifest_sha256')
            if bound_sha != selected_source_sha or not job['selected_manifest'] or manifest_sha != sha256(job['selected_manifest']):
                raise ValueError('frozen source selected native checker binding mismatch')
            job.update(native_partition_result=report_path,
                       native_partition_binding=dict(status='BOUND', version_id=publication['version_id'],
                           source_sha256=selected_source_sha, report_sha256=sha256(report_path), manifest_sha256=manifest_sha))
        else:
            job['native_partition_binding'] = dict(status='UNAVAILABLE', reason='selected version has no bound native checker report')
    return job


async def generate_worker(root, case_id, arm, cases, arms, physics):
    probe(root, arm, arms)
    folder = root / 'jobs' / case_id / arm
    path = folder / 'job.json'
    job = read_json(path)
    if job['generation']['status'] != 'PENDING':
        raise ValueError('no automatic replay of a started generation job')
    if job['input_sha256'] != sha256(cases[case_id]['input_image']):
        raise ValueError('frozen reference image changed before worker generation')
    fields = request_fields(cases[case_id], arm, physics)
    if digest(fields) != job['request_sha256']:
        raise ValueError('frozen source request changed')
    models = importlib.import_module('adsl.agents.models')
    service = importlib.import_module('adsl.agents.service')
    work = folder / 'generation/native'
    request_values = {k: v for k, v in fields.items() if k not in {'checker_names', 'repair_policy'}}
    request_values['image_paths'] = tuple(Path(p) for p in fields['image_paths'])
    if arm == 'ours':
        topology = importlib.import_module('adsl.agents.assembly_topology')
        checks = importlib.import_module('adsl.agents.assembly_physics')
        request_values['checker_specs'] = tuple(topology.checker_spec() if n == 'assembly_topology' else
                                               checks.checker_spec(n) for n in fields['checker_names'])
        request_values['repair_policy'] = models.RepairPolicy.model_validate(fields['repair_policy'])
    request = models.ObjectRequest(workspace=work, task_id=f'six_{case_id}_{arm}', **request_values)
    class RecordedWorkflow(service.ObjectWorkflow):
        def _runtime(self, request, workspace, **kwargs):
            runtime = super()._runtime(request, workspace, **kwargs)
            self.six_runtime = audited_runtime(runtime, root=root, case_id=case_id, arm=arm)
            return self.six_runtime
    workflow = RecordedWorkflow(arms[arm]['profile'])
    started = time.time()
    job['generation'].update(status='RUNNING', started_at=started, initial_generation_reserved=True)
    write_json(path, job)
    try:
        native = await workflow.generate(request)
        generation_result(job, native, work)
    except Exception as error:
        pause = shared_fault(error)
        job['generation'].update(status='PAUSED' if pause else 'generation_failed',
                                 reason=safe_reason(error), error_type=type(error).__name__)
        job['pause_batch'] = pause
        (folder / 'generation/error.log').write_text(safe_reason(error) + '\n' + traceback.format_exc(), encoding='utf-8')
    runtime = getattr(workflow, 'six_runtime', None)
    if runtime:
        job['usage'] = json_value(runtime.usage.totals())
        job['generation']['actual_initial_calls'] = runtime.six_counters['initial']
        job['generation']['actual_source_repairs'] = runtime.six_counters['repairs']
        job['generation']['unknown_usage_role_calls'] = sum(
            read_json(p).get('status') == 'ERROR' for p in (folder / 'generation/role_calls').glob('*.json'))
        if runtime.six_shared_faults:
            job['pause_batch'] = True
            job['generation'].update(status='PAUSED', reason='shared API/infrastructure fault recorded in native role call',
                                     shared_failures=runtime.six_shared_faults)
    job['generation'].update(finished_at=time.time(), elapsed_seconds=time.time() - started)
    job['artifact_hashes'] = artifact_hashes(job)
    job['sessions'] = snapshot_sessions(runtime, folder / 'generation/sessions_snapshot.sqlite3')
    if job['generation']['status'] == 'generation_failed':
        write_json(folder / 'generation_failed.json', job['generation'])
    write_json(path, job)
    return 2 if job.get('pause_batch') else 0


async def review_worker(root, case_id, arm, cases, arms):
    """One common read-only critic call, with no method label or source tools."""
    probe(root, 'ours', arms)
    folder = root / 'jobs' / case_id / arm
    request = read_json(folder / 'evaluation/image_critic_request.json')
    destination = folder / 'review/image_critic.json'
    if destination.exists():
        return 0
    if request['status'] != 'READY' or len(request.get('rendered_images', [])) != 8:
        write_json(destination, dict(status='INDETERMINATE', reason='unified final native views unavailable',
                   read_only=True, no_candidate_selection=True))
        return 0
    marker = folder / 'review/started.json'
    if marker.exists():
        raise ValueError('no automatic replay of a started common review')
    rendered = [Path(p) for p in request['rendered_images']]
    reference = Path(request['reference_image'])
    if sha256(reference) != cases[case_id]['input_sha256'] or any(not p.is_file() for p in rendered):
        raise ValueError('common review frozen image input changed')
    inputs = importlib.import_module('adsl.agents.utils.inputs')
    prompts = importlib.import_module('adsl.agents.prompts')
    models = importlib.import_module('adsl.agents.models')
    runtime_cls = importlib.import_module('adsl.agents.utils.runner').AgentRuntime
    service = importlib.import_module('adsl.agents.service').ObjectWorkflow
    runtime = runtime_cls(model_profile=arms['ours']['profile'], workspace=folder / 'review/native',
                          task_id=f'blind_{digest(str(folder))[:16]}')
    audited_runtime(runtime, root=root, case_id=case_id, arm=arm, kind='review')
    agent = runtime.agent(name='object-image-critic', tools=(), output_type=models.GradedImageCriticDecision,
                          instructions=prompts.object_prompt('image_critic_review', articulation=False))
    payload = dict(requirement=common_requirement(cases[case_id]), round=1, max_rounds=1,
                   reference_image_count=1, render_image_count=8,
                   render_views=service._review_view_labels(rendered, start_index=2),
                   previous_image_decisions=[], code_critic_corrections=[],
                   assessment='Read-only final appearance assessment; no edits, regeneration, or candidate selection.')
    write_json(marker, dict(started_at=time.time(), input_sha256=digest(payload),
                           image_sha256={str(p): sha256(p) for p in [reference, *rendered]}))
    try:
        result = await runtime.run(agent=agent, input=inputs.user_input(json.dumps(payload), [reference, *rendered]),
                                   role='image-critic:final', stage='final_readonly_image_critic', max_turns=1)
        decision = service._typed_output(result.final_output, models.GradedImageCriticDecision)
        decision = service._normalize_visual_decision(decision)
        write_json(destination, dict(status='COMPLETED', decision=decision.model_dump(),
                   usage=json_value(runtime.usage.totals()), read_only=True, no_candidate_selection=True,
                   sessions=snapshot_sessions(runtime, folder / 'review/sessions_snapshot.sqlite3')))
        return 0
    except Exception as error:
        write_json(folder / 'review/error.json', dict(status='PAUSED', error_type=type(error).__name__,
                   reason=safe_reason(error), usage=json_value(runtime.usage.totals()),
                   sessions=snapshot_sessions(runtime, folder / 'review/sessions_snapshot.sqlite3')))
        return 2


def checkpoint(root, status, *, reason=None, current_job=None):
    rows = []
    for cid in CASE_ORDER:
        for arm in ARMS:
            path = root / 'jobs' / cid / arm / 'job.json'
            if path.is_file():
                rows.append(read_json(path))
    value = dict(status=status, updated_at=time.time(), case_order=CASE_ORDER, arm_order=ARMS,
                 total_jobs=12, current_job=current_job, reason=reason, jobs=rows)
    write_json(root / 'checkpoint.json', {k: v for k, v in value.items() if k != 'jobs'})
    write_json(root / 'results.json', value)
    try:
        # Launchers and official workers have different cwd/package namespaces.
        spec = importlib.util.spec_from_file_location('benchmark_six_build_review', HERE.with_name('build_review.py'))
        builder = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(builder)
        builder.build(root)
    except (ImportError, OSError, ValueError, KeyError, TypeError) as error:
        # Report failure must not hide the real batch checkpoint or its cause.
        write_json(root / 'review/build_error.json', {'reason': safe_reason(error), 'updated_at': time.time()})


def initialize(root, cases, arms, physics):
    fingerprint = frozen_fingerprint(root, cases, arms)
    marker = root / 'batch.json'
    if marker.exists():
        if read_json(marker)['fingerprint'] != fingerprint:
            raise ValueError('frozen batch hashes changed; refuse budget reset or candidate replay')
        return
    write_json(marker, dict(created_at=time.time(), fingerprint=fingerprint,
                           case_order=CASE_ORDER, arm_order=ARMS, max_rounds=MAX_ROUNDS,
                           initial_generation_limit=1, source_repair_limit=SOURCE_REPAIR_LIMIT))
    for cid in CASE_ORDER:
        for arm in ARMS:
            folder = root / 'jobs' / cid / arm
            fields = request_fields(cases[cid], arm, physics)
            write_json(folder / 'input.json', fields)
            write_json(folder / 'job.json', dict(case_id=cid, arm=arm, status='PENDING',
                       input_sha256=cases[cid]['input_sha256'], request_sha256=digest(fields),
                       generation=dict(status='PENDING', max_rounds=MAX_ROUNDS, initial_generation_limit=1,
                                       source_repair_limit=SOURCE_REPAIR_LIMIT),
                       code_root=arms[arm]['code_root'], profile_sha256=sha256(arms[arm]['profile']),
                       source=None, selected_glb=None, selected_manifest=None, approved=False,
                       approved_round=None, usage={}, render_paths=[], artifact_hashes={}))


def child(root, arms, common, arm, arguments, log):
    with Path(log).open('a', encoding='utf-8') as stream:
        return subprocess.run([arms[arm]['python'], str(HERE), '--run-root', str(root), *arguments],
                              cwd=arms[arm]['code_root'], env=clean_environment(arms[arm], common),
                              stdout=stream, stderr=subprocess.STDOUT).returncode


def evaluate_child(root, case_id, arm, arms, common):
    folder = root / 'jobs' / case_id / arm
    command = [arms['ours']['python'], str(HERE.with_name('evaluate.py')),
               '--run-root', str(root), '--case', case_id, '--arm', arm]
    with (folder / 'evaluation.log').open('a', encoding='utf-8') as stream:
        process = subprocess.Popen(command, cwd=arms['ours']['code_root'],
                    env=clean_environment(arms['ours'], common), stdout=stream, stderr=subprocess.STDOUT,
                    start_new_session=True)
        try:
            return process.wait(timeout=3600)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
    path = folder / 'evaluation/result.json'
    saved = read_json(path) if path.is_file() else dict(case_id=case_id, arm=arm, metrics={'N': None})
    saved.update(status='INDETERMINATE', failure_stage='evaluation_timeout',
                 reason='offline evaluation exceeded its fixed 3600-second budget')
    write_json(path, saved)
    write_json(folder / 'evaluation/image_critic_request.json',
               dict(status='INDETERMINATE', rendered_images=[], read_only=True, no_candidate_selection=True))
    return 0


def merge_final_appearance(evaluation_path, review_path):
    evaluation = read_json(evaluation_path)
    review = read_json(review_path)
    if evaluation.get('failure_stage') == 'generation':
        evaluation['appearance']['final_review'] = str(review_path)
    elif review['status'] == 'COMPLETED':
        decision = review['decision']
        evaluation['appearance'] = dict(status='PASS' if decision['approved'] else 'FAIL',
                decision=decision, final_review=str(review_path), read_only=True)
        if not decision['approved']:
            evaluation['status'] = 'FAIL'
    else:
        evaluation['appearance'] = dict(status='INDETERMINATE', reason=review.get('reason'),
                final_review=str(review_path), read_only=True)
        if evaluation['status'] == 'PASS':
            evaluation['status'] = 'INDETERMINATE'
    write_json(evaluation_path, evaluation)
    return evaluation, review


def batch(root, *, preflight_only=False):
    root.mkdir(parents=True, exist_ok=True)
    try:
        cases = load_cases(root)
        arms, common = load_envs(root)
        physics = read_json(root / 'config/physics.json')
        for arm in ARMS:
            log = root / 'logs' / f'{arm}_preflight.log'
            log.parent.mkdir(parents=True, exist_ok=True)
            if child(root, arms, common, arm, ['--probe', '--arm', arm], log):
                raise RuntimeError(f'{arm} import/profile preflight failed; see {log}')
            evidence = read_json(root / 'preflight' / f'{arm}_imports.json')
            if evidence['code_root'] != arms[arm]['code_root'] or any(
                    not Path(row['path']).is_relative_to(arms[arm]['code_root']) for row in evidence['modules'].values()):
                raise ValueError('parent module origin mismatch')
        if preflight_only:
            return 0
        initialize(root, cases, arms, physics)
        with socket.create_connection(('127.0.0.1', 28317), timeout=5):
            pass
        checkpoint(root, 'RUNNING')
        for cid in CASE_ORDER:
            for arm in ARMS:
                folder = root / 'jobs' / cid / arm
                path = folder / 'job.json'
                job = read_json(path)
                checkpoint(root, 'RUNNING', current_job=f'{cid}/{arm}')
                print(f'{cid}/{arm}: {job["status"]}; native budget 1 initial + at most 9 repairs', flush=True)
                if job['status'] == 'COMPLETED':
                    if not hashes_match(job.get('completion_hashes', {})):
                        raise ValueError(f'completed job artifact hashes changed: {cid}/{arm}')
                    continue
                generation_status = job['generation']['status']
                if generation_status == 'PENDING':
                    if child(root, arms, common, arm, ['--worker', '--case', cid, '--arm', arm], folder / 'generation.log'):
                        raise RuntimeError(f'{cid}/{arm}: generation paused; see generation.log and job.json')
                    job = read_json(path)
                elif generation_status not in {'COMPLETED', 'generation_failed'}:
                    raise RuntimeError(f'{cid}/{arm}: started generation requires review; no automatic regeneration')
                if job['generation']['status'] == 'COMPLETED' and not hashes_match(job['artifact_hashes']):
                    raise ValueError(f'native selected artifacts changed: {cid}/{arm}')
                evaluation = folder / 'evaluation/result.json'
                review_request = folder / 'evaluation/image_critic_request.json'
                if not evaluation.exists() or not review_request.exists():
                    rc = evaluate_child(root, cid, arm, arms, common)
                    if rc or not evaluation.is_file():
                        raise RuntimeError(f'{cid}/{arm}: offline evaluation infrastructure failed; see evaluation.log')
                if review_request.is_file():
                    if child(root, arms, common, 'ours', ['--review-worker', '--case', cid, '--arm', arm], folder / 'review.log'):
                        raise RuntimeError(f'{cid}/{arm}: common image review paused; see review.log')
                else:
                    write_json(folder / 'review/image_critic.json', dict(status='INDETERMINATE',
                               reason='no unified final native views available', read_only=True, no_candidate_selection=True))
                job = read_json(path)
                job.update(status='COMPLETED', evaluation=str(evaluation), final_image_review=str(folder / 'review/image_critic.json'))
                final_result, review = merge_final_appearance(evaluation, folder / 'review/image_critic.json')
                job.update(offline_evaluation=final_result, final_image_review_result=review)
                files = {str(evaluation), str(folder / 'review/image_critic.json')}
                job['completion_hashes'] = {**job['artifact_hashes'], **{p: sha256(p) for p in sorted(files)}}
                write_json(path, job)
                checkpoint(root, 'RUNNING', current_job=f'{cid}/{arm}')
        checkpoint(root, 'COMPLETED')
        return 0
    except Exception as error:
        checkpoint(root, 'PAUSED', reason={'type': type(error).__name__, 'message': safe_reason(error)})
        print(f'Batch PAUSED: {safe_reason(error)}', flush=True)
        return 2


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-root', '--root', dest='root', type=Path, required=True)
    parser.add_argument('--case', choices=CASE_ORDER)
    parser.add_argument('--arm', choices=ARMS)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--worker', action='store_true')
    mode.add_argument('--review-worker', action='store_true')
    mode.add_argument('--probe', action='store_true')
    mode.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args(argv)
    root = args.root.resolve()
    if args.worker or args.review_worker or args.probe:
        if not args.arm or ((args.worker or args.review_worker) and not args.case):
            parser.error('worker/review require --case and --arm; probe requires --arm')
        cases = load_cases(root)
        arms, _ = load_envs(root)
        if args.probe:
            probe(root, args.arm, arms)
            return 0
        if args.review_worker:
            return asyncio.run(review_worker(root, args.case, args.arm, cases, arms))
        return asyncio.run(generate_worker(root, args.case, args.arm, cases, arms, read_json(root / 'config/physics.json')))
    return batch(root, preflight_only=args.preflight_only)


if __name__ == '__main__':
    raise SystemExit(main())
