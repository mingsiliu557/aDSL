"""Focused offline checks for frozen native comparison orchestration."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from experiments.benchmark_six import run


def case(tmp_path, cid='Toys4K_robot_050'):
    image = tmp_path / 'reference.png'
    image.write_bytes(b'exact-reference-bytes')
    return dict(case_id=cid, requirement='Static reference object, one scene unit is one millimetre.',
                input_image=str(image), final_size_mm=[75., 50., 150.], voxel_pitch_mm=5.,
                standing_applicable=cid != 'Toys4K_dragon_007', input_sha256=run.sha256(image))


def test_both_requests_use_same_frozen_image_and_initial_plus_nine_budget(tmp_path):
    row = case(tmp_path)
    official = run.request_fields(row, 'official', {})
    ours = run.request_fields(row, 'ours', {'fea': {'enabled': True}})
    assert official['image_paths'] == ours['image_paths'] == [row['input_image']]
    assert official['max_rounds'] == ours['max_rounds'] == 10
    assert ours['requirement'].startswith(official['requirement'])
    assert 'fixed_assembly' not in official and 'checker_names' not in official
    fixed = ours['fixed_assembly']
    assert fixed['mm_per_unit'] == 1 and fixed['fit_offset_mm'] == .2
    assert fixed['validation_mode'] == 'visual_only' and fixed['final_size_mm'] == row['final_size_mm']
    assert 'fea' not in fixed['physics'] and 'assembly_fea' not in ours['checker_names']
    assert fixed['physics']['overhang']['partition_objective'] == run.PARTITION_OBJECTIVE
    assert fixed['physics']['overhang']['partition_editable']
    assert ours['repair_policy']['print_partition_editable']
    assert ours['repair_policy']['max_total_candidates'] == 9


def test_dragon_never_enables_standing(tmp_path):
    row = case(tmp_path, 'Toys4K_dragon_007')
    fields = run.request_fields(row, 'ours', {})
    assert fields['checker_names'] == ['assembly_topology', 'assembly_overhang']


def test_cases_whitelist_excludes_ground_truth_and_risk_labels(tmp_path):
    rows = []
    for cid in run.CASE_ORDER:
        row = case(tmp_path, cid)
        row.update(risk_label='DO_NOT_INPUT', gt_mesh='/DO_NOT_INPUT.glb', ordinary_control=True)
        rows.append(row)
    run.write_json(tmp_path / 'config/cases.json', {'cases': rows})
    loaded = run.load_cases(tmp_path)
    assert tuple(loaded) == run.CASE_ORDER
    assert 'DO_NOT_INPUT' not in str(loaded)
    changed = rows[0]['input_image']
    Path(changed).write_bytes(b'changed-input')
    with pytest.raises(ValueError, match='reference image changed'):
        run.load_cases(tmp_path)


def test_worker_and_parent_reject_official_modules_from_ours(tmp_path, monkeypatch):
    official = tmp_path / 'official'
    ours = tmp_path / 'ours'
    official.mkdir()
    ours.mkdir()
    wrong = ours / 'service.py'
    wrong.write_text('sentinel')
    monkeypatch.setattr(run.importlib, 'import_module', lambda name: SimpleNamespace(__file__=str(wrong)))
    with pytest.raises(ValueError, match='module origin mismatch'):
        run.verify_module_origins({'code_root': str(official)})


def test_venv_python_symlink_is_not_resolved_to_base_interpreter(tmp_path):
    base = tmp_path / 'basepython'
    base.write_text('not run')
    arms = {}
    for arm in run.ARMS:
        directory = tmp_path / arm
        directory.mkdir()
        python = directory / 'python'
        python.symlink_to(base)
        profile = directory / 'profile.yaml'
        profile.write_text('not loaded')
        arms[arm] = dict(python=str(python), profile=str(profile), code_root=str(directory))
    run.write_json(tmp_path / 'config/envs.json', {'arms': arms})
    loaded, _ = run.load_envs(tmp_path)
    assert loaded['official']['python'] == arms['official']['python']
    assert loaded['ours']['python'] == arms['ours']['python']


def test_environment_drops_proxies_and_gpu_queue(tmp_path, monkeypatch):
    monkeypatch.setenv('HTTPS_PROXY', 'do-not-use')
    monkeypatch.setenv('ADSL_GPU_RENDER_QUEUE', 'do-not-use')
    monkeypatch.setenv('PYTHONPATH', '/other-arm-code')
    env = run.clean_environment({}, {'http_proxy': 'do-not-use', 'ADSL_GPU_RENDER_QUEUE': 'do-not-use'})
    assert not any(k.lower().endswith('_proxy') for k in env)
    assert 'ADSL_GPU_RENDER_QUEUE' not in env and 'PYTHONPATH' not in env
    assert env['ADSL_RENDER_ENGINE'] == 'BLENDER_EEVEE'
    assert env['LIBGL_ALWAYS_SOFTWARE'] == '1' and env['GALLIUM_DRIVER'] == 'llvmpipe'


def test_actual_role_call_adapter_allows_one_initial_and_nine_source_repairs(tmp_path, monkeypatch):
    class Sessions:
        def __init__(self, workspace, task_id):
            self.workspace = workspace
            self.task_id = task_id
    monkeypatch.setattr(run.importlib, 'import_module', lambda name: SimpleNamespace(SessionManager=Sessions))
    calls = []
    async def native_call(**kwargs):
        calls.append(kwargs['stage'])
        return SimpleNamespace(final_output={'accepted': False}, new_items=[])
    runtime = SimpleNamespace(run=native_call)
    run.audited_runtime(runtime, root=tmp_path, case_id='fixture', arm='official')
    agent = SimpleNamespace(name='native-coder', instructions='unchanged native prompt', tools=[])
    async def bounded_calls():
        await runtime.run(agent=agent, input='reference+requirement', role='coder', stage='initial_code')
        for number in range(1, 10):
            await runtime.run(agent=agent, input='native feedback', role='coder', stage=f'critic_patch:{number}')
        with pytest.raises(ValueError, match='budget exceeded'):
            await runtime.run(agent=agent, input='extra', role='coder', stage='critic_patch:10')
    asyncio.run(bounded_calls())
    assert len(calls) == 10 and calls[0] == 'initial_code'
    records = list((tmp_path / 'jobs/fixture/official/generation/role_calls').glob('*.json'))
    assert len(records) == 10
    assert run.read_json(records[0])['instructions'] == 'unchanged native prompt'
    assert runtime.sessions.workspace.is_relative_to('/tmp')


def test_native_selection_never_reads_better_offline_score(tmp_path):
    chosen = tmp_path / 'selected.py'
    chosen.write_text('native retained source')
    glb = tmp_path / 'selected.glb'
    glb.write_bytes(b'native')
    better = tmp_path / 'rejected.glb'
    better.write_bytes(b'rejected candidate')
    run.write_json(tmp_path / 'evaluation/result.json', {'best_candidate': str(better), 'score': 1000})
    native = SimpleNamespace(source_path=chosen, glb_path=glb, render_paths=(), approved=False,
                             selected_round=4, usage={'total_tokens': 123})
    job = run.generation_result({'arm': 'official', 'generation': {}}, native, tmp_path)
    assert job['selected_glb'] == str(glb) and job['source'] == str(chosen)
    assert job['approved_round'] == 4 and job['generation']['status'] == 'COMPLETED'


def test_selected_artifact_hashes_require_exact_bytes(tmp_path):
    source = tmp_path / 'source.py'
    source.write_text('selected source')
    hashes = run.artifact_hashes({'source': str(source)})
    assert run.hashes_match(hashes)
    source.write_text('changed source')
    assert not run.hashes_match(hashes)


def test_existing_batch_budget_and_started_job_are_never_reset(tmp_path, monkeypatch):
    cases = {cid: case(tmp_path, cid) for cid in run.CASE_ORDER}
    arms = {arm: {'code_root': str(tmp_path / arm), 'profile': str(tmp_path / (arm + '.yaml'))} for arm in run.ARMS}
    for arm in run.ARMS:
        Path(arms[arm]['profile']).write_text('frozen profile')
    fingerprint = {'sources': {'official': 'a', 'ours': 'b'}, 'budget': {'source_repair_limit': 9}}
    monkeypatch.setattr(run, 'frozen_fingerprint', lambda *args: fingerprint)
    run.initialize(tmp_path, cases, arms, {})
    path = tmp_path / 'jobs' / run.CASE_ORDER[0] / 'official/job.json'
    job = run.read_json(path)
    job['generation'].update(status='RUNNING', actual_source_repairs=3)
    run.write_json(path, job)
    before = path.read_bytes()
    run.initialize(tmp_path, cases, arms, {})
    assert path.read_bytes() == before
    fingerprint['sources']['official'] = 'changed-source'
    with pytest.raises(ValueError, match='refuse budget reset'):
        run.initialize(tmp_path, cases, arms, {})


def test_api_fault_pauses_but_single_design_execution_error_does_not():
    class APIConnectionError(Exception):
        pass
    class AssetExecutionError(Exception):
        pass
    assert run.shared_fault(APIConnectionError('unreachable'))
    assert not run.shared_fault(AssetExecutionError('generated source failed export'))


def test_failed_unified_views_do_not_fall_back_to_generation_images(tmp_path, monkeypatch):
    folder = tmp_path / 'jobs' / run.CASE_ORDER[0] / 'official'
    run.write_json(folder / 'evaluation/image_critic_request.json', {'status': 'INDETERMINATE', 'rendered_images': []})
    monkeypatch.setattr(run, 'probe', lambda *args: None)
    assert asyncio.run(run.review_worker(tmp_path, run.CASE_ORDER[0], 'official', {}, {})) == 0
    saved = run.read_json(folder / 'review/image_critic.json')
    assert saved['status'] == 'INDETERMINATE' and saved['no_candidate_selection']


def test_serial_twelve_jobs_and_completed_resume_make_no_new_generation(tmp_path, monkeypatch):
    cases = {cid: case(tmp_path, cid) for cid in run.CASE_ORDER}
    arms = {}
    for arm in run.ARMS:
        directory = tmp_path / arm
        directory.mkdir()
        profile = directory / 'profile.yaml'
        profile.write_text('frozen profile')
        arms[arm] = dict(code_root=str(directory), profile=str(profile), python='not executed')
    monkeypatch.setattr(run, 'load_cases', lambda root: cases)
    monkeypatch.setattr(run, 'load_envs', lambda root: (arms, {}))
    monkeypatch.setattr(run, 'frozen_fingerprint', lambda *args: {'unchanged': True})
    run.write_json(tmp_path / 'config/physics.json', {})
    generations = []
    def fake_child(root, configured, common, arm, arguments, log):
        if '--probe' in arguments:
            run.write_json(root / 'preflight' / f'{arm}_imports.json',
                dict(code_root=arms[arm]['code_root'], modules={'native': {'path': str(Path(arms[arm]['code_root']) / 'service.py')}}))
            return 0
        cid = arguments[arguments.index('--case') + 1]
        method = arguments[arguments.index('--arm') + 1]
        folder = root / 'jobs' / cid / method
        if '--worker' in arguments:
            generations.append((cid, method))
            job = run.read_json(folder / 'job.json')
            job['generation']['status'] = 'generation_failed'
            run.write_json(folder / 'job.json', job)
        else:
            run.write_json(folder / 'review/image_critic.json', {'status': 'INDETERMINATE'})
        return 0
    def fake_evaluation(root, cid, arm, *args):
        folder = root / 'jobs' / cid / arm / 'evaluation'
        run.write_json(folder / 'result.json', {'status': 'FAIL', 'failure_stage': 'generation', 'appearance': {'status': 'FAIL'}})
        run.write_json(folder / 'image_critic_request.json', {'status': 'INDETERMINATE'})
        return 0
    class Connected:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return None
    monkeypatch.setattr(run, 'child', fake_child)
    monkeypatch.setattr(run, 'evaluate_child', fake_evaluation)
    monkeypatch.setattr(run.socket, 'create_connection', lambda *a, **k: Connected())
    assert run.batch(tmp_path, preflight_only=True) == 0
    assert not (tmp_path / 'batch.json').exists() and not generations
    assert run.batch(tmp_path) == 0
    assert generations == [(cid, arm) for cid in run.CASE_ORDER for arm in run.ARMS]
    assert run.read_json(tmp_path / 'results.json')['status'] == 'COMPLETED'
    assert run.batch(tmp_path) == 0
    assert len(generations) == 12


def test_evaluation_timeout_retains_partial_numbers_and_case_denominator(tmp_path, monkeypatch):
    folder = tmp_path / 'jobs' / run.CASE_ORDER[0] / 'ours'
    folder.mkdir(parents=True)
    result = folder / 'evaluation/result.json'
    run.write_json(result, {'status': 'INDETERMINATE', 'included_in_denominator': True, 'metrics': {'N': 4, 'G': None}})
    class TimedOut:
        pid = 12345
        def __init__(self, *args, **kwargs):
            self.calls = 0
        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                assert timeout == 3600
                raise run.subprocess.TimeoutExpired('offline evaluation', timeout)
            return 0
    killed = []
    monkeypatch.setattr(run.subprocess, 'Popen', TimedOut)
    monkeypatch.setattr(run.os, 'killpg', lambda pid, sig: killed.append((pid, sig)))
    arms = {'ours': {'python': 'not executed', 'code_root': str(tmp_path)}}
    assert run.evaluate_child(tmp_path, run.CASE_ORDER[0], 'ours', arms, {}) == 0
    saved = run.read_json(result)
    assert saved['included_in_denominator'] and saved['metrics']['N'] == 4 and saved['metrics']['G'] is None
    assert saved['failure_stage'] == 'evaluation_timeout'
    assert killed == [(12345, run.signal.SIGTERM)]


def test_retained_native_partition_binding_excludes_rejected_working_report(tmp_path):
    work = tmp_path / 'native'
    source = work / 'source.py'
    source.parent.mkdir()
    source.write_text('native retained source')
    glb = work / 'selected.glb'
    glb.write_bytes(b'native selected GLB')
    manifest = work / 'assembly/assembly_manifest.json'
    run.write_json(manifest, {'source_sha256': run.sha256(source)})
    retained = work / 'retained_report.json'
    run.write_json(retained, {'assumptions': {'source_sha256': run.sha256(source), 'manifest_sha256': run.sha256(manifest)}})
    rejected = work / 'rejected_report.json'
    run.write_json(rejected, {'score': 999999})
    run.write_json(work / 'assembly_versions.json', {'retained': 'original', 'working': 'attempt_0009'})
    run.write_json(work / 'assembly_result.json', {'version_id': 'original', 'source_sha256': run.sha256(source),
        'reviews': {'assembly_overhang': {'artifacts': {'report': str(retained)}}}})
    native = SimpleNamespace(source_path=source, glb_path=glb, render_paths=(), approved=True,
                             selected_round=1, usage={'total_tokens': 12})
    job = run.generation_result({'arm': 'ours', 'generation': {}}, native, work)
    assert job['native_partition_result'] == str(retained)
    assert job['native_partition_binding']['report_sha256'] == run.sha256(retained)
    assert str(rejected) not in str(job)
    run.write_json(retained, {'assumptions': {'source_sha256': 'wrong', 'manifest_sha256': run.sha256(manifest)}})
    with pytest.raises(ValueError, match='binding mismatch'):
        run.generation_result({'arm': 'ours', 'generation': {}}, native, work)


def test_checkpoint_builds_startup_table_and_separate_request_usage(tmp_path):
    rows = [case(tmp_path, cid) for cid in run.CASE_ORDER]
    run.write_json(tmp_path / 'config/cases.json', {'cases': rows})
    folder = tmp_path / 'jobs' / run.CASE_ORDER[0] / 'official'
    run.write_json(folder / 'job.json', {'generation': {'status': 'PENDING'}, 'usage': {'requests': 3, 'total_tokens': 31}})
    run.write_json(folder / 'review/image_critic.json', {'usage': {'requests': 1, 'total_tokens': 7}})
    run.checkpoint(tmp_path, 'RUNNING')
    summary = run.read_json(tmp_path / 'review/summary.json')
    assert summary['status'] == 'RUNNING' and len(summary['rows']) == 12
    assert summary['rows'][0]['generation_requests'] == 3
    assert summary['rows'][0]['final_review_requests'] == 1
    assert 'Final review requests' in (tmp_path / 'review/README.md').read_text()
    # A missing config on a PAUSED error path still preserves the checkpoint.
    run.checkpoint(tmp_path / 'partial', 'PAUSED', reason={'message': 'preflight failed'})
    assert run.read_json(tmp_path / 'partial/checkpoint.json')['status'] == 'PAUSED'
