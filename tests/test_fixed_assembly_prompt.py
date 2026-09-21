"""Offline checks for original-prompt fixed-assembly generation and its gate."""
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from adsl.agents.utils.io import read_json, write_json
from experiments.fixed_assembly_prompt import run as prompt


CONFIG = {'mm_per_unit': 1., 'fit_offset_mm': .2,
          'final_size_mm': [120., 120., 120.], 'validation_mode': 'visual_only',
          'require_multiple_parts': False}


def frozen_input():
    return {'original_task': {'prompt': 'A small wooden cabinet', 'image_paths': []},
            'manufacturing_requirements': prompt.MANUFACTURING,
            'fixed_assembly': dict(CONFIG)}


def stage_records(work, inputs, *, normalized=False):
    for index, stage in enumerate(('plan', 'initial_code'), 1):
        payload = {'requirement': inputs['original_task']['prompt'] + '\n' +
                   inputs['manufacturing_requirements'],
                   'fixed_assembly': inputs['fixed_assembly']}
        if stage == 'initial_code':
            payload.update(articulation_required=False, plan={}, assignment='complete source')
        text = json.dumps(payload)
        write_json(work/'stage_inputs'/f'{stage}.json', {
            'stage': stage, 'input': text, 'source_empty_before_call': True})
        actual = [{'role': 'user', 'content': [{'type': 'input_text', 'text': text}]}]
        write_json(work/'api_calls'/f'{index:03d}.json', {
            'stage': stage, 'input': actual if normalized else text,
            'status': 'COMPLETED', 'total_tokens': 17})


@pytest.mark.parametrize('max_rounds', [None, 2, 5])
def test_prepare_uses_original_prompt_only_and_freezes_dimensions(tmp_path, monkeypatch, max_rounds):
    manifest = tmp_path/'manifest.json'
    cases = [{'case_id': cid, 'prompt': f'original task {cid}', 'dataset': 'fixture',
              'object_id': cid, 'caption_source': 'captions.json',
              'source': 'ARCHIVED_SOURCE_SENTINEL',
              'image_paths': ['ARCHIVED_RENDER_SENTINEL.png']}
             for cid in prompt.IDS]
    write_json(manifest, {'cases': cases})
    monkeypatch.setattr(prompt, 'MANIFEST', manifest)
    monkeypatch.setattr(prompt.subprocess, 'check_output', lambda *a, **kw: 'fixture-commit\n')
    root = tmp_path/'batch'
    prompt.prepare(root, **({} if max_rounds is None else {'max_rounds':max_rounds}))
    expected_rounds = 5 if max_rounds is None else max_rounds
    for case in cases:
        cid = case['case_id']
        inputs = read_json(root/cid/'input.json')
        assert inputs['original_task'] == {'prompt': case['prompt'], 'image_paths': []}
        assert inputs['manufacturing_requirements'].startswith(prompt.MANUFACTURING)
        assert 'there must be at least two distinct print parts' not in inputs['manufacturing_requirements']
        assert "Use the existing Planner's component and connection plan" in inputs['manufacturing_requirements']
        assert f'at most {expected_rounds} evaluation rounds' in inputs['manufacturing_requirements']
        assert f'at most {expected_rounds-1} source repairs' in inputs['manufacturing_requirements']
        assert inputs['fixed_assembly'] == dict(CONFIG, final_size_mm=prompt.SIZES[cid])
        assert inputs['initial_generation_limit'] == 1
        assert inputs['source_repair_limit'] == expected_rounds-1
        assert 'ARCHIVED_' not in json.dumps(inputs)
    batch = read_json(root/'batch.json')
    assert batch['case_order'] == list(prompt.IDS)
    assert batch['physical_checkers'] == []
    assert batch['cross_experiment_token_accounting'] is False
    assert not list(root.rglob('source.py'))
    assert not list(root.rglob('*.png'))


@pytest.mark.parametrize('old_multiple_parts', [None, True])
def test_prepare_preserves_old_frozen_mode(tmp_path, old_multiple_parts):
    # Existing directories retain their original geometry default; a new visual
    # experiment must use a new directory, not rewrite frozen inputs or hashes.
    inputs = frozen_input()
    inputs['fixed_assembly'].pop('validation_mode')
    if old_multiple_parts is None:
        inputs['fixed_assembly'].pop('require_multiple_parts')
    else:
        inputs['fixed_assembly']['require_multiple_parts'] = old_multiple_parts
    path = write_json(tmp_path/'SF07/input.json', inputs)
    marker = write_json(tmp_path/'batch.json', {'input_sha256': {'SF07': prompt.file_hash(path)}})
    before = (path.read_bytes(), marker.read_bytes())
    prompt.prepare(tmp_path)
    assert (path.read_bytes(), marker.read_bytes()) == before


@pytest.mark.parametrize('max_rounds', [0, 6])
def test_prepare_rejects_invalid_round_budget(tmp_path, max_rounds):
    root = tmp_path/'new_batch'
    with pytest.raises(ValueError, match='max_rounds'):
        prompt.prepare(root, max_rounds=max_rounds)
    assert not root.exists()


def test_one_fresh_topology_case_freezes_only_requested_tool_and_stepcode(tmp_path):
    profile=prompt.REPO/'adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml'
    prompt.prepare(tmp_path,cases=('SF07',),assembly_topology=True,profile=profile)
    batch=read_json(tmp_path/'batch.json');inputs=read_json(tmp_path/'SF07/input.json')
    assert batch['case_order']==['SF07'] and batch['model_profile']==str(profile)
    assert batch['physical_checkers']==['assembly_topology']
    assert [s['name'] for s in inputs['checker_specs']]==['assembly_topology']
    assert inputs['fixed_assembly']['validation_mode']=='visual_only'
    assert inputs['source_repair_limit']==4 and inputs['original_task']['image_paths']==[]
    assert not (tmp_path/'SF03').exists() and not list(tmp_path.rglob('source.py'))


@pytest.mark.parametrize('normalized', [False, True])
def test_audit_requires_matching_actual_model_input(tmp_path, normalized):
    work, inputs = tmp_path/'generate', frozen_input()
    stage_records(work, inputs, normalized=normalized)
    result = prompt.input_audit(work, inputs)
    assert result['input_verified']
    assert all(stage['actual_model_input_matches_stage_input']
               for stage in result['stages'].values())
    assert not result['assembly_api_verified']


@pytest.mark.parametrize('parts, links, stale, required, connector_calls, expected', [
    (1, [], False, False, False, True),
    (1, [], True, False, False, False),
    (1, [], False, True, False, False),
    (2, [{'id':'joint'}], False, False, False, False),
    (2, [{'id':'joint'}], False, False, True, True),
])
def test_single_part_api_audit_uses_current_manifest(tmp_path, parts, links, stale, required, connector_calls, expected):
    work, inputs = tmp_path/'generate', frozen_input()
    inputs['fixed_assembly']['require_multiple_parts'] = required
    stage_records(work, inputs)
    source = work/'original/source.py'
    source.parent.mkdir()
    source.write_text('assembly = FixedAssembly(root_id="body", mm_per_unit=1)\n'
                      + ('joint = TabSlot()\nassembly.connect()\n' if connector_calls else ''))
    write_json(work/'rounds/round_01/asset/assembly/assembly_manifest.json', {
        'source_sha256':'old' if stale else prompt.file_hash(source),
        'part_declarations':[{'id':f'part{i}'} for i in range(parts)], 'connections':links})
    audit = prompt.input_audit(work, inputs)
    assert audit['input_verified']
    assert audit['assembly_api_verified'] is expected


@pytest.mark.parametrize('violation', [
    'nonempty_source', 'old_source_field', 'old_image_field', 'actual_mismatch',
    'frozen_dimensions_changed', 'original_prompt_missing', 'missing_actual_call',
])
def test_audit_rejects_dirty_or_unproven_initial_input(tmp_path, violation):
    work, inputs = tmp_path/'generate', frozen_input()
    stage_records(work, inputs)
    stage_path = work/'stage_inputs/initial_code.json'
    api_path = work/'api_calls/002.json'
    row, call = read_json(stage_path), read_json(api_path)
    payload = json.loads(row['input'])
    if violation == 'nonempty_source':
        row['source_empty_before_call'] = False
    elif violation == 'old_source_field':
        payload['original_source'] = 'class ArchivedAsset: pass'
    elif violation == 'old_image_field':
        payload['image_paths'] = ['old/generated.png']
    elif violation == 'frozen_dimensions_changed':
        payload['fixed_assembly']['final_size_mm'] = [1., 2., 3.]
    elif violation == 'original_prompt_missing':
        payload['requirement'] = prompt.MANUFACTURING
    row['input'] = json.dumps(payload)
    call['input'] = 'different real request' if violation == 'actual_mismatch' else row['input']
    write_json(stage_path, row)
    if violation == 'missing_actual_call':
        api_path.unlink()
    else:
        write_json(api_path, call)
    assert not prompt.input_audit(work, inputs)['input_verified']


@pytest.mark.parametrize('extra', [
    {'type': 'input_text', 'text': 'OLD_SOURCE = ArchivedAsset()'},
    {'type': 'input_image', 'image_url': 'data:image/png;base64,b2xk'},
])
def test_audit_rejects_extra_actual_model_content(tmp_path, extra):
    work, inputs = tmp_path/'generate', frozen_input()
    stage_records(work, inputs, normalized=True)
    path = work/'api_calls/002.json'
    call = read_json(path)
    call['input'][0]['content'].append(extra)
    write_json(path, call)
    assert not prompt.input_audit(work, inputs)['input_verified']


def test_request_log_records_large_actual_usage_and_failed_call_separately(tmp_path):
    response = SimpleNamespace(usage=SimpleNamespace(input_tokens=900_000, output_tokens=200_000))
    model = SimpleNamespace(get_response=AsyncMock(side_effect=[response, RuntimeError('API failed')]))
    logger = prompt.RequestLogModel(model, tmp_path)
    logger.stage = 'initial_code'

    async def invoke():
        assert await logger.get_response(input='original task', tools=[]) is response
        with pytest.raises(RuntimeError, match='API failed'):
            await logger.get_response(input='followup', tools=[])

    asyncio.run(invoke())
    first, failed = [read_json(p) for p in sorted((tmp_path/'api_calls').glob('*.json'))]
    assert first['status'] == 'COMPLETED'
    assert (first['input_tokens'], first['output_tokens'], first['total_tokens']) == (900_000, 200_000, 1_100_000)
    assert first['stage'] == 'initial_code' and first['input'] == 'original task'
    assert failed['status'] == 'ERROR' and failed['total_tokens'] is None
    assert model.get_response.await_count == 2
    assert not list(tmp_path.rglob('*token_budget*'))
    assert not list(tmp_path.rglob('*ledger*'))


@pytest.mark.parametrize('repair_limit', [None, 0, 4])
def test_native_generation_keeps_completed_usage_after_tool_failure(tmp_path, monkeypatch, repair_limit):
    inputs = frozen_input()
    if repair_limit is not None:
        inputs['source_repair_limit'] = repair_limit
    input_path = write_json(tmp_path/'SF07/input.json', inputs)
    write_json(tmp_path/'batch.json', {'input_sha256': {'SF07': prompt.file_hash(input_path)}})
    requests = []

    class Workflow:
        def __init__(self, profile):
            assert profile == prompt.PROFILE

        async def generate(self, request):
            requests.append(request)
            assert not (request.workspace/'source.py').exists()
            response = SimpleNamespace(usage=SimpleNamespace(input_tokens=31, output_tokens=7))
            logger = prompt.RequestLogModel(
                SimpleNamespace(get_response=AsyncMock(return_value=response)), request.workspace)
            logger.stage = 'initial_code'
            await logger.get_response(input=request.requirement, tools=[])
            raise RuntimeError('tool failed after completed model call')

    monkeypatch.setattr(prompt, 'PromptWorkflow', Workflow)
    monkeypatch.setattr(prompt, 'input_audit', lambda *a: {
        'input_verified': True, 'assembly_api_verified': True})
    result = asyncio.run(prompt.run_case(tmp_path, 'SF07'))
    request, = requests
    assert request.max_rounds == (2 if repair_limit is None else repair_limit+1)
    assert result['max_rounds'] == request.max_rounds
    assert result['source_repair_limit'] == request.max_rounds-1
    assert request.image_paths == request.checker_specs == ()
    assert request.articulation is False and request.fixed_assembly == CONFIG
    assert request.requirement == ('[ORIGINAL TASK]\n' + inputs['original_task']['prompt'] +
        '\n\n[UNIFORM MANUFACTURING REQUIREMENTS]\n' + prompt.MANUFACTURING)
    assert not result['pause_batch'] and not result['approved']
    assert result['known_actual_tokens'] == 38 and result['actual_api_requests'] == 1
    assert result['unknown_usage_requests'] == 0
    assert result['exception']['reason'] == 'tool failed after completed model call'
    assert result['physical_checkers'] == 'NOT_EXECUTED'


@pytest.mark.parametrize('gate', ['absent', 'paused', 'input_unverified', 'assembly_unverified'])
def test_incomplete_single_case_does_not_block_later_cases(tmp_path, monkeypatch, gate):
    monkeypatch.setattr(prompt, 'prepare', lambda root, **kw: None)
    run_case = AsyncMock(return_value={'pause_batch': False})
    monkeypatch.setattr(prompt, 'run_case', run_case)
    if gate != 'absent':
        write_json(tmp_path/'SF07/result.json', {'pause_batch': gate == 'paused'})
        write_json(tmp_path/'SF07/input_audit.json', {
            'input_verified': gate != 'input_unverified',
            'assembly_api_verified': gate != 'assembly_unverified'})
    assert asyncio.run(prompt.main(tmp_path, ['SF03', 'SF13']))==0
    assert run_case.await_count==2


def test_main_runs_later_cases_after_sf07_audit_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, 'prepare', lambda root, **kw: None)
    write_json(tmp_path/'SF07/result.json', {'pause_batch': False})
    write_json(tmp_path/'SF07/input_audit.json', {
        'input_verified': True, 'assembly_api_verified': True})
    run_case = AsyncMock(return_value={'pause_batch': False})
    monkeypatch.setattr(prompt, 'run_case', run_case)
    assert asyncio.run(prompt.main(tmp_path, ['SF03', 'SF13'])) == 0
    assert [call.args[1] for call in run_case.await_args_list] == ['SF03', 'SF13']


def test_main_records_pause_when_first_case_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(prompt, 'prepare', lambda root, **kw: None)
    run_case = AsyncMock(return_value={'pause_batch': True})
    monkeypatch.setattr(prompt, 'run_case', run_case)
    assert asyncio.run(prompt.main(tmp_path, ['SF07'])) == 2
    run_case.assert_awaited_once_with(tmp_path, 'SF07')
    assert read_json(tmp_path/'paused.json')['case'] == 'SF07'


INVALID_MESH = {'code': 'EXPORTED_FILE_INVALID', 'file': 'pedestal.stl',
                'reason': 'mesh is not a finite closed oriented volume, or has zero-area faces'}


@pytest.mark.parametrize('failure, should_pause', [
    (INVALID_MESH, False),
    ({'code': 'EXPORTED_FILE_GEOMETRY_MISMATCH', 'file': 'scene.glb'}, False),
    ({'code': 'EXPORTED_FILE_GEOMETRY_MISMATCH', 'file': 'part.stl',
      'triangle_coordinate_deviation_mm': 12.2976}, False),
    ({'code': 'EXPORTED_FILE_PLACEMENT_OR_SCALE_MISMATCH', 'file': 'scene.glb'}, False),
    ({**INVALID_MESH, 'reason': 'No such file: scene.glb'}, False),
    ({**INVALID_MESH, 'reason': "geometry node 'mesh' has no unique print-part ID"}, False),
    ({**INVALID_MESH, 'reason': 'unexpected read error'}, False),
    ({'code':'SHARED_ENVIRONMENT_UNAVAILABLE','reason':'confirmed external renderer service unavailable'},True),
])
def test_candidate_geometry_rejection_does_not_imply_shared_failure(
        tmp_path, monkeypatch, failure, should_pause):
    inputs = frozen_input()
    path = write_json(tmp_path/'SF07/input.json', inputs)
    write_json(tmp_path/'batch.json', {'input_sha256': {'SF07': prompt.file_hash(path)}})

    class Workflow:
        def __init__(self, profile):
            pass

        async def generate(self, request):
            assert request.max_rounds == 2 and request.checker_specs == ()
            write_json(request.workspace/'rounds/round_01/assembly_manifest.json', {
                'status': 'FAIL', 'failures': [failure]})
            return SimpleNamespace(approved=False)

    monkeypatch.setattr(prompt, 'PromptWorkflow', Workflow)
    monkeypatch.setattr(prompt, 'input_audit', lambda *args: {
        'input_verified': True, 'assembly_api_verified': True})
    result = asyncio.run(prompt.run_case(tmp_path, 'SF07'))
    assert result['pause_batch'] is should_pause
    assert not result['approved']
    assert result['geometry_failures'] == [failure]
    assert result['physical_checkers'] == 'NOT_EXECUTED'


def test_current_report_not_historical_export_error_controls_batch(tmp_path):
    failure={'code':'EXPORTED_FILE_INVALID','file':'part.stl','reason':'No such file'}
    source=tmp_path/'candidate.py';source.write_text('current candidate')
    report={'source_sha256':prompt.file_hash(source),'failures':[]}
    ledger={'retained':'original','working':'attempt_0001','versions':{
        'original':{'reviews':{'geometry':{'failures':[failure]}}},
        'attempt_0001':{'source':str(source),'reviews':{'geometry':report}}}}
    path=write_json(tmp_path/'assembly_versions.json',ledger)
    before=path.read_bytes()
    assert prompt.current_geometry_failures(tmp_path,[failure])==[]
    assert path.read_bytes()==before  # Never rewrite old evidence/retained.
    report['failures']=[failure]
    write_json(path,ledger)
    assert prompt.current_geometry_failures(tmp_path)==[failure]
    assert prompt.shared_export_failures(prompt.current_geometry_failures(tmp_path))==[]
    source.write_text('other version')
    assert prompt.shared_export_failures(prompt.current_geometry_failures(tmp_path))[0]['code']=='CURRENT_REPORT_SOURCE_MISMATCH'


def saved_geometry_pause(root):
    folder = root/'SF07'
    write_json(folder/'result.json', {
        'approved': False, 'pause_batch': True, 'geometry_failures': [INVALID_MESH],
        'exception': None, 'tool_errors': [], 'flow_errors': [], 'repair_attempts': 1})
    write_json(folder/'input_audit.json', {'input_verified': True, 'assembly_api_verified': True})
    write_json(folder/'generate/rounds/round_01/assembly_manifest.json', {
        'status': 'FAIL', 'failures': [INVALID_MESH]})
    write_json(folder/'generate/api_calls/001.json', {'status': 'COMPLETED', 'total_tokens': 27})
    return folder


def test_saved_sf07_geometry_pause_can_continue_without_replay_or_result_overwrite(tmp_path, monkeypatch):
    folder = saved_geometry_pause(tmp_path)
    original = (folder/'result.json').read_bytes()
    monkeypatch.setattr(prompt, 'prepare', lambda root, **kw: None)
    run_case = AsyncMock(return_value={'pause_batch': False, 'approved': False})
    monkeypatch.setattr(prompt, 'run_case', run_case)
    assert asyncio.run(prompt.main(tmp_path, ['SF03', 'SF13'])) == 0
    assert [call.args[1] for call in run_case.await_args_list] == ['SF03', 'SF13']
    assert (folder/'result.json').read_bytes() == original
    gate = read_json(folder/'continuation_gate.json')
    assert gate['original_pause_batch'] is True and gate['pause_batch'] is False
    assert not gate['case_approved'] and not gate['case_replayed']
    assert gate['evidence_sha256'][str(folder/'result.json')] == prompt.file_hash(folder/'result.json')
    assert read_json(folder/'result.json')['repair_attempts'] == 1


@pytest.mark.parametrize('defect', ['mismatch', 'api_error', 'flow_error', 'audit_error', 'evidence_missing','shared_fault'])
def test_saved_pause_only_blocks_confirmed_shared_fault(tmp_path, monkeypatch, defect):
    folder = saved_geometry_pause(tmp_path)
    if defect == 'mismatch':
        failure = {'code': 'EXPORTED_FILE_GEOMETRY_MISMATCH', 'file': 'scene.glb'}
        path = folder/'result.json'
        result = read_json(path)
        result['geometry_failures'] = [failure]
        write_json(path, result)
        write_json(folder/'generate/rounds/round_01/assembly_manifest.json', {'failures': [failure]})
    elif defect == 'api_error':
        write_json(folder/'generate/api_calls/001.json', {'status': 'ERROR'})
    elif defect == 'flow_error':
        write_json(folder/'generate/rounds/round_01/flow_error.json', {'reason': 'version mismatch'})
    elif defect == 'audit_error':
        write_json(folder/'input_audit.json', {'input_verified':False, 'stages':{
            'initial_code':{'frozen_config_unchanged':False}}})
    elif defect == 'shared_fault':
        write_json(folder/'generate/rounds/round_01/assembly_manifest.json', {
            'failures':[{'code':'SHARED_ENVIRONMENT_UNAVAILABLE','reason':'confirmed service failure'}]})
    else:
        (folder/'generate/rounds/round_01/assembly_manifest.json').unlink()
    monkeypatch.setattr(prompt, 'prepare', lambda root, **kw: None)
    run_case = AsyncMock(return_value={'pause_batch':False,'approved':False})
    monkeypatch.setattr(prompt, 'run_case', run_case)
    blocked=defect in ('audit_error','shared_fault')
    if blocked:
        with pytest.raises(ValueError, match='SF07'):
            asyncio.run(prompt.main(tmp_path, ['SF03', 'SF13']))
        run_case.assert_not_awaited()
    else:
        assert asyncio.run(prompt.main(tmp_path,['SF03','SF13']))==0
        assert run_case.await_count==2
    assert read_json(folder/'continuation_gate.json')['pause_batch'] is blocked


def test_frozen_input_change_is_saved_and_stops_before_model(tmp_path,monkeypatch):
    write_json(tmp_path/'SF07/input.json',frozen_input())
    write_json(tmp_path/'batch.json',{'input_sha256':{'SF07':'other version'}})
    monkeypatch.setattr(prompt,'PromptWorkflow',lambda *a:pytest.fail('must not call model'))
    result=asyncio.run(prompt.run_case(tmp_path,'SF07'))
    assert result['pause_batch'] and not result['approved']
    assert result['shared_failures'][0]['code']=='FROZEN_INPUT_CHANGED'
    assert read_json(tmp_path/'SF07/result.json')==result


def test_unreadable_export_manifest_still_saves_case_result(tmp_path,monkeypatch):
    inputs=frozen_input()
    path=write_json(tmp_path/'SF07/input.json',inputs)
    write_json(tmp_path/'batch.json',{'input_sha256':{'SF07':prompt.file_hash(path)}})
    class Workflow:
        def __init__(self,*a):pass
        async def generate(self,request):
            stage_records(request.workspace,inputs)
            source=request.workspace/'source.py';source.write_text('preserved source')
            path=request.workspace/'rounds/round_01/assembly_manifest.json'
            path.parent.mkdir(parents=True);path.write_text('{broken JSON')
            return SimpleNamespace(approved=False)
    monkeypatch.setattr(prompt,'PromptWorkflow',Workflow)
    result=asyncio.run(prompt.run_case(tmp_path,'SF07'))
    assert not result['pause_batch'] and not result['approved']
    assert result['current_geometry_failures'][0]['code']=='EXPORTED_REPORT_UNAVAILABLE'
    assert read_json(tmp_path/'SF07/input_audit.json')['artifact_errors']
    assert (tmp_path/'SF07/generate/source.py').read_text()=='preserved source'
    assert read_json(tmp_path/'SF07/result.json')==result
