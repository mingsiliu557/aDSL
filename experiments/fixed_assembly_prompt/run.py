"""Frozen original prompts -> native ObjectWorkflow.generate; no asset editing."""
from __future__ import annotations

import argparse
import ast
import asyncio
from dataclasses import asdict
from contextlib import closing
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from agents.models.interface import Model
from adsl.agents.models import ObjectRequest, CheckerSpec, RepairPolicy
from adsl.agents.overhang_edit import assert_version, file_hash
from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.io import read_json, write_json
from adsl.agents.utils.sessions import SessionManager

IDS = ('SF07', 'SF03', 'SF13')
SIZES = {'SF07':[120.,120.,120.], 'SF03':[90.,80.,180.], 'SF13':[100.,32.,200.]}
PROFILE = REPO/'adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml'
SESSION_ROOT = REPO/'temp'/'assembly_sessions'
MANIFEST = REPO/'experiments/standing_fea_30/case_manifest.json'
MANUFACTURING = '''Generate a NEW complete aDSL program from the original task below, with static fixed manufacturing assembly, not articulation. Use the existing Planner's component and connection plan; Coder implements that plan in assembly using connectors for its connections. Do not add a separate print-part decision stage. Do not assume one Asset/class per print part or impose a minimum number of print parts; a single planned print part needs no connector. Use ONLY existing FixedAssembly / TabSlot and a receiver-first rooted tree. Within a print part retain normal modeling and attach_part. Between print parts use assembly.connect to generate BOTH mating sides and placement; no global union across print parts. The parameters argument of connect must be a TabSlot object (or an entry containing that object), not a dictionary of dimensions. Preserve the requested appearance and function. Frozen scale, overall dimensions and single-sided fit allowance below must not change. These are geometric demonstration dimensions; clearance is not proof of physical fixation. Use one complete initial source.py and repair only within the frozen budget, with existing Image/Code Critic in visual_only mode and export consistency checks. Assembly geometry checks are NOT_EVALUATED; visual approval is not manufacturing approval. No physical checkers, snap_floaters, or extra interface types.'''


def prepare(root, max_rounds=5, *, cases=IDS, assembly_topology=False, profile=PROFILE, sizes=None, tools=(), physics=None):
    if not 1 <= max_rounds <= 5:
        raise ValueError('max_rounds must be between 1 and 5')
    root.mkdir(parents=True, exist_ok=True)
    marker = root/'batch.json'
    if marker.exists():
        print('Existing batch: preserve frozen repair limits; --max-rounds only affects new batches.', flush=True)
        return
    manifest_cases = {c['case_id']:c for c in read_json(MANIFEST)['cases']}
    from adsl.agents.assembly_physics import NAMES,checker_spec as physics_spec
    selected=list(dict.fromkeys([*(['assembly_topology'] if assembly_topology else []),*tools]))
    if set(selected)-set(NAMES):raise ValueError('unknown fixed-assembly tool')
    for cid in cases:
        case = manifest_cases[cid]
        inputs = {'case_id':cid, 'experiment_type':'fixed_assembly_prompt_to_3d',
            'original_task':{'prompt':case['prompt'], 'image_paths':[]},
            'manufacturing_requirements':MANUFACTURING + (
                f' Budget: at most {max_rounds} evaluation rounds, including the initial review,'
                f' and at most {max_rounds-1} source repairs. Stop early on approval or explicit no change;'
                ' do not force edits to exhaust the budget.'),
            'fixed_assembly':{'mm_per_unit':1., 'fit_offset_mm':.2, 'final_size_mm':(sizes or SIZES)[cid],
                              'validation_mode':'visual_only', 'require_multiple_parts':False},
            'provenance':{'manifest':str(MANIFEST), 'manifest_sha256':file_hash(MANIFEST),
                'field':f'cases[case_id={cid}].prompt', 'dataset':case['dataset'],
                'object_id':case['object_id'], 'caption_source':case['caption_source'],
                'references':'Saved original batch create command had no --image argument; generated renders excluded. Historical serialized requests are unavailable.',
                'reference_evidence':['experiments/standing_fea_30/run_batch.py::generation_command',
                    'adsl-agents/cli.py --image default []'],
                'geometry_dimensions':'New demonstration specification, not inferred from any saved model.'},
            'initial_generation_limit':1, 'source_repair_limit':max_rounds-1}
        if selected:
            from adsl.agents.assembly_topology import checker_spec
            inputs['checker_specs']=[(checker_spec() if name=='assembly_topology' else physics_spec(name)).model_dump() for name in selected]
            inputs['manufacturing_requirements']=inputs['manufacturing_requirements'].replace(
                'No physical checkers, snap_floaters, or extra interface types.',
                'Selected assembly tools: '+', '.join(selected)+'. Old whole-object checkers remain off. '
                'All available feedback enters one shared repair budget. No snap_floaters or extra interface types.')
            if physics is not None:
                inputs['fixed_assembly']['physics']=physics
                editable=bool(physics.get('overhang',{}).get('orientation_editable',False))
                inputs['repair_policy']={'print_orientation_editable':editable}
                inputs['manufacturing_requirements'] += (
                    '\nPhysics specifications are frozen, not editable. Use actual millimetre dimensions, '
                    'not normalized toy geometry. FEA uses explicit load/support regions, ideal bonding, '
                    'and is separate from self-weight contact stability. Overhang optimization permits '
                    'only print-orientation changes, not shape/use-pose changes. Keep source local/world '
                    'coordinates consistent with configured load and support regions; unresolved mapping '
                    'is unverified, never permission to change loading. These conditions are screening, '
                    'not manufacturing or fastening certification.')
        write_json(root/cid/'input.json', inputs)
    paths = [Path(__file__), profile, REPO/'adsl-agents/service.py', REPO/'adsl-agents/fixed_assembly.py',
             REPO/'adsl-core/core/export/export_assembly.py']
    write_json(marker, {'experiment_type':'fixed_assembly_prompt_to_3d','case_order':cases,
        'model_profile':str(profile),
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'file_sha256':{str(p):file_hash(p) for p in paths}, 'created_at':time.time(),
        'input_sha256':{cid:file_hash(root/cid/'input.json') for cid in cases},
        'physical_checkers':selected,
        'geometry_timeout_seconds':120, 'render_timeout_seconds':300,
        'render':{'engine':'CYCLES','width':512,'height':512,'samples':32},
        'cross_experiment_token_accounting':False})


class RequestLogModel(Model):
    """Only per-call evidence and actual usage; no cumulative ledger or limits."""
    def __init__(self, model, workspace):
        self.model, self.workspace, self.stage = model, workspace, 'unset'
        self.count = 0

    async def get_response(self, *args, **kwargs):
        self.count += 1
        path = self.workspace/'api_calls'/f'{self.count:03d}.json'
        row = {'stage':self.stage, 'started_at':time.time(), 'status':'RUNNING',
            'system_instructions':kwargs.get('system_instructions'), 'input':kwargs.get('input'),
            'tools':[getattr(t,'name',type(t).__name__) for t in kwargs.get('tools',[])]}
        write_json(path,row)
        try:
            response = await self.model.get_response(*args, **kwargs)
            usage = response.usage
            row.update(status='COMPLETED', input_tokens=usage.input_tokens,
                       output_tokens=usage.output_tokens, total_tokens=usage.input_tokens+usage.output_tokens)
            return response
        except Exception as error:
            row.update(status='ERROR', error_type=type(error).__name__, reason=str(error)[:400], total_tokens=None)
            raise
        finally:
            row.update(finished_at=time.time(), elapsed_seconds=time.time()-row['started_at'])
            write_json(path,row)

    async def stream_response(self, *args, **kwargs):
        async for item in self.model.stream_response(*args, **kwargs):
            yield item


class PromptWorkflow(ObjectWorkflow):
    def _runtime(self, request, workspace, **kwargs):
        runtime = super()._runtime(request, workspace, **kwargs)
        self.runtime = runtime
        SESSION_ROOT.mkdir(parents=True,exist_ok=True)
        runtime.sessions = SessionManager(workspace, request.task_id,
            Path(tempfile.mkdtemp(prefix='session-',dir=SESSION_ROOT)))
        runtime.model = RequestLogModel(runtime.model, workspace)
        call = runtime.run

        async def recorded_run(**kw):
            stage = kw['stage']
            runtime.model.stage = stage
            path = workspace/'stage_inputs'/(stage.replace(':','_')+'.json')
            row = {'stage':stage, 'role':kw['role'], 'input':kw['input'],
                   'source_empty_before_call':not (workspace/'source.py').read_text().strip(),
                   'started_at':time.time()}
            if stage in {'plan','initial_code'} and (not row['source_empty_before_call'] or request.image_paths):
                raise ValueError('Prompt-only initial input violated: source not empty or unexpected reference images')
            write_json(path,row)
            try:
                return await call(**kw)
            finally:
                row.update(elapsed_seconds=time.time()-row['started_at'],
                           tools=[asdict(e) for e in getattr(kw.get('context'), 'events', [])])
                write_json(path,row)
        runtime.run = recorded_run
        return runtime


def snapshot_sessions(runtime, work):
    """Finish SQLite on the code disk before copying closed snapshot bytes.

    Archival failure is an artifact warning, not a model/checker failure. Keep
    the live database and local snapshot for recovery; never discard evidence.
    """
    if not runtime or not runtime.sessions.database_path.is_file():
        return {'status':'NOT_AVAILABLE'}
    source=runtime.sessions.database_path
    local=source.with_name('sessions_snapshot.sqlite3')
    destination=work/'sessions_snapshot.sqlite3'
    stage='local_sqlite_backup'
    try:
        with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as db:
            with closing(sqlite3.connect(local)) as out:
                db.backup(out)
                if out.execute('PRAGMA quick_check').fetchone()!=('ok',):
                    raise sqlite3.DatabaseError('snapshot quick_check failed')
        stage='snapshot_file_copy'
        temporary=destination.with_suffix('.sqlite3.copying')
        shutil.copyfile(local,temporary)
        temporary.replace(destination)
        return {'status':'SAVED','path':str(destination),'local_path':str(local)}
    except (sqlite3.Error,OSError) as error:
        return {'status':'ERROR','stage':stage,'type':type(error).__name__,
                'reason':str(error),'source_database':str(source),'local_path':str(local),
                'local_snapshot_available':stage=='snapshot_file_copy' and local.is_file()}


def input_audit(work, inputs):
    evidence = {}
    for stage in ('plan','initial_code'):
        path = work/'stage_inputs'/(stage+'.json')
        if not path.exists():
            evidence[stage] = {'verified':False,'reason':'stage not reached'}
            continue
        record = read_json(path)
        payload = json.loads(record['input'])
        expected = {'requirement','fixed_assembly'} if stage=='plan' else {
            'requirement','fixed_assembly','articulation_required','plan','assignment'}
        actual = [read_json(p) for p in sorted((work/'api_calls').glob('*.json'))
                  if read_json(p)['stage']==stage]
        first_input = actual[0]['input'] if actual else None
        expected_text = record['input']
        # SDK may normalize a string into an input_text message. Check actual
        # first model input, not merely that our staging payload was constructed.
        matched = first_input == expected_text
        if isinstance(first_input,list) and len(first_input)==1 and isinstance(first_input[0],dict):
            message = first_input[0]
            content = message.get('content')
            matched = message.get('role')=='user' and (content==expected_text or
                content==[{'type':'input_text','text':expected_text}])
        evidence[stage] = {'verified':bool(record['source_empty_before_call'] and set(payload)==expected and matched),
            'source_empty':record['source_empty_before_call'], 'payload_fields':list(payload),
            'actual_model_input_matches_stage_input':matched, 'api_call_count':len(actual),
            'frozen_config_unchanged':payload['fixed_assembly']==inputs['fixed_assembly'],
            'original_prompt_present':inputs['original_task']['prompt'] in payload['requirement'],
            'reference_images':0, 'tool_events':record.get('tools',[])}
    programs, artifact_errors = [], []
    single_part_sources = set()
    if not inputs['fixed_assembly'].get('require_multiple_parts', False):
        for manifest_path in (work/'rounds').rglob('assembly_manifest.json'):
            try:
                manifest = read_json(manifest_path)
                if not isinstance(manifest, dict):
                    raise ValueError('manifest must be an object')
            except (OSError, ValueError) as error:
                artifact_errors.append({'path':str(manifest_path),'stage':'assembly_api_audit',
                                        'reason':str(error)[:240]})
                continue
            if len(manifest.get('part_declarations', manifest.get('parts', []))) == 1 and manifest.get('connections') == []:
                single_part_sources.add(manifest.get('source_sha256'))
    paths = [work/'original/source.py', *sorted((work/'rounds').rglob('source.py'))]
    for path in paths:
        if not path.is_file():continue
        try:
            calls = [node.func for node in ast.walk(ast.parse(path.read_text())) if isinstance(node,ast.Call)]
            names = [f.id if isinstance(f,ast.Name) else f.attr if isinstance(f,ast.Attribute) else '' for f in calls]
            source_hash = file_hash(path)
            programs.append({'source':str(path),'sha256':source_hash,
                             'assembly_api_present':'FixedAssembly' in names and (
                                 source_hash in single_part_sources or all(n in names for n in ('TabSlot','connect')))})
        except SyntaxError:
            programs.append({'source':str(path),'sha256':file_hash(path),'assembly_api_present':False})
    result = {'experiment_type':'fixed_assembly_prompt_to_3d', 'stages':evidence,'programs':programs,
              'artifact_errors':artifact_errors,
              'input_verified':all(evidence[s].get('verified') and evidence[s].get('frozen_config_unchanged') and evidence[s].get('original_prompt_present') for s in ('plan','initial_code')),
              'assembly_api_verified':any(p['assembly_api_present'] for p in programs)}
    write_json(work.parent/'input_audit.json', result)
    return result


def shared_export_failures(failures):
    """Only controller-confirmed shared faults pause; an EXPORTED_* code does not."""
    confirmed = {'FROZEN_INPUT_CHANGED', 'ACTUAL_INPUT_CONTRACT_MISMATCH',
                 'RETAINED_VERSION_MISMATCH', 'CURRENT_REPORT_SOURCE_MISMATCH',
                 'SHARED_ENVIRONMENT_UNAVAILABLE'}
    return [f for f in failures if f.get('code') in confirmed]


def shared_input_failures(audit):
    # A stage not reached or missing audit evidence is not a proven bad input.
    bad = [stage for stage, row in audit.get('stages', {}).items()
        if any(row.get(key) is False for key in
               ('source_empty', 'frozen_config_unchanged', 'original_prompt_present'))
        or (row.get('payload_fields') is not None and set(row['payload_fields']) != (
            {'requirement','fixed_assembly'} if stage=='plan' else
            {'requirement','fixed_assembly','articulation_required','plan','assignment'}))
        or (row.get('api_call_count', 0) > 0 and
            row.get('actual_model_input_matches_stage_input') is False)]
    return [{'code':'ACTUAL_INPUT_CONTRACT_MISMATCH', 'stages':bad}] if bad else []


def current_geometry_failures(work, fallback=()):
    """Current checked working version only; keep history out of batch gates."""
    try:
        ledger = work/'assembly_versions.json'
        if ledger.is_file():
            book = read_json(ledger)
            version = book['versions'][book.get('working', book['retained'])]
            report = version.get('reviews', {}).get('geometry')
            if isinstance(report, dict):
                if report.get('source_sha256') and report['source_sha256'] != file_hash(Path(version['source'])):
                    return [{'code':'CURRENT_REPORT_SOURCE_MISMATCH', 'reason':'current assembly report/source mismatch'}]
                return report.get('failures', [])
            return []  # No current geometry; execution/tool failures are reported separately.
        reports = sorted((work/'rounds').rglob('assembly_manifest.json'))
        return read_json(reports[-1]).get('failures', []) if reports else list(fallback)
    except (OSError, ValueError, KeyError, TypeError) as error:
        return [{'code':'EXPORTED_REPORT_UNAVAILABLE', 'failure_kind':'export',
                 'stage':'read_current_report', 'reason':str(error)[:240]}]


def reclassify_saved_geometry_pause(folder):
    """Reclassify saved case errors without replay, approval or budget changes."""
    result_path, audit_path = folder/'result.json', folder/'input_audit.json'
    result = read_json(result_path)
    audit = read_json(audit_path) if audit_path.is_file() else {}
    work = folder/'generate'
    paths = sorted((work/'rounds').rglob('assembly_manifest.json'))
    calls = sorted((work/'api_calls').glob('*.json'))
    shared = (shared_export_failures(current_geometry_failures(work, result.get('geometry_failures', [])))
              + shared_export_failures(result.get('shared_failures', [])) + shared_input_failures(audit))
    blocked = bool(shared)
    gate = {'case': folder.name, 'original_pause_batch': result.get('pause_batch'),
        'pause_batch': blocked, 'case_approved': result.get('approved', False),
        'reason': ('No confirmed shared fault; continue independent cases, not approval of this case'
                   if not blocked else 'confirmed shared fault; keep batch paused'),
        'evidence_sha256': {str(path): file_hash(path)
                           for path in [result_path, audit_path, *paths, *calls] if path.is_file()},
        'shared_export_failures': shared,
        'case_replayed': False, 'checked_at': time.time()}
    write_json(folder/'continuation_gate.json', gate)
    return not blocked


async def run_case(root, cid, *, resume_from=None):
    folder = root/cid
    if (folder/'result.json').exists():return read_json(folder/'result.json')
    if (folder/'started.json').exists():raise RuntimeError('No automatic replay of a started case')
    inputs = read_json(folder/'input.json')
    if file_hash(folder/'input.json') != read_json(root/'batch.json')['input_sha256'][cid]:
        failure = {'code':'FROZEN_INPUT_CHANGED', 'stage':'preflight', 'reason':'Frozen task input changed'}
        summary = {'case':cid, 'approved':False, 'pause_batch':True, 'shared_failures':[failure]}
        write_json(folder/'result.json', summary)
        return summary
    # Old saved inputs used one repair; never expand an existing case's budget.
    max_rounds = inputs.get('source_repair_limit', 1) + 1
    requirement = '[ORIGINAL TASK]\n'+inputs['original_task']['prompt']+'\n\n[UNIFORM MANUFACTURING REQUIREMENTS]\n'+inputs['manufacturing_requirements']
    work = folder/'generate'
    if resume_from is not None:
        # A previously generated initial program interrupted during review.
        # Never replay Planner/Coder generation, discard history, or reset edits.
        previous=read_json(resume_from.parent/'result.json')
        assert previous['repair_attempts']==0 and previous['stop_reason']=='FLOW_ERROR'
        assert not (resume_from/'repair_history.jsonl').exists()
        assert file_hash(resume_from/'source.py')==file_hash(resume_from/'original/source.py')
        assert inputs==read_json(resume_from.parent/'input.json')
        work.mkdir(parents=True,exist_ok=False)
        for name in ('source.py','plan.json'):
            shutil.copy2(resume_from/name,work/name)
    workflow = PromptWorkflow(Path(read_json(root/'batch.json').get('model_profile',str(PROFILE))))
    specs=tuple(CheckerSpec.model_validate(s) for s in inputs.get('checker_specs',[]))
    request = ObjectRequest(requirement,work,f'prompt_assembly_{cid}',image_paths=(),
        articulation=False,max_rounds=max_rounds,checker_specs=specs,fixed_assembly=inputs['fixed_assembly'],
        repair_policy=RepairPolicy.model_validate(inputs.get('repair_policy',{})))
    start = time.time()
    write_json(folder/'started.json',{'time':start,'route':'ObjectWorkflow.resume' if resume_from else 'ObjectWorkflow.generate',
        'source_preloaded':bool(resume_from),'resume_from':str(resume_from) if resume_from else None})
    print(f'{cid}: native {"resume initial review (no regeneration)" if resume_from else "generate, fresh source, no reference images"}; max_rounds={max_rounds}, max_repairs={max_rounds-1}',flush=True)
    result, error = None, None
    try:
        result = await workflow.resume(request) if resume_from else await workflow.generate(request)
    except Exception as exc:
        error = {'type':type(exc).__name__,'reason':str(exc)[:600]}
        (folder/'error.log').write_text(traceback.format_exc())
    if resume_from:
        audit=read_json(resume_from.parent/'input_audit.json')
        audit={**audit,'original_generation_audit':str(resume_from.parent/'input_audit.json'),
               'continuation_initial_source_sha256':file_hash(work/'original/source.py') if (work/'original/source.py').exists() else file_hash(work/'source.py'),
               'new_initial_generation':False}
        write_json(folder/'input_audit.json',audit)
    else:
        audit = input_audit(work,inputs)
    book_path = work/'assembly_versions.json'
    book = read_json(book_path) if book_path.exists() else {}
    publication_failures = []
    if book:
        try:
            selected = book['versions'][book['retained']]
            assert_version(selected)
            if file_hash(work/'source.py') != file_hash(Path(selected['source'])):
                raise ValueError('Retained/source mismatch')
        except (OSError, ValueError, KeyError, TypeError) as exc:
            confirmed = isinstance(exc, ValueError) and (str(exc) in
                ('Retained/source mismatch', 'version checker/review record changed')
                or str(exc).startswith('version asset hash changed:'))
            publication_failures.append({'code':'RETAINED_VERSION_MISMATCH' if confirmed else 'EXPORTED_REPORT_UNAVAILABLE',
                'stage':'verify_publication', 'reason':str(exc)[:240]})
    reports = []
    for path in sorted((work/'rounds').rglob('assembly_manifest.json')):
        try:
            report = read_json(path)
            if not isinstance(report, dict) or not isinstance(report.get('failures', []), list):
                raise ValueError('assembly report must contain a failures list')
            reports.append(report)
        except (OSError, ValueError) as exc:
            reports.append({'failures':[{'code':'EXPORTED_REPORT_UNAVAILABLE',
                'report_path':str(path), 'reason':str(exc)[:240]}]})
    failures = [f for report in reports for f in report.get('failures',[])]
    current_failures = current_geometry_failures(work, failures)
    shared = shared_export_failures(current_failures + publication_failures) + shared_input_failures(audit)
    tool_errors = [read_json(p) for p in work.rglob('*edit_outcome.json') if read_json(p).get('status')=='TOOL_ERROR']
    flow_errors = [str(p) for p in work.rglob('flow_error.json')]
    # Do not inherit old candidate faults after a later version is evaluated.
    if book.get('stop_reason') == 'FLOW_ERROR' and flow_errors:
        shared += shared_export_failures([read_json(Path(sorted(flow_errors)[-1]))])
    calls = [read_json(p) for p in sorted((work/'api_calls').glob('*.json'))]
    repair_log = work/'repair_history.jsonl'
    repairs = len(repair_log.read_text().splitlines()) if repair_log.exists() else 0
    pause = bool(shared)
    summary = {'case':cid,'experiment_type':'fixed_assembly_prompt_to_3d',
        'approved':bool(result and result.approved and not error and not publication_failures),
        'pause_batch':pause, 'shared_failures':shared, 'publication_failures':publication_failures,
        'initial_generations':int((work/'stage_inputs/initial_code.json').exists()),'repair_attempts':repairs,
        'max_rounds':max_rounds,'source_repair_limit':max_rounds-1,
        'source':str(work/'source.py'),'retained_version':book.get('retained'),
        'stop_reason':book.get('stop_reason'), 'elapsed_seconds':time.time()-start,
        'geometry_failures':failures,'current_geometry_failures':current_failures,
        'tool_errors':tool_errors,'flow_errors':flow_errors,'exception':error,
        'actual_api_requests':len(calls),'known_actual_tokens':sum(c.get('total_tokens') or 0 for c in calls),
        'unknown_usage_requests':sum(c.get('total_tokens') is None for c in calls),
        'physical_checkers':[s.name for s in specs] if specs else 'NOT_EXECUTED'}
    runtime = getattr(workflow,'runtime',None)
    # Save the real outcome before optional history archival. A copy failure
    # must not hide an earlier API failure or prevent the next independent case.
    write_json(folder/'result.json',summary)
    summary['session_snapshot']=snapshot_sessions(runtime,work)
    if summary['session_snapshot']['status']=='ERROR':
        print(f'{cid}: session snapshot unavailable; details saved in result.json',flush=True)
    write_json(folder/'result.json',summary)
    print(f'{cid}: approved={summary["approved"]} pause={pause} repairs={repairs}/{max_rounds-1} tokens={summary["known_actual_tokens"]}',flush=True)
    return summary


async def main(root, cases, max_rounds=5, *, assembly_topology=False, profile=PROFILE, tools=(), physics=None, sizes=None):
    prepare(root, max_rounds=max_rounds, assembly_topology=assembly_topology, profile=profile,
            cases=cases if assembly_topology or tools else IDS, tools=tools,physics=physics,sizes=sizes)
    if any(c!=IDS[0] for c in cases):
        first = root/IDS[0]
        if ((first/'result.json').exists() and read_json(first/'result.json')['pause_batch']
                and not reclassify_saved_geometry_pause(first)):
            raise ValueError('SF07 saved evidence still requires a batch pause')
    for cid in cases:
        result = await run_case(root,cid)
        if result['pause_batch']:
            write_json(root/'paused.json',{'case':cid,'reason':'confirmed shared fault',
                                          'failures':result.get('shared_failures',[])})
            return 2
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',choices=IDS,default=[IDS[0]])
    parser.add_argument('--assembly-topology',action='store_true')
    parser.add_argument('--tools',nargs='+',choices=['assembly_topology','assembly_standing','assembly_overhang','assembly_fea'],default=[])
    parser.add_argument('--physics-config',type=Path)
    parser.add_argument('--final-size-mm',nargs=3,type=float)
    parser.add_argument('--llm-config',type=Path,default=PROFILE)
    parser.add_argument('--max-rounds',type=int,choices=range(1,6),default=5,
                        help='New batches only: total review rounds including initial evaluation (default 5, at most 4 repairs); existing frozen limits remain unchanged')
    args=parser.parse_args()
    raise SystemExit(asyncio.run(main(args.root.resolve(),args.cases,args.max_rounds,
        assembly_topology=args.assembly_topology,profile=args.llm_config.resolve(),tools=args.tools,
        physics=read_json(args.physics_config) if args.physics_config else None,
        sizes={c:args.final_size_mm for c in args.cases} if args.final_size_mm else None)))
