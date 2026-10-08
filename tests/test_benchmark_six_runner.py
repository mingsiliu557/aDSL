"""Focused offline checks for frozen native comparison orchestration."""
import asyncio
from dataclasses import dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest
import httpx
import openai
from agents import Agent, Model, ModelBehaviorError, RunConfig, Runner
from openai.types.responses import (Response, ResponseCompletedEvent, ResponseErrorEvent,
                                    ResponseIncompleteEvent, ResponseTextDeltaEvent)
from pydantic import BaseModel

from experiments.benchmark_six import run


@pytest.mark.parametrize('status,code,message,expected', [
    (429, None, 'You exceeded your current quota, please check your plan and billing details', True),
    (429, 'insufficient_quota', 'request rejected', True),
    (429, 'rate_limit_exceeded', 'Too many requests', False),
    (400, 'context_too_large', 'input too large', False),
])
def test_batch_shared_fault_uses_request_classification(status, code, message, expected):
    class Error(Exception):
        status_code = status
        body = {'error': {'code':code, 'message':message}}
    error = Error(message)
    assert run.shared_fault(error) is expected
    assert (run.request_error_helpers().classify_model_request_error(error)['kind'] == 'shared_fault') is expected
    assert not run.shared_fault(TypeError('program defect'))
    assert not run.shared_fault(KeyError('program defect'))
    assert run.shared_fault(OSError('no space left on device'))


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


@pytest.mark.parametrize('arm', run.ARMS)
def test_probe_rejects_non_sol_61_model_before_native_request(tmp_path, monkeypatch, arm):
    profile = tmp_path / f'{arm}.yaml'
    profile.write_text('params:\n  base_url: http://127.0.0.1:28317/v1\n  model: gpt-6-sol\n')
    monkeypatch.setattr(run, 'verify_module_origins', lambda env: {})
    with pytest.raises(ValueError, match=f'{arm} profile model mismatch: expected gpt-6.1-sol'):
        run.probe(tmp_path, arm, {arm: {'profile': str(profile)}})
    assert not (tmp_path / 'preflight').exists()


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


def test_frozen_request_classifier_loads_without_native_package(tmp_path, monkeypatch):
    import shutil
    helpers = run.request_error_helpers()
    directory = tmp_path / 'runner'
    directory.mkdir()
    shutil.copy2(helpers.__file__, directory / 'request_errors.py')
    monkeypatch.setattr(run, 'HERE', directory / 'run.py')
    monkeypatch.setattr(run, '_REQUEST_ERRORS', None)
    # The official environment does not import/install the new agents module.
    monkeypatch.setattr(run.importlib, 'import_module', lambda name: pytest.fail(name))
    assert run.request_error_helpers().classify_model_request_error(TypeError('bug')) is None
    (directory / 'request_errors.py').unlink()
    monkeypatch.setattr(run, '_REQUEST_ERRORS', None)
    with pytest.raises(ValueError, match='snapshot missing'):
        run.request_error_helpers()


def test_role_observation_counts_text_and_images_separately():
    import json
    text = json.dumps({'evaluation_feedback': {'unique_failure_count': 1, 'failures': []}}, ensure_ascii=False)
    value = [{'role':'user', 'content':[{'type':'input_text', 'text':text},
        *[{'type':'input_image', 'image_url':'data:image/png;base64,' + 'a' * 100000} for _ in range(8)]]}]
    observed = run.input_observation(value)
    assert observed['text_utf8_bytes'] == len(text.encode())
    assert observed['image_count'] == 8 and observed['unique_failure_count'] == 1
    assert observed['evaluation_feedback_bytes'] < 100


@pytest.mark.parametrize('marker', ['checkpoint.json', 'batch.json'])
def test_new_frozen_runner_copies_classifier_but_resume_never_invents_it(tmp_path, monkeypatch, marker):
    import shutil
    origin = Path(run.request_error_helpers().__file__)
    runner = tmp_path/'runner'; runner.mkdir()
    for name in ('run.py', 'evaluate.py', 'build_review.py'):
        (runner/name).write_text('# frozen offline runner\n')
    (tmp_path/'config').mkdir()
    for name in ('cases.json', 'envs.json', 'physics.json'):
        (tmp_path/'config'/name).write_text('{}')
    arms = {}
    for arm in run.ARMS:
        code = tmp_path/arm
        (code/'adsl-agents/utils').mkdir(parents=True)
        (code/'adsl-agents/fake.py').write_text('x = 1')
        profile = code/'profile.yaml'; profile.write_text('offline profile')
        arms[arm] = {'code_root':str(code), 'profile':str(profile)}
    shutil.copy2(origin, tmp_path/'ours/adsl-agents/utils/request_errors.py')
    monkeypatch.setattr(run, 'HERE', runner/'run.py')
    monkeypatch.setattr(run, '_REQUEST_ERRORS', None)
    fingerprint = run.frozen_fingerprint(tmp_path, {}, arms)
    assert fingerprint['request_error_classifier'] == run.sha256(origin)
    assert (runner/'request_errors.py').read_bytes() == origin.read_bytes()
    (tmp_path/marker).write_text('{"completed":true}')
    (runner/'request_errors.py').unlink()
    monkeypatch.setattr(run, '_REQUEST_ERRORS', None)
    with pytest.raises(ValueError, match='snapshot missing'):
        run.frozen_fingerprint(tmp_path, {}, arms)
    assert not (runner/'request_errors.py').exists()


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


class OfflinePlannerOutput(BaseModel):
    description: str


class OfflinePlannerModel(Model):
    """Exercise the real SDK stream parser without a client or API connection."""
    def __init__(self, events):
        self.events = events
        self.calls = []

    async def get_response(self, *args, **kwargs):
        raise AssertionError('Planner must use the SDK streaming entry point')

    async def stream_response(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        for event in self.events:
            yield event


def planner_response(*, status='completed'):
    return Response(
        id='offline-planner-response', created_at=0, model='gpt-6.1-sol', object='response',
        status=status, parallel_tool_calls=False, tool_choice='auto', tools=[],
        incomplete_details=dict(reason='max_output_tokens') if status == 'incomplete' else None,
        output=[dict(id='offline-planner-message', type='message', role='assistant', status=status,
                     content=[dict(type='output_text', text='{"description":"native typed plan"}',
                                   annotations=[])])],
        usage=dict(input_tokens=8, output_tokens=4, total_tokens=12,
                   input_tokens_details=dict(cached_tokens=0, cache_write_tokens=0),
                   output_tokens_details=dict(reasoning_tokens=1)),
    )


def planner_completed():
    return ResponseCompletedEvent(type='response.completed', sequence_number=1,
                                  response=planner_response())


def offline_planner_runtime(tmp_path, monkeypatch, events, *, case_id='planner_fixture'):
    from adsl.agents.utils.usage import UsageRecorder

    native_calls = []
    native_result = SimpleNamespace(final_output='native result', new_items=[])
    async def native_run(**kwargs):
        native_calls.append(kwargs)
        return native_result

    runtime = SimpleNamespace(run=native_run, usage=UsageRecorder(tmp_path / case_id / 'native'))
    run.audited_runtime(runtime, root=tmp_path, case_id=case_id, arm='official')
    model = OfflinePlannerModel(events)
    agent = Agent(name='native-planner', instructions='native planner instructions', model=model,
                  output_type=OfflinePlannerOutput)
    return runtime, agent, model, native_calls, native_result


@pytest.fixture
def real_planner_stream(monkeypatch):
    """Observe public SDK arguments, then run its real offline model boundary."""
    native_streamed = Runner.run_streamed
    calls = []
    def observed(*args, **kwargs):
        calls.append((args, kwargs.copy()))
        return native_streamed(*args, **kwargs, run_config=RunConfig(tracing_disabled=True))
    monkeypatch.setattr(Runner, 'run_streamed', observed)
    return calls


def test_planner_stream_returns_sdk_typed_output_and_records_native_usage(tmp_path, monkeypatch,
                                                                        real_planner_stream):
    runtime, agent, model, native_calls, _ = offline_planner_runtime(
        tmp_path, monkeypatch, [planner_completed()])
    context = SimpleNamespace(events=[])
    async def invoke():
        result = await runtime.run(agent=agent, input='frozen planner input', role='planner',
                                   stage='planner', context=context)
        history = await runtime.sessions.for_role('planner').get_items()
        return result, history
    result, history = asyncio.run(invoke())
    assert isinstance(result.final_output, OfflinePlannerOutput)
    assert result.final_output.description == 'native typed plan'
    assert result.context_wrapper.context is context and not native_calls
    assert len(model.calls) == len(real_planner_stream) == 1
    args, kwargs = real_planner_stream[0]
    assert args == (agent, 'frozen planner input')
    assert kwargs['context'] is context and kwargs['max_turns'] == 16
    assert kwargs['session'].session_id.endswith(':planner')
    assert history[0]['content'] == 'frozen planner input'
    usage = runtime.usage.events()
    assert len(usage) == 1 and usage[0].stage == 'planner' and usage[0].agent == agent.name
    assert usage[0].requests == 1 and usage[0].total_tokens == 12 and usage[0].reasoning_tokens == 1
    row = run.read_json(next((tmp_path / 'jobs/planner_fixture/official/generation/role_calls').glob('*.json')))
    assert row['status'] == 'COMPLETED' and row['transport'] == 'Runner.run_streamed'
    assert row['response_completed'] and row['first_event_elapsed_seconds'] >= 0
    assert row['output'] == {'description': 'native typed plan'}


def test_planner_partial_json_without_completion_is_isolated_and_has_unknown_usage(
        tmp_path, monkeypatch, real_planner_stream):
    partial = ResponseTextDeltaEvent(type='response.output_text.delta', sequence_number=0,
        content_index=0, output_index=0, item_id='partial-plan', logprobs=[],
        delta='{"description":"unconfirmed plan"}')
    runtime, agent, _, native_calls, _ = offline_planner_runtime(tmp_path, monkeypatch, [partial])
    with pytest.raises(run.PlannerStreamInterrupted) as caught:
        asyncio.run(runtime.run(agent=agent, input='frozen input', role='planner', stage='planner'))
    assert caught.value.code == 'stream_missing_completion'
    assert run.api_interruption(caught.value) and not run.shared_fault(caught.value)
    assert not native_calls and not runtime.usage.events() and not runtime.six_shared_faults
    assert runtime.six_api_interruptions[0]['code'] == 'stream_missing_completion'
    row = run.read_json(next((tmp_path / 'jobs/planner_fixture/official/generation/role_calls').glob('*.json')))
    assert row['status'] == 'ERROR' and not row['response_completed']
    assert 'output' not in row and 'unconfirmed plan' not in str(row)
    next_runtime, next_agent, _, _, _ = offline_planner_runtime(
        tmp_path, monkeypatch, [planner_completed()], case_id='next_planner_fixture')
    result = asyncio.run(next_runtime.run(agent=next_agent, input='next frozen input', role='planner',
                                         stage='planner'))
    assert isinstance(result.final_output, OfflinePlannerOutput)
    assert len(next_runtime.usage.events()) == 1 and not next_runtime.six_api_interruptions
    assert runtime.sessions.database_path != next_runtime.sessions.database_path


@pytest.mark.parametrize('code,status,shared', [('request_timeout', None, False),
    ('request_timeout', 408, False), ('invalid_api_key', None, True),
    ('authentication_error', None, True), ('permission_denied', None, True),
    ('insufficient_quota', None, True)])
def test_planner_stream_error_classification_uses_provider_code(tmp_path, monkeypatch,
                                                               real_planner_stream, code, status, shared):
    error = ResponseErrorEvent(type='error', sequence_number=0, code=code,
                               message='offline provider failure',
                               **({'status_code': status} if status is not None else {}))
    runtime, agent, _, native_calls, _ = offline_planner_runtime(tmp_path, monkeypatch, [error])
    with pytest.raises(run.PlannerStreamInterrupted) as caught:
        asyncio.run(runtime.run(agent=agent, input='frozen input', role='planner', stage='planner'))
    assert caught.value.code == code and caught.value.status_code == status
    assert run.shared_fault(caught.value) is shared
    assert run.api_interruption(caught.value) is not shared
    assert not native_calls and not runtime.usage.events()
    evidence = runtime.six_shared_faults if shared else runtime.six_api_interruptions
    assert len(evidence) == 1 and evidence[0]['code'] == code


def test_planner_incomplete_keeps_sdk_model_behavior_failure(tmp_path, monkeypatch, real_planner_stream):
    response = planner_response(status='incomplete')
    event = ResponseIncompleteEvent(type='response.incomplete', sequence_number=0, response=response)
    runtime, agent, _, _, _ = offline_planner_runtime(tmp_path, monkeypatch, [event])
    with pytest.raises(ModelBehaviorError):
        asyncio.run(runtime.run(agent=agent, input='frozen input', role='planner', stage='planner'))
    assert not runtime.six_api_interruptions and not runtime.six_shared_faults
    assert not runtime.usage.events()


def test_cancelled_planner_stream_closes_provider_and_settles_sdk_task(tmp_path, monkeypatch,
                                                                    real_planner_stream):
    streamed_results = []
    native_streamed = Runner.run_streamed
    def capture(*args, **kwargs):
        result = native_streamed(*args, **kwargs)
        streamed_results.append(result)
        return result
    monkeypatch.setattr(Runner, 'run_streamed', capture)
    runtime, agent, _, _, _ = offline_planner_runtime(tmp_path, monkeypatch, [])
    async def cancel_running_stream():
        started, closed = asyncio.Event(), asyncio.Event()
        class BlockingPlannerModel(OfflinePlannerModel):
            async def stream_response(self, *args, **kwargs):
                try:
                    yield ResponseTextDeltaEvent(type='response.output_text.delta', sequence_number=0,
                        content_index=0, output_index=0, item_id='pending-plan', logprobs=[], delta='{')
                    started.set()
                    await asyncio.Event().wait()
                finally:
                    closed.set()
        agent.model = BlockingPlannerModel([])
        task = asyncio.create_task(runtime.run(agent=agent, input='frozen input', role='planner',
                                               stage='planner'))
        await asyncio.wait_for(started.wait(), timeout=2)
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, timeout=2)
        assert closed.is_set()
        assert streamed_results[0].run_loop_task.done()
    asyncio.run(cancel_running_stream())
    assert not runtime.usage.events()


def test_non_planner_keeps_native_runtime_call(tmp_path, monkeypatch, real_planner_stream):
    runtime, agent, _, native_calls, native_result = offline_planner_runtime(
        tmp_path, monkeypatch, [planner_completed()])
    context = SimpleNamespace(events=[])
    kwargs = dict(agent=agent, input='native coder input', role='coder', stage='initial_code',
                  context=context, max_turns=7)
    assert asyncio.run(runtime.run(**kwargs)) is native_result
    assert native_calls == [kwargs] and not real_planner_stream
    row = run.read_json(next((tmp_path / 'jobs/planner_fixture/official/generation/role_calls').glob('*.json')))
    assert row['transport'] == 'native_run' and row['status'] == 'COMPLETED'


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


def api_status_error(status=408, code='request_timeout', cls=openai.APIStatusError):
    response = httpx.Response(status, request=httpx.Request('POST', 'http://127.0.0.1:28317/v1/responses'),
                              headers={'x-request-id': 'fixture-request-id'})
    return cls('fixture API request failed', response=response, body={'code': code})


@pytest.mark.parametrize('status', [408, 429, 500, 503])
def test_request_api_status_errors_do_not_pause_batch(status):
    error = api_status_error(status)
    assert isinstance(error, openai.APIError)
    assert not run.shared_fault(error)
    assert run.api_interruption(error)
    assert run.error_evidence(error)['status_code'] == status


def test_api_connection_timeout_are_isolated_but_confirmed_infrastructure_pauses():
    request = httpx.Request('POST', 'http://127.0.0.1:28317/v1/responses')
    for error in (openai.APIConnectionError(request=request), openai.APITimeoutError(request=request)):
        assert not run.shared_fault(error)
        assert run.api_interruption(error)
    class AssetExecutionError(Exception):
        pass
    class AssetInfrastructureError(AssetExecutionError):
        pass
    assert not run.shared_fault(AssetExecutionError('generated source failed export'))
    assert run.shared_fault(AssetInfrastructureError('renderer installation unavailable'))
    assert run.shared_fault(ImportError('native module unavailable'))
    assert run.shared_fault(OSError('No space left on device'))


@pytest.mark.parametrize('error', [api_status_error(401, 'invalid_api_key', openai.AuthenticationError),
                                  api_status_error(403, 'permission_denied', openai.PermissionDeniedError),
                                  api_status_error(429, 'insufficient_quota')])
def test_definite_auth_permission_and_account_faults_remain_shared(error):
    assert run.shared_fault(error)
    assert not run.api_interruption(error)


def install_failing_native(monkeypatch, error, *, returned='raise'):
    """Run the real worker/audit around a tiny native workflow; no API request."""
    @dataclass
    class UsageTotals:
        requests: int = 0
        total_tokens: int = 0
    class Sessions:
        def __init__(self, workspace, task_id):
            self.database_path = workspace / 'not-created.sqlite3'
    class Runtime:
        def __init__(self, **kwargs):
            self.usage = SimpleNamespace(totals=UsageTotals)
        def agent(self, **kwargs):
            return SimpleNamespace(**kwargs)
        async def run(self, **kwargs):
            raise error
    class Workflow:
        def __init__(self, *args):
            pass
        def _runtime(self, *args, **kwargs):
            return Runtime()
        @staticmethod
        def _review_view_labels(rendered, **kwargs):
            return [p.name for p in rendered]
        async def generate(self, request):
            runtime = self._runtime(request, request.workspace)
            agent = runtime.agent(name='native-coder', instructions='native instructions', tools=[])
            try:
                await runtime.run(agent=agent, input='frozen input', role='coder', stage='initial_code')
            except Exception:
                if returned == 'raise':
                    raise
            request.workspace.mkdir(parents=True, exist_ok=True)
            source = request.workspace / 'source.py'
            source.write_text('native retained source')
            glb = request.workspace / 'selected.glb' if returned == 'usable' else None
            if glb:
                glb.write_bytes(b'native returned selection')
            return SimpleNamespace(source_path=source, glb_path=glb, render_paths=(), approved=False,
                                   selected_round=1, usage=runtime.usage.totals())
    modules = {'adsl.agents.models': SimpleNamespace(ObjectRequest=lambda **kw: SimpleNamespace(**kw),
                                                   GradedImageCriticDecision=object),
               'adsl.agents.service': SimpleNamespace(ObjectWorkflow=Workflow),
               'adsl.agents.utils.sessions': SimpleNamespace(SessionManager=Sessions),
               'adsl.agents.utils.runner': SimpleNamespace(AgentRuntime=Runtime),
               'adsl.agents.utils.inputs': SimpleNamespace(user_input=lambda *args: args),
               'adsl.agents.prompts': SimpleNamespace(object_prompt=lambda *args, **kw: 'native critic prompt')}
    native_import = run.importlib.import_module
    monkeypatch.setattr(run.importlib, 'import_module',
                        lambda name: modules[name] if name in modules else native_import(name))
    monkeypatch.setattr(run, 'probe', lambda *args: None)
    return Runtime


@pytest.mark.parametrize('returned', ['raise', 'no_output', 'usable'])
def test_generation_api408_keeps_audit_and_only_usable_native_selection(tmp_path, monkeypatch, returned):
    install_failing_native(monkeypatch, api_status_error(), returned=returned)
    row = case(tmp_path)
    folder = tmp_path / 'jobs' / row['case_id'] / 'official'
    fields = run.request_fields(row, 'official', {})
    run.write_json(folder / 'job.json', dict(case_id=row['case_id'], arm='official',
                   input_sha256=row['input_sha256'], request_sha256=run.digest(fields), generation={'status': 'PENDING'}))
    rc = asyncio.run(run.generate_worker(tmp_path, row['case_id'], 'official', {row['case_id']: row},
                                         {'official': {'profile': 'not loaded'}}, {}))
    assert rc == 0
    job = run.read_json(folder / 'job.json')
    assert not job.get('pause_batch')
    assert job['generation']['status'] == ('COMPLETED' if returned == 'usable' else 'API_INTERRUPTED')
    assert job['generation']['unknown_usage_role_calls'] == 1
    assert job['generation']['actual_initial_calls'] == 1 and job['generation']['actual_source_repairs'] == 0
    evidence = job['generation']['api_interruptions']
    assert len(evidence) == 1 and evidence[0]['stage'] == 'initial_code'
    assert evidence[0]['type'] == 'APIStatusError' and evidence[0]['status_code'] == 408
    assert evidence[0]['request_id'] == 'fixture-request-id'
    assert job['usage']['requests'] == 0  # Failed call usage is unknown, not an invented successful request.
    if returned == 'usable':
        assert job['selected_glb_sha256'] == run.sha256(job['selected_glb'])
        assert not (folder / 'api_interrupted.json').exists()
    else:
        assert (folder / 'api_interrupted.json').is_file()
        assert not (folder / 'generation_failed.json').exists()
    with pytest.raises(ValueError, match='no automatic replay'):
        asyncio.run(run.generate_worker(tmp_path, row['case_id'], 'official', {row['case_id']: row},
                                        {'official': {'profile': 'not loaded'}}, {}))


def test_swallowed_native_auth_error_still_pauses(tmp_path, monkeypatch):
    install_failing_native(monkeypatch, api_status_error(401, 'invalid_api_key', openai.AuthenticationError),
                           returned='no_output')
    row = case(tmp_path)
    folder = tmp_path / 'jobs' / row['case_id'] / 'official'
    run.write_json(folder / 'job.json', dict(arm='official', input_sha256=row['input_sha256'],
                   request_sha256=run.digest(run.request_fields(row, 'official', {})), generation={'status': 'PENDING'}))
    assert asyncio.run(run.generate_worker(tmp_path, row['case_id'], 'official', {row['case_id']: row},
                                           {'official': {'profile': 'not loaded'}}, {})) == 2
    job = run.read_json(folder / 'job.json')
    assert job['pause_batch'] and job['generation']['status'] == 'PAUSED'
    assert job['generation']['shared_failures'][0]['status_code'] == 401


def test_common_review_api408_is_indeterminate_without_pause_or_replay(tmp_path, monkeypatch):
    install_failing_native(monkeypatch, api_status_error())
    row = case(tmp_path)
    folder = tmp_path / 'jobs' / row['case_id'] / 'official'
    views = [tmp_path / f'view_{n}.png' for n in range(8)]
    for path in views:
        path.write_bytes(b'fixture view')
    run.write_json(folder / 'evaluation/image_critic_request.json',
                   dict(status='READY', rendered_images=[str(p) for p in views], reference_image=row['input_image']))
    assert asyncio.run(run.review_worker(tmp_path, row['case_id'], 'official', {row['case_id']: row},
                                         {'ours': {'profile': 'not loaded'}})) == 0
    review_path = folder / 'review/image_critic.json'
    saved = run.read_json(review_path)
    assert saved['status'] == 'INDETERMINATE' and saved['api_interruption']
    assert saved['error']['status_code'] == 408 and saved['unknown_usage_role_calls'] == 1
    assert saved['read_only'] and saved['no_candidate_selection']
    run.write_json(folder / 'evaluation/result.json', {'status': 'PASS', 'appearance': {'status': 'PENDING'}})
    measured, _ = run.merge_final_appearance(folder / 'evaluation/result.json', review_path)
    assert measured['status'] == measured['appearance']['status'] == 'INDETERMINATE'
    before = review_path.read_bytes()
    assert asyncio.run(run.review_worker(tmp_path, row['case_id'], 'official', {}, {})) == 0
    assert review_path.read_bytes() == before


def test_failed_unified_views_do_not_fall_back_to_generation_images(tmp_path, monkeypatch):
    folder = tmp_path / 'jobs' / run.CASE_ORDER[0] / 'official'
    run.write_json(folder / 'evaluation/image_critic_request.json', {'status': 'INDETERMINATE', 'rendered_images': []})
    monkeypatch.setattr(run, 'probe', lambda *args: None)
    assert asyncio.run(run.review_worker(tmp_path, run.CASE_ORDER[0], 'official', {}, {})) == 0
    saved = run.read_json(folder / 'review/image_critic.json')
    assert saved['status'] == 'INDETERMINATE' and saved['no_candidate_selection']


@pytest.mark.parametrize('api408', [False, True])
def test_serial_twelve_jobs_and_completed_resume_make_no_new_generation(tmp_path, monkeypatch, api408):
    if api408:
        install_failing_native(monkeypatch, api_status_error())
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
    run.write_json(tmp_path / 'config/cases.json', {'cases': list(cases.values())})
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
            if api408 and (cid, method) == (run.CASE_ORDER[0], 'official'):
                return asyncio.run(run.generate_worker(root, cid, method, cases, arms, {}))
            job = run.read_json(folder / 'job.json')
            job['generation']['status'] = 'generation_failed'
            run.write_json(folder / 'job.json', job)
        else:
            run.write_json(folder / 'review/image_critic.json', {'status': 'INDETERMINATE'})
        return 0
    def fake_evaluation(root, cid, arm, *args):
        if api408:
            from experiments.benchmark_six import evaluate
            evaluate.evaluate(root, cid, arm)
            return 0
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
    if api408:
        interrupted = run.read_json(tmp_path / 'jobs' / run.CASE_ORDER[0] / 'official/job.json')
        assert interrupted['status'] == 'COMPLETED'
        assert interrupted['generation']['status'] == 'API_INTERRUPTED'
        assert interrupted['generation']['api_interruptions'][0]['status_code'] == 408
        measured = interrupted['offline_evaluation']
        assert measured['status'] == 'API_INTERRUPTED' and measured['included_in_denominator']
        assert measured['appearance']['status'] == 'INDETERMINATE' and measured['metrics']['G'] is None
        assert run.read_json(tmp_path / 'jobs' / run.CASE_ORDER[-1] / 'ours/job.json')['status'] == 'COMPLETED'
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
