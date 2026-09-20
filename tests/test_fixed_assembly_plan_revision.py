"""Declaration revisions and source-bound review inputs; no API or CSG runs."""
import asyncio
from dataclasses import replace
import importlib
import json

import numpy as np
import pytest
import trimesh

from adsl.core import Asset, Cube, FixedAssembly, InterfaceFrame
from adsl.agents import fixed_assembly as flow
from adsl.agents.models import FixedAssemblyPlan
from test_fixed_assembly import mock_flow, params, plan_data, run_flow
from test_generation_review_contract import real_review_state, unpack

exporter = importlib.import_module('adsl.core.export.export_assembly')
LINK_FIELDS = ('id', 'tab_part', 'slot_part', 'tab_port', 'slot_port', 'parameter_name')


def chair(split=False):
    legs = Asset()
    children = [legs.attach_part(f'leg{i}', Cube((5, 5, 40), center=(i*15, 0, -20)))
                for i in range(4)]
    assembly = FixedAssembly(root_id='seat', mm_per_unit=1)
    assembly.add_part('seat', Cube((80, 40, 10)), components=('Seat',))
    pieces = [(f'leg{i}', child, [f'Legs/leg{i}']) for i, child in enumerate(children)] if split else [
        ('legs', legs, ['Legs'])]
    for i, (name, body, components) in enumerate(pieces):
        assembly.add_part(name, body, components=components)
        assembly.connect(f'{name}_joint', tab_part=name, slot_part='seat',
            tab_frame=InterfaceFrame(), slot_frame=InterfaceFrame((i*15, 0, 0)),
            slot_port=f'slot{i}', parameter_name='shared', parameters=params())
    assembly.validate()
    return assembly


def initial_plan():
    a = chair()
    return {'root_part':a.root_id,
            'print_parts':[{'id':k, 'components':list(v)} for k,v in a.components.items()],
            'connections':[{k:c[k] for k in LINK_FIELDS} for c in a.connections]}


@pytest.fixture
def fake_export(monkeypatch):
    # Exercise real declaration/contract handling; mesh evaluation/serialization
    # have separate export regressions and are not geometry evidence in this test.
    def evaluated(*args, **kwargs):
        return trimesh.creation.box(), None, {'display_complete':True, 'omitted_mesh_nodes':[]}
    monkeypatch.setattr(exporter, 'evaluated', evaluated)
    monkeypatch.setattr(exporter, '_diagnostic_view', lambda a,o,m,r:r.update(
        diagnostic={'display_available':True}))
    monkeypatch.setattr(exporter, '_write_final_meshes', lambda *a, **k:None)
    monkeypatch.setattr(exporter, '_verify_written_exports', lambda *a, **k:None)
    return {'validation_mode':'visual_only', 'mm_per_unit':1, 'fit_offset_mm':.2,
            'assembly_plan':initial_plan()}


@pytest.mark.parametrize('split', [False, True])
def test_grouping_revision_is_trace_not_hard_failure(tmp_path, fake_export, split):
    before = json.dumps(fake_export['assembly_plan'], sort_keys=True)
    report = exporter.export_assembly(chair(split), tmp_path, source_sha256='source', expected=fake_export)
    assert report['export_status'] == 'PASS' and report['failures'] == []
    assert report['status'] == 'NOT_EVALUATED'  # Not manufacturing approval.
    delta = report['plan_changes']
    assert delta['status'] == ('CHANGED' if split else 'UNCHANGED')
    assert json.dumps(fake_export['assembly_plan'], sort_keys=True) == before
    saved = json.loads((tmp_path/'assembly_manifest.json').read_text())
    assert saved == json.loads(json.dumps(report))
    assert len(saved['part_declarations']) == (5 if split else 2)
    assert len(saved['connections']) == (4 if split else 1)
    assert all(c['parameters']['fit_offset_mm'] == .2 and len(c['tab_frame']) == 4
               and len(c['slot_frame']) == 4 and 'tab_solid' not in c for c in saved['connections'])
    if split:
        assert set(delta['codes']) == {'PART_MEMBERSHIP_CHANGED', 'CONNECTION_PLAN_CHANGED'}
        assert [p['id'] for p in delta['parts']['removed']] == ['legs']
        assert [p['id'] for p in delta['parts']['added']] == [f'leg{i}' for i in range(4)]
        assert len(delta['connections']['added']) == 4 and len(delta['connections']['removed']) == 1
        assert all(p['components'] == [f"Legs/{p['id']}"] for p in saved['part_declarations'][1:])
    else:
        assert delta['codes'] == [] and not any(delta['parts'].values())


@pytest.mark.parametrize('required, count', [(None,1),(False,1),(True,1),(True,2),(True,5)])
def test_multiple_parts_is_only_an_explicit_task_floor(tmp_path, fake_export, required, count):
    if required is not None:
        fake_export['require_multiple_parts'] = required
    if count == 1:
        a = FixedAssembly(root_id='seat', mm_per_unit=1)
        a.add_part('seat', Cube(5), components=('Seat','Legs'))
    else:
        a = chair(split=count == 5)
    a.validate()  # A single print part remains legal in the general API.
    report = exporter.export_assembly(a, tmp_path, source_sha256='source', expected=fake_export)
    rejected = required is True and count == 1
    assert report['export_status'] == ('FAIL' if rejected else 'PASS')
    assert report['status'] == 'NOT_EVALUATED'
    assert all(f['code'] == 'MULTIPART_ASSEMBLY_REQUIRED' for f in report['failures'])
    if rejected:
        failure, = report['failures']
        assert failure['part_count'] == 1 and failure['connection_count'] == 0
    assert len(report['part_declarations']) == count
    if count != 2:
        assert report['plan_changes']['status'] == 'CHANGED'  # Still a diff, not the rejection reason.


def test_image_approval_cannot_bypass_explicit_multipart_task(tmp_path, monkeypatch, fake_export):
    state, calls = real_review_state(tmp_path, monkeypatch, [('NOT_EVALUATED',True)])
    state = (state[0], replace(state[1], max_rounds=1,
        fixed_assembly={**state[1].fixed_assembly,'require_multiple_parts':True}), *state[2:])
    execute = flow.execute_asset_source
    def merged_candidate(source, *args, **kwargs):
        result = execute(source, *args, **kwargs)
        a = FixedAssembly(root_id='crossbar', mm_per_unit=1)
        a.add_part('crossbar', Cube(5), components=('crossbar','stem'))
        exporter.export_assembly(a, result.output_root/'assembly', source_sha256=flow.file_hash(source),
                                 expected=kwargs['fixed_assembly'])
        return result
    monkeypatch.setattr(flow, 'execute_asset_source', merged_candidate)
    result, book = run_flow(state)
    assert not result.approved and book['retained'] == 'original' and book['qualified'] is None
    assert len(calls) == 1 and calls[0]['stage'] == 'image_critic:1'  # No extra Code/Planner call.
    payload, _ = unpack(calls[0])
    assert payload['assembly_context']['task_constraints'] == {'require_multiple_parts':True}
    assert (tmp_path/'source.py').read_text() == (tmp_path/'scene.glb').read_text() == 'original'
    final = json.loads((tmp_path/'assembly_result.json').read_text())
    assert final['visual_code_approved'] is True and final['approved'] is False
    assert final['reviews']['geometry']['failures'][0]['code'] == 'MULTIPART_ASSEMBLY_REQUIRED'


def test_changed_declarations_and_failed_part_still_record_actual_structure(tmp_path, fake_export, monkeypatch):
    fake_export['assembly_plan']['print_parts'][0]['components'] = ['OldSeatName']
    fake_export['assembly_plan']['connections'][0]['slot_port'] = 'old_slot'
    def unavailable(*args, **kwargs):
        raise ValueError('simulated unavailable mesh')
    monkeypatch.setattr(exporter, 'evaluated', unavailable)
    report = exporter.export_assembly(chair(), tmp_path, source_sha256='source', expected=fake_export)
    assert report['export_status'] == 'FAIL'
    assert report['parts'] == [] and len(report['part_declarations']) == 2
    assert report['plan_changes']['parts']['changed'][0]['actual']['components'] == ['Seat']
    assert report['plan_changes']['connections']['changed'][0]['actual']['slot_port'] == 'slot0'
    assert {f['code'] for f in report['failures']} == {'PART_DISPLAY_UNAVAILABLE'}


@pytest.mark.parametrize('field, value, code', [
    ('root_part', 'legs', 'ROOT_CHANGED'), ('mm_per_unit', 2, 'SCALE_CHANGED'),
    ('fit_offset_mm', .3, 'FIT_ALLOWANCE_CHANGED')])
def test_frozen_constraints_remain_hard_failures(tmp_path, fake_export, field, value, code):
    target = fake_export['assembly_plan'] if field == 'root_part' else fake_export
    target[field] = value
    report = exporter.export_assembly(chair(True), tmp_path, source_sha256='source', expected=fake_export)
    assert report['export_status'] == 'FAIL'
    assert code in {f['code'] for f in report['failures']}


@pytest.mark.parametrize('invalid', ['unknown_part', 'occupied_port', 'parameter_mismatch', 'bad_size'])
def test_regroup_does_not_bypass_existing_connect_legality(invalid):
    a = chair(True)
    a.add_part('extra', Cube(2), components=['Extra'])
    kw = dict(tab_part='extra', slot_part='seat', tab_frame=InterfaceFrame(),
              slot_frame=InterfaceFrame(), slot_port='extra_slot', parameter_name='shared', parameters=params())
    if invalid == 'unknown_part': kw['slot_part'] = 'missing'
    if invalid == 'occupied_port': kw['slot_port'] = 'slot0'
    if invalid == 'parameter_mismatch': kw['parameters'] = params(width_mm=13)
    with pytest.raises(ValueError):
        if invalid == 'bad_size': kw['parameters'] = params(width_mm=0)
        a.connect('extra_joint', **kw)


@pytest.mark.parametrize('report', [None, {'source_sha256':'old', 'connections':[{'id':'stale'}]}])
def test_missing_or_stale_manifest_never_asserts_previous_structure(tmp_path, report):
    source = tmp_path/'source.py'; source.write_text('current')
    context = flow._assembly_context(source, report, version_role='current_candidate')
    assert context['current_assembly'] is None and context['plan_changes'] is None
    assert context['manifest_status'] == 'UNAVAILABLE_OR_SOURCE_MISMATCH'
    assert context['initial_plan_role'] == 'reference_proposal_not_immutable_implementation'


@pytest.mark.parametrize('final_approved', [False, True])
def test_real_request_context_repair_history_and_retained_match(tmp_path, monkeypatch, final_approved):
    state, calls = real_review_state(tmp_path, monkeypatch,
        [('NOT_EVALUATED', False), ('NOT_EVALUATED', False), ('NOT_EVALUATED', final_approved)])
    state = (state[0], replace(state[1], max_rounds=3), *state[2:])
    plan_path = tmp_path/'plan.json'
    original_plan = json.dumps(plan_data()); plan_path.write_text(original_plan)
    execute = flow.execute_asset_source
    def declared(source, *args, **kwargs):
        execution = execute(source, *args, **kwargs)
        path = execution.output_root/'assembly/assembly_manifest.json'
        report = json.loads(path.read_text())
        # Different recorded structure for each candidate, all bound to source.
        label = source.read_text()
        report.update(root_id='crossbar', part_declarations=[
            {'id':'crossbar', 'components':['crossbar'], 'assembly_transform':np.eye(4).tolist()},
            {'id':label, 'components':['stem'], 'assembly_transform':np.eye(4).tolist()}],
            connections=[dict(id='joint', tab_part=label, slot_part='crossbar')],
            plan_changes={'status':'CHANGED', 'codes':['PART_MEMBERSHIP_CHANGED', 'CONNECTION_PLAN_CHANGED']})
        path.write_text(json.dumps(report))
        return execution
    monkeypatch.setattr(flow, 'execute_asset_source', declared)
    result, book = run_flow(state)
    assert result.approved == final_approved and len(state[-1]) == 2
    assert not any('planner' in c['stage'] for c in calls)
    for call in calls:
        payload, _ = unpack(call)
        context = payload['assembly_context']
        expected_label = ['original', 'candidate1', 'candidate2'][payload['round']-1]
        assert context['current_assembly']['parts'][1]['id'] == expected_label
        assert context['plan_changes']['status'] == 'CHANGED'
        assert 'not an immutable' in context['instruction']
        assert context['version_role'] == 'current_candidate'
    for call, label in zip(state[-1], ['original', 'candidate1']):
        context = call['payload']['assembly_context']
        assert context['current_assembly']['connections'][0]['tab_part'] == label
        assert context['version_role'] == 'repair_starting_version'
    assert plan_path.read_text() == original_plan
    selected = 'candidate2' if final_approved else 'original'
    assert (tmp_path/'source.py').read_text() == (tmp_path/'scene.glb').read_text() == selected
    final = json.loads((tmp_path/'assembly_result.json').read_text())
    manifest = json.loads((tmp_path/'assembly/assembly_manifest.json').read_text())
    context = final['reviews']['assembly_context']
    assert context['current_assembly']['parts'][1]['id'] == selected
    assert context['current_assembly']['connections'] == manifest['connections']
    assert context['source_sha256'] == manifest['source_sha256'] == final['source_sha256'] == flow.file_hash(tmp_path/'source.py')
    assert final['version_id'] == book['retained'] == ('attempt_0002' if final_approved else 'original')


def test_optional_context_does_not_change_ordinary_generation(tmp_path, monkeypatch):
    state, calls = real_review_state(tmp_path, monkeypatch, [])
    monkeypatch.setattr(mock_flow, 'appearance', True, raising=False)
    w, request, runtime, source, _ = state
    request = replace(request, fixed_assembly=None)
    common = dict(runtime=runtime, request=request, plan=FixedAssemblyPlan.model_validate(plan_data()),
                  execution=None, round_number=1, max_rounds=3, round_root=tmp_path,
                  assembly_context={'must_not_leak':True})
    asyncio.run(w._review_generation_image(**common, image_critic={}, image_history=[], code_critic_corrections=[]))
    asyncio.run(w._review_generation_code(**common, workspace=tmp_path, source_path=source,
        code_critic={}, image_decision=None, code_history=[]))
    assert len(calls) == 2
    assert all('assembly_context' not in unpack(c)[0] for c in calls)
