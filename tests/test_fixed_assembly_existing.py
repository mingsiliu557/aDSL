"""Offline regressions for the archived-asset conversion adapter."""
import asyncio
import base64
import json
from types import SimpleNamespace

import pytest

from adsl.agents.models import (
    CodeCriticDecision, FixedAssemblyPlan, ImageCriticDecision, ObjectRequest,
)
from adsl.agents.overhang_edit import file_hash, version_assets, version_record
from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.execution import ExecutionResult
from adsl.agents.utils.io import read_json, write_json
from experiments.fixed_assembly_existing import run as adapter


CONFIG = {'mm_per_unit': 1., 'fit_offset_mm': .2, 'final_size_mm': [60., 20., 62.]}


def test_runtime_needs_no_cumulative_ledger_and_keeps_per_run_usage(tmp_path, monkeypatch):
    model, usage = object(), object()
    runtime = SimpleNamespace(model=model, usage=usage)
    monkeypatch.setattr(ObjectWorkflow, '_runtime', lambda *a, **kw: runtime)
    monkeypatch.setattr(adapter.tempfile, 'mkdtemp', lambda **kw: str(tmp_path/'sessions'))
    workflow = adapter.ExistingAssetWorkflow(original={})
    request = ObjectRequest('convert', tmp_path, 'no_ledger', fixed_assembly=CONFIG)
    result = workflow._runtime(request, tmp_path, mode='existing_fixed_assembly_experiment')
    assert result is runtime
    assert result.model is model and result.usage is usage
    assert not list(tmp_path.rglob('*token_budget*'))


def test_missing_feedback_report_is_not_a_geometry_design_failure(tmp_path):
    path = tmp_path/'assembly_run/rounds/round_02/candidate/edit_outcome.json'
    write_json(path, {'status':'TOOL_ERROR',
        'reason':'Error running tool read_file: No such file: asset/assembly/assembly_manifest.json'})
    result = adapter.classify(tmp_path, False)
    assert result['category'] == 'MISSING_FEEDBACK_REPORT_TOOL_ERROR'
    assert result['pause_batch'] is True
    assert result['tool_errors'][0]['report'] == str(path)


def assembly_plan(**changes):
    return FixedAssemblyPlan.model_validate({
        'object_name': 'existing table',
        'components': [{'name': name, 'description': name} for name in ('top', 'base')],
        'relations': ['base supports top'], 'critic_checklist': ['preserve silhouette'],
        'print_parts': [{'id': name, 'components': [name]} for name in ('top', 'base')],
        'root_part': 'top',
        'connections': [{'id': 'joint', 'tab_part': 'base', 'slot_part': 'top',
                         'tab_port': 'tab', 'slot_port': 'slot', 'parameter_name': 'joint',
                         'interface_type': 'tab_slot', 'insertion_direction': '+Z',
                         'fit_intent': 'demonstration clearance'}],
        **CONFIG, **changes,
    })


def saved_version(root, name, *, accepted=False):
    folder = root/name
    render = folder/'asset/render'
    render.mkdir(parents=True)
    source = folder/'source.py'
    source.write_text(f'# {name} source\n')
    glb = render/'scene.glb'
    glb.write_bytes(f'{name} model'.encode())
    image = render/'view.png'
    image.write_bytes(f'{name} image'.encode())
    execution = ExecutionResult(folder/'asset', glb, None, (image,), '', '')
    extras = []
    if accepted:
        assembly = execution.output_root/'assembly'
        assembly.mkdir()
        write_json(assembly/'assembly_manifest.json', {'status': 'PASS', 'failures': []})
        (assembly/'top.stl').write_bytes(f'{name} print part'.encode())
        extras = list(assembly.iterdir())
    return version_record(name, source, execution, reviews={'accepted': accepted},
                          extra_files=extras)


def test_conversion_planner_receives_original_source_frozen_config_and_images(tmp_path):
    original = saved_version(tmp_path, 'original')
    source, execution, _ = version_assets(original)
    request = ObjectRequest('convert this original', tmp_path, 'test', fixed_assembly=CONFIG)
    calls = []

    class Runtime:
        def agent(self, **kwargs):
            return kwargs

        async def run(self, **kwargs):
            calls.append(kwargs)
            return SimpleNamespace(final_output=assembly_plan())

    workflow = ObjectWorkflow.__new__(ObjectWorkflow)
    plan = asyncio.run(adapter.plan_conversion(
        workflow, Runtime(), request, source, execution.render_paths))
    call, = calls
    assert call['agent']['output_type'] is FixedAssemblyPlan
    content = call['input'][0]['content']
    payload = json.loads(content[0]['text'])
    assert payload['original_source'] == source.read_text()
    assert payload['requirement'] == request.requirement
    assert payload['fixed_assembly'] == CONFIG
    assert base64.b64decode(content[1]['image_url'].split(',', 1)[1]) == b'original image'
    assert read_json(tmp_path/'planner_input.json') == payload
    assert read_json(tmp_path/'plan.json') == plan.model_dump()


@pytest.mark.parametrize('change', [
    {'mm_per_unit': 2.}, {'final_size_mm': [120., 40., 124.]},
])
def test_conversion_planner_cannot_change_frozen_dimensions(tmp_path, change):
    source = tmp_path/'source.py'
    source.write_text('# archived source')

    async def run(**kwargs):
        return SimpleNamespace(final_output=assembly_plan(**change))

    runtime = SimpleNamespace(agent=lambda **kwargs: kwargs, run=run)
    request = ObjectRequest('convert', tmp_path, 'test', fixed_assembly=CONFIG)
    with pytest.raises(ValueError, match='frozen scale or target size'):
        asyncio.run(adapter.plan_conversion(ObjectWorkflow.__new__(ObjectWorkflow),
                    runtime, request, source, ()))
    assert not (tmp_path/'plan.json').exists()


@pytest.mark.parametrize('round_number', [1, 2])
def test_both_critics_receive_original_even_when_baseline_is_candidate(tmp_path, round_number):
    original = saved_version(tmp_path, 'original')
    candidate = saved_version(tmp_path, 'candidate')
    source, execution, _ = version_assets(candidate)
    workflow = adapter.ExistingAssetWorkflow.__new__(adapter.ExistingAssetWorkflow)
    workflow.original = original
    calls = []

    async def run(**kwargs):
        calls.append(kwargs)
        cls = ImageCriticDecision if kwargs['role'].startswith('image-critic') else CodeCriticDecision
        return SimpleNamespace(final_output=cls(approved=False, observations=['preserve original']))

    request = ObjectRequest('convert', tmp_path, 'test', fixed_assembly=CONFIG)
    approved, _ = asyncio.run(workflow._review_candidate_appearance(
        runtime=SimpleNamespace(run=run), request=request, workspace=tmp_path,
        round_number=round_number, proposal_index=0,
        proposal=SimpleNamespace(model_dump=lambda: {}), baseline_execution=execution,
        candidate_execution=execution, candidate_source=source,
        candidate_root=source.parent, image_critic='image', code_critic='code'))
    assert not approved
    assert [call['agent'] for call in calls] == ['image', 'code']
    for call in calls:
        content = call['input'][0]['content']
        images = [base64.b64decode(item['image_url'].split(',', 1)[1])
                  for item in content if item['type'] == 'input_image']
        assert images == [b'original image', b'candidate image']


@pytest.mark.parametrize('accepted', [False, True])
def test_publish_selects_matching_original_or_approved_retained_bytes(tmp_path, accepted):
    original = saved_version(tmp_path, 'original')
    retained = saved_version(tmp_path, 'retained', accepted=True)
    rejected = saved_version(tmp_path, 'rejected')
    work = tmp_path/'assembly_run'
    work.mkdir()
    # The mutable workspace and latest candidate are deliberately not retained.
    (work/'source.py').write_text('# rejected mutable source')
    write_json(work/'assembly_versions.json', {
        'retained': 'retained', 'candidate': 'rejected',
        'versions': {'retained': retained, 'rejected': rejected},
    })
    assert adapter.select_and_publish(tmp_path, original,
                                      SimpleNamespace(approved=accepted)) is accepted
    expected = retained if accepted else original
    source, execution, _ = version_assets(expected)
    final = tmp_path/'final'
    assert file_hash(final/'source.py') == file_hash(source)
    assert file_hash(final/'scene.glb') == file_hash(execution.glb_path)
    assert file_hash(final/'render/view.png') == file_hash(execution.render_paths[0])
    selection = read_json(final/'selection.json')
    assert selection['version'] == expected
    assert selection['assembly_approved'] is accepted
    assert selection['physical_validation'] == 'NOT_EVALUATED'
    assert selection['original_is_not_automatically_assembly_approved']
    assert (final/'assembly').exists() is accepted
    if accepted:
        assert selection['extra_files']['assembly/top.stl'] == file_hash(
            execution.output_root/'assembly/top.stl')


@pytest.mark.parametrize('code, pause', [
    ('EXPORTED_FILE_GEOMETRY_MISMATCH', True),
    ('EXPORTED_FILE_INVALID', True),
    ('EXPORTED_INTERFACE_GEOMETRY_MISMATCH', True),
    ('UNEXPECTED_PART_COLLISION', False),
])
def test_failure_classification_pauses_shared_exports_but_not_individual_geometry(tmp_path, code, pause):
    report = tmp_path/'assembly_run/rounds/round_01/asset/assembly/assembly_manifest.json'
    write_json(report, {'status': 'FAIL', 'failures': [{'code': code}]})
    result = adapter.classify(tmp_path, accepted=False)
    assert result['pause_batch'] is pause
    assert result['category'] == ('ASSEMBLY_IMPLEMENTATION_OR_FLOW_ERROR_REQUIRES_REVIEW'
        if pause else 'AGENT_DESIGN_OR_ORIGINAL_GEOMETRY_LIMITATION')
    assert result['geometry_failures'] == [{'code': code, 'report': str(report)}]


def test_flow_failure_pauses_even_with_no_geometry_manifest(tmp_path):
    write_json(tmp_path/'assembly_run/rounds/round_01/flow_error.json', {'type': 'RuntimeError'})
    result = adapter.classify(tmp_path, accepted=False)
    assert result['pause_batch']
    assert len(result['flow_errors']) == 1
