"""Offline contract tests; no native geometry, simulation or model requests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from adsl.agents.agent_feedback import (
    AgentFeedbackError, evaluation_details, finding_for_agent, payload_for_agent,
)
from adsl.agents.utils.request_errors import classify_model_request_error


class RequestFailure(Exception):
    def __init__(self, status, code=None, message='request rejected'):
        super().__init__(message)
        self.status_code = status
        self.body = {'error': {'code': code, 'message': message}}


@pytest.mark.parametrize('status,code,kind,retry', [
    (400, 'context_too_large', 'context_limit', False),
    (400, 'context_length_exceeded', 'context_limit', False),
    (413, None, 'context_limit', False),
    (400, 'invalid_schema', 'request_rejected', False),
    (400, None, 'request_rejected', False),
    (422, None, 'request_rejected', False),
    (401, None, 'shared_fault', False),
    (403, None, 'shared_fault', False),
    (429, 'insufficient_quota', 'shared_fault', False),
    (429, 'rate_limit_exceeded', 'transient', None),
    (408, 'request_timeout', 'transient', None),
    (503, None, 'transient', None),
])
def test_request_classification(status, code, kind, retry):
    row = classify_model_request_error(RequestFailure(status, code))
    assert row['kind'] == kind and row['code'] == code
    assert row['retryable_same_input'] is retry
    assert set(row) == {'kind', 'code', 'status_code', 'request_id', 'reason', 'retryable_same_input'}
    assert classify_model_request_error(TypeError('internal bug')) is None


@pytest.mark.parametrize('code,stop', [('context_too_large', 'agent_input_too_large'),
    ('invalid_schema', 'agent_request_rejected'), (None, 'agent_request_rejected')])
def test_engineering_request_rejection_stops_before_coder(tmp_path, monkeypatch, code, stop):
    from test_mesh_evaluation_feedback import setup_evaluation_flow, failure
    from test_assembly_feedback_recovery import payload
    from test_fixed_assembly import run_flow
    from adsl.agents.service import ObjectWorkflow
    state, executed, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure(), rounds=5)
    monkeypatch.setattr(state[0], '_repair', ObjectWorkflow._repair.__get__(state[0]))
    calls = []
    async def runtime(**kwargs):
        calls.append(kwargs['role'])
        assert 'evaluation_feedback' in payload(kwargs)
        raise RequestFailure(400, code)
    state[2].run = runtime
    original = state[3].read_bytes()
    result, book = run_flow(state)
    assert not result.approved and book['stop_reason'] == stop
    assert len(calls) == 1 and calls[0].startswith('engineering')
    assert state[3].read_bytes() == original and len(executed) == 1
    assert not (tmp_path / 'repair_history.jsonl').exists()
    assert book['feedback']['request_error']['code'] == code
    assert book['next_round'] == 2 and book['completed']
    run_flow(state)
    assert len(calls) == 1


@pytest.mark.parametrize('error', [RequestFailure(429, 'insufficient_quota'), TypeError('internal defect')])
def test_engineering_shared_or_program_error_saved_then_propagated(tmp_path, monkeypatch, error):
    from test_mesh_evaluation_feedback import setup_evaluation_flow, failure
    from test_fixed_assembly import run_flow
    state, _, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure())
    async def runtime(**kwargs):
        raise error
    state[2].run = runtime
    with pytest.raises(type(error)) as caught:
        run_flow(state)
    assert caught.value is error
    book = json.loads((tmp_path / 'assembly_versions.json').read_text())
    assert 'original' in book['versions'] and book['feedback']['engineering']['status'] == 'UNAVAILABLE'
    assert book['next_round'] == 2 and not book['completed']


def test_coder_request_error_keeps_reservation_and_tool_error_contract(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_mesh_evaluation_feedback import setup_evaluation_flow, failure
    from test_fixed_assembly import run_flow
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.models import EngineeringCriticDecision
    state, _, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure())
    monkeypatch.setattr(state[0], '_repair', ObjectWorkflow._repair.__get__(state[0]))
    calls = []
    async def runtime(**kwargs):
        calls.append(kwargs['role'])
        if kwargs['role'].startswith('engineering'):
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['No safe change'], repair_proposals=[]))
        raise RequestFailure(400, 'context_too_large')
    state[2].run = runtime
    result, book = run_flow(state)
    assert not result.approved and len(calls) == 2
    outcome = book['versions']['attempt_0001']['reviews']['edit_outcome']
    assert outcome['status'] == 'TOOL_ERROR' and outcome['request_error']['kind'] == 'context_limit'
    assert outcome['before_sha256'] == outcome['after_sha256']
    assert book['stop_reason'] == 'agent_input_too_large'
    assert len((tmp_path / 'repair_history.jsonl').read_text().splitlines()) == 1
    assert (tmp_path / outcome['error_evidence_ref']['path']).is_file()


def test_assigned_source_read_requires_exact_successful_path(tmp_path):
    from adsl.agents.tools import AgentToolContext
    from adsl.agents.service import _has_assigned_source_read
    source = tmp_path / 'candidate/source.py'
    source.parent.mkdir()
    source.write_text('x = 1')
    context = AgentToolContext(workspace=tmp_path, source_path=source)
    context.record('read_file', tmp_path / 'report.json')
    assert not _has_assigned_source_read(context)
    context.record('read_file', source, success=False)
    assert not _has_assigned_source_read(context)
    context.record('read_file', source)
    assert _has_assigned_source_read(context)


def test_typed_evaluation_association_from_written_original_pointer(tmp_path):
    source, rows, ref, payload = inputs(tmp_path, 2)
    result = tmp_path / 'result.json'
    original = [{'finding_id': f'assembly_mesh_evaluation:{i}:body_0:TARGET_PRECISION_UNREPRESENTABLE',
        'rule_id': 'TARGET_PRECISION_UNREPRESENTABLE', 'category': 'geometry_failure',
        'repairability': 'geometry', 'required': False, 'domain': row} for i, row in enumerate(rows)]
    result.write_text(json.dumps({'assumptions': {'source_sha256':ref['source_sha256']}, 'findings': original}))
    payload['typed_findings'] = [finding_for_agent(f, result_ref=str(result),
        result_pointer=f'/findings/{i}', source_sha256=ref['source_sha256']) for i, f in enumerate(original)]
    projected = project(tmp_path, source, ref, payload)
    feedback = projected['evaluation_feedback']
    assert feedback['failure_count'] == 3 and feedback['unique_failure_count'] == 1
    group, = feedback['failures']
    assert group['finding_ids'] == [f['finding_id'] for f in original]
    assert all(f['failure_id'] == group['failure_id'] for f in projected['typed_findings'])
    assert all(f['association_status'] == 'RESOLVED' for f in projected['typed_findings'])
    assert projected == project(tmp_path, source, ref, projected)


def test_normalized_old_finding_unavailable_domain_not_guessed(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 1)
    payload['typed_findings'] = [{'finding_id': 'assembly_mesh_evaluation:0:body:err',
        'rule_id': 'TARGET_PRECISION_UNREPRESENTABLE', 'result_ref': 'missing.json',
        'result_pointer': '/findings/0'}]
    out = project(tmp_path, source, ref, payload)
    assert out['typed_findings'][0]['association_status'] == 'UNRESOLVED'
    assert 'failure_id' not in out['typed_findings'][0]


def test_four_actual_role_boundaries_share_bounded_evidence(tmp_path, monkeypatch):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    from adsl.agents import assembly_topology as adapter
    from adsl.agents.models import (EngineeringCriticDecision, FixedAssemblyPlan,
        GradedImageCriticDecision, GradedCodeCriticDecision)
    from adsl.agents.service import ObjectWorkflow, _checker_evidence
    from adsl.agents.utils.execution import ExecutionResult
    from test_fixed_assembly import mock_flow, plan_data
    from test_assembly_feedback_recovery import payload, tool
    from adsl.agents.tools.files import read_file
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    workflow, request, runtime, _, _ = state
    source, rows, ref, raw = inputs(tmp_path, 1000)
    # A report and its later evaluation finding are real files; native/model work
    # is stubbed. The same large source-bound evidence reaches all four roles.
    report = {'source_sha256':ref['source_sha256'], 'failures':rows,
              'part_declarations':[{'id':'body_0', 'components':[]}]}
    feedback = {**raw['feedback'],
        'geometry_report_ref':ref, 'source_version':'original'}
    reference = tmp_path/'reference.png'; reference.write_bytes(b'unchanged reference')
    render = tmp_path/'view.png'; render.write_bytes(b'unchanged render')
    request = replace(request, image_paths=(reference,))
    execution = ExecutionResult(tmp_path, tmp_path/'scene.glb', None, (render,), '', '')
    captures = {}
    pointer_reads = []
    async def model(**kwargs):
        data = payload(kwargs)
        role = kwargs['stage'].split(':')[0]
        captures[role] = data
        assert len(json.dumps(data, ensure_ascii=False).encode()) < 32768
        assert 'remaining_bad_faces"' not in json.dumps(data)
        if role == 'image_critic':
            # This call occurs before engineering's result.json is requested.
            assert not (tmp_path/'evaluation_feedback/result.json').exists()
            evidence = data['evaluation_feedback']['failures'][0]['evidence_refs'][0]
            assert evidence['path'] == 'manifest.json'
            return SimpleNamespace(final_output=GradedImageCriticDecision(approved=True,
                observations=['stub'], issues=[]))
        if role == 'code_critic':
            await tool(read_file, kwargs['context'], 'source', path=data['assigned_source'])
            return SimpleNamespace(final_output=GradedCodeCriticDecision(approved=True,
                observations=['stub'], issues=[]))
        if role == 'assembly_engineering':
            finding = data['typed_findings'][0]
            content = await tool(read_file, kwargs['context'], 'report_metric',
                path=finding['result_ref'], json_pointer=finding['result_pointer']+'/domain/diagnostic/internal_metrics')
            assert json.loads(content)['valid'] is True
            pointer_reads.append({'path':finding['result_ref'],
                'json_pointer':finding['result_pointer']+'/domain/diagnostic/internal_metrics',
                'read_result':json.loads(content)})
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['No safe proposal; stop'], repair_proposals=[]))
        assert role == 'repair'
        return SimpleNamespace(final_output='NO_CHANGE: bounded source unchanged')
    runtime.run = model
    async def exercise():
        root = tmp_path/'round'; root.mkdir()
        plan = FixedAssemblyPlan.model_validate(plan_data())
        context = raw['assembly_context']
        image = await ObjectWorkflow._review_generation_image(workflow,
            runtime=runtime, request=request, plan=plan, execution=execution,
            round_number=1, max_rounds=2, round_root=root, image_critic={}, image_history=[],
            code_critic_corrections=[], assembly_context=context, source_path=source, source_version='original')
        await ObjectWorkflow._review_generation_code(workflow, runtime=runtime, request=request,
            plan=plan, execution=execution, workspace=tmp_path, source_path=source,
            round_number=1, max_rounds=2, round_root=root, code_critic={}, image_decision=image,
            code_history=[], assembly_context=context)
        assert not (tmp_path/'evaluation_feedback/result.json').exists()
        from adsl.agents.fixed_assembly import _failure_feedback
        run = adapter.evaluation_evidence_run(report, _failure_feedback(report,
            source_sha256=ref['source_sha256']), source, tmp_path/'evaluation_feedback')
        feedback.update(_checker_evidence([run], workspace=tmp_path))
        await adapter.engineer(workflow, runtime, request, plan, source, execution,
            root, run, context, feedback, 1)
        outcome = await ObjectWorkflow._repair(workflow, runtime=runtime, repairer={},
            workspace=tmp_path, source_path=source, role='coder', stage='repair:1',
            payload={**raw, 'feedback':feedback, 'source_version':'original'}, allow_no_change=True)
        assert outcome['status'] == 'NO_CHANGE'
    asyncio.run(exercise())
    assert set(captures) == {'image_critic', 'code_critic', 'assembly_engineering', 'repair'}
    identities = [value['evaluation_feedback']['failures'][0]['failure_id'] for value in captures.values()]
    assert len(set(identities)) == 1
    assert reference.read_bytes() == b'unchanged reference' and render.read_bytes() == b'unchanged render'
    for role, value in captures.items():
        (tmp_path/f'captured_{role}.json').write_text(json.dumps(value, ensure_ascii=False, allow_nan=False))
    (tmp_path/'capture_evidence.json').write_text(json.dumps({'workspace':str(tmp_path),
        'raw_payload_utf8_bytes':len(json.dumps(raw, ensure_ascii=False).encode()),
        'roles':{role:{'text_utf8_bytes':len(json.dumps(value, ensure_ascii=False).encode()),
            'unique_failure_count':value['evaluation_feedback']['unique_failure_count']}
            for role,value in captures.items()}, 'pointer_reads':pointer_reads}, allow_nan=False))


def test_original_stale_binding_not_relabelled_by_current_report(tmp_path):
    source, rows, ref, _ = inputs(tmp_path, 1)
    old = deepcopy(rows[0])
    old.update(source_sha256='0' * 64, source_version='attempt_0001')
    out = project(tmp_path, source, ref, {'feedback':{'failure_feedback':[old]}})
    evidence, = out['evaluation_feedback']['failures'][0]['evidence_refs']
    assert evidence['source_sha256'] == '0' * 64
    assert evidence['source_version'] == 'attempt_0001'
    assert evidence['source_binding'] == 'STALE' and evidence['matches_current_source'] is False


def test_report_only_read_cannot_support_source_proposal(tmp_path, monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from adsl.agents import assembly_topology as adapter
    from adsl.agents.fixed_assembly import _failure_feedback
    from adsl.agents.models import EngineeringCriticDecision, FixedAssemblyPlan, RepairProposal, RepairTarget
    from adsl.agents.service import _checker_evidence
    from test_fixed_assembly import mock_flow, plan_data
    from test_assembly_feedback_recovery import payload, tool
    from adsl.agents.tools.files import read_file
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    workflow, request, runtime, _, _ = state
    source, rows, ref, raw = inputs(tmp_path, 1)
    report = json.loads((tmp_path/'manifest.json').read_text())
    run = adapter.evaluation_evidence_run(report, _failure_feedback(report,
        source_sha256=ref['source_sha256']), source, tmp_path/'evidence')
    feedback = {**_checker_evidence([run], workspace=tmp_path),
                'source_version':'original', 'geometry_report_ref':ref}
    async def model(**kwargs):
        data = payload(kwargs)
        finding = data['typed_findings'][0]
        await tool(read_file, kwargs['context'], 'report_only', path=finding['result_ref'],
                   json_pointer=finding['result_pointer']+'/metric')
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
            observations=['proposal'], repair_proposals=[RepairProposal(proposal_id='test',
                action='reshape', finding_ids=[finding['finding_id']], hypothesis='local change', target=RepairTarget())]))
    runtime.run = model
    with pytest.raises(ValueError, match='reading assigned_source'):
        asyncio.run(adapter.engineer(workflow, runtime, request, FixedAssemblyPlan.model_validate(plan_data()),
            source, None, tmp_path/'engineering', run, raw['assembly_context'], feedback, 1))


def test_local_feedback_build_failure_never_calls_model(tmp_path, monkeypatch):
    import asyncio
    from adsl.agents import service
    from test_fixed_assembly import mock_flow
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    workflow, _, runtime, source, _ = state
    async def forbidden(**kwargs):
        pytest.fail('feedback construction failed before runtime.run')
    runtime.run = forbidden
    def invalid(*args, **kwargs):
        raise AgentFeedbackError('AGENT_FEEDBACK_INVALID', {'reason':'invalid_source_binding'})
    monkeypatch.setattr(service, 'payload_for_agent', invalid)
    outcome = asyncio.run(service.ObjectWorkflow._repair(workflow, runtime=runtime, repairer={},
        workspace=tmp_path, source_path=source, role='coder', stage='repair', payload={}, allow_no_change=True))
    assert outcome['status'] == 'TOOL_ERROR'
    assert outcome['request_error']['kind'] == 'input_construction'
    assert outcome['before_sha256'] == outcome['after_sha256']


def test_unbound_candidate_geometry_cannot_authorize_repair(tmp_path):
    from adsl.agents.fixed_assembly import _failure_feedback, _bound_manifest
    from adsl.agents.overhang_edit import file_hash, version_record
    source = tmp_path/'source.py'; source.write_text('scene = None')
    row = {'status':'FAIL', 'failures':[{'code':'DISCONNECTED_PRINT_PART', 'part_id':'leg'}]}
    for binding in ({}, {'source_sha256':'0' * 64}):
        report = {**row, **binding}
        projected = _bound_manifest(report, tmp_path/'manifest.json', source, {})
        assert projected['source_binding'] in {'STALE', 'UNKNOWN'}
        assert not _failure_feedback(projected, source_sha256=file_hash(source))[0]['geometry_repair_allowed']
        assert report == {**row, **binding}
    manifest = tmp_path/'manifest.json'; manifest.write_text(json.dumps(row))
    version = version_record('original', source, None, extra_files=[manifest])
    projected = _bound_manifest(row, manifest, source, {'original':version})
    assert projected['source_sha256'] == file_hash(source)
    assert projected['source_binding_basis'] == 'verified_version_files'


def test_critic_context_rejection_uses_terminal_stop_without_followup(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from test_mesh_evaluation_feedback import setup_evaluation_flow, failure
    from test_fixed_assembly import run_flow
    state, _, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure())
    async def critic(**kwargs):
        raise RequestFailure(400, 'context_too_large')
    async def image(**kwargs):
        return SimpleNamespace(approved=False)
    async def forbidden(**kwargs):
        pytest.fail('critic request rejection must prevent engineering/coder')
    monkeypatch.setattr(state[0], '_review_generation_code', critic)
    monkeypatch.setattr(state[0], '_review_generation_image', image)
    state[2].run = forbidden
    result, book = run_flow(state)
    assert not result.approved and book['stop_reason'] == 'agent_input_too_large'
    assert book['feedback']['request_error']['kind'] == 'context_limit'
    assert book['versions']['original']['reviews']['geometry_report_ref']['source_version'] == 'original'


def test_nested_history_preview_has_original_full_pointer_and_provenance(tmp_path):
    source, _, ref, _ = inputs(tmp_path, 1)
    history = [{'approved':False, '_history_meta':{'source_sha256':'0' * 64,
        'source_version':'attempt_0001', 'round':2}, 'issues':[
            {'severity':'HIGH', 'aspect':'geometry', 'problem':f'problem {i}',
             'suggested_fix':'inspect', 'details':diagnostic(2)} for i in range(12)]}]
    out = project(tmp_path, source, ref, {'previous_image_decisions':history})
    record = out['previous_image_decisions'][0]
    full = record['preview_info']['issues']['full_ref']
    assert len(record['issues']) == 3 and record['preview_info']['issues']['total'] == 12
    assert record['_history_meta'] == history[0]['_history_meta']
    document = json.loads((tmp_path/full['path']).read_text())
    assert full['json_pointer'] == '/history/0/issues' and len(document['history'][0]['issues']) == 12
    assert project(tmp_path, source, ref, out) == out


def test_legacy_nonfinite_original_stays_in_file_only(tmp_path):
    source, _, _, _ = inputs(tmp_path, 1)
    raw = {'feedback':{'failure_feedback':[{'code':'INPUT_GEOMETRY_INVALID',
        'stage':'input_geometry', 'metrics':{'volume':float('nan')}}]}}
    out = project(tmp_path, source, None, raw)
    group, = out['evaluation_feedback']['failures']
    assert group['internal_metrics']['volume'] is None
    assert group['internal_metrics']['value_status'] == 'NONFINITE'
    evidence, = group['evidence_refs']
    assert 'NaN' in (tmp_path/evidence['path']).read_text()
    json.dumps(out, allow_nan=False)
    assert out == project(tmp_path, source, None, out)


def diagnostic(count=10000):
    return {'code': 'TARGET_PRECISION_UNREPRESENTABLE', 'stage': 'target_precision',
        'node_path': 'body/arm/union', 'operation': 'UNION', 'input_count': 3,
        'internal_metrics': {'valid': True, 'zero_area_triangles': 0, 'boundary_edges': 0},
        'displacement_budget_mm': 0.00014, 'local_scale_mm': 10,
        'absolute_scale_floor_mm': 0.001,
        'attempts': [{'method': 'local_precision_edge_repair', 'status': 'REJECTED',
            'diagnostic': {'repair': {'method': 'edge_collapse', 'status': 'REJECTED',
                'remaining_bad_faces_total': 8, 'target_zero_normals': 8,
                'remaining_bad_faces': [{'vertices': [[1, 2, 3]] * 3, 'neighbors': list(range(8))}] * count}}}
            for _ in range(3)],
        'operation_nodes': [{'node_path': f'body/arm/{i}'} for i in range(100)]}


def inputs(tmp_path, count=10000, groups=1):
    source = tmp_path / 'candidate' / 'source.py'
    source.parent.mkdir(exist_ok=True)
    source.write_text('scene = None\n')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    rows = [{'part_id': f'body_{i}', 'diagnostic': diagnostic(count),
        'failure_kind': 'candidate_evaluation', 'geometry_repair_allowed': True,
        'output_role': 'display', 'file': file}
        for i in range(groups) for file in (f'body_{i}.glb', 'scene.glb', 'exploded.glb')]
    report = tmp_path / 'manifest.json'
    report.write_text(json.dumps({'source_sha256': sha, 'failures': rows}))
    ref = {'path': str(report), 'source_sha256': sha, 'source_version': 'original'}
    payload = {'requirement': 'keep this task', 'source_version': 'original',
        'assembly_context': {'failure_feedback': rows, 'report_ref': ref,
                             'current_assembly': {'parts': [{'id': 'body_0'}]}},
        'evaluation_failures': rows,
        'feedback': {'failures': rows, 'failure_feedback': rows,
            'resolved_visual_feedback': {'approved': False},
            'edit_restriction': 'keep scope', 'partition_guidance': {'current': {'N': 3}}}}
    return source, rows, ref, payload


def project(tmp_path, source, ref, payload, role='engineering'):
    return payload_for_agent(payload, workspace=tmp_path, source_path=source,
        source_version='original', role=role, report_ref=ref)


def test_details_formats_preserve_false_zero_empty_and_complete_nodes():
    raw = diagnostic(10)
    assert evaluation_details(raw) == evaluation_details({'diagnostic': raw})
    details = evaluation_details({'diagnostic': {'metrics': {}, 'input_count': 0,
        'attempts': [], 'operation_nodes': []}, 'metrics': {'valid': False}, 'attempts': [1]})
    assert details['metrics'] == {} and details['input_count'] == 0
    assert details['attempts'] == [] and details['operation_nodes'] == []
    assert evaluation_details({})['attempts'] is None
    assert len(evaluation_details(raw)['operation_nodes']) == 100


@pytest.mark.parametrize('role', ['engineering', 'code_critic', 'image_critic', 'coder'])
def test_huge_raw_payload_deduplicated_and_idempotent(tmp_path, role):
    source, rows, ref, raw = inputs(tmp_path)
    original = deepcopy(raw)
    projected = project(tmp_path, source, ref, raw, role)
    text = json.dumps(projected, ensure_ascii=False, allow_nan=False)
    assert len(text.encode()) < 32768
    assert 'remaining_bad_faces"' not in text and 'neighbors' not in text
    feedback = projected['evaluation_feedback']
    assert feedback['failure_count'] == 3 and feedback['unique_failure_count'] == 1
    group, = feedback['failures']
    assert group['occurrence_count'] == 3 and group['attempt_count'] == 3
    assert group['internal_metrics']['valid'] is True
    assert group['attempts_summary'][0]['repair']['remaining_bad_faces_total'] == 8
    assert group['displacement_budget_mm'] == .00014
    assert raw == original
    assert projected == project(tmp_path, source, ref, projected, role)
    assert projected['feedback']['resolved_visual_feedback'] == raw['feedback']['resolved_visual_feedback']
    for evidence in group['evidence_refs']:
        assert evidence['source_binding'] == 'CURRENT'
        document = json.loads((tmp_path / evidence['path']).read_text())
        assert document['failures'][int(evidence['json_pointer'].split('/')[-1])] in rows


def test_growth_of_detail_arrays_does_not_grow_summary(tmp_path):
    source, rows, ref, payload = inputs(tmp_path, 100)
    first = project(tmp_path, source, ref, payload)
    source, rows, ref, payload = inputs(tmp_path, 1000)
    second = project(tmp_path, source, ref, payload)
    assert abs(len(json.dumps(first)) - len(json.dumps(second))) < 100


def test_distinct_parts_and_canonical_full_index(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 10, groups=8)
    out = project(tmp_path, source, ref, payload)
    feedback = out['evaluation_feedback']
    assert feedback['unique_failure_count'] == 8 and feedback['failure_count'] == 24
    assert len(feedback['failures']) == 6
    full = feedback['preview_info']['failures']['full_ref']
    doc = json.loads((tmp_path / full['path']).read_text())
    assert len(doc['failures']) == 8
    assert out == project(tmp_path, source, ref, out)


def test_finding_contract_and_nonfinite_measurement():
    raw = {'finding_id': 'test', 'rule_id': 'R', 'required': True, 'repairability': 'geometry',
        'category': 'geometry_failure', 'applicability': 'applicable', 'applicability_basis': 'measured',
        'metric': {'name': 'volume', 'value': float('nan'), 'unit': 'mm3', 'threshold': 0},
        'region': {'kind': 'aabb', 'frame': 'assembly', 'unit': 'mm',
            'bounds': [[0, 0, 0], [1, 1, 1]], 'layer_range': [0, 2], 'details': diagnostic(10)},
        'source_candidates': [{'feature_id': 'a', 'source_ids': list('abcdef'),
            'source_locations': [f'x:{i}' for i in range(6)], 'method': 'direct',
            'ambiguous': True, 'relation_role': 'tab', 'overlap_score': .2, 'evidence': ['large'] * 1000}],
        'domain': diagnostic(10)}
    out = finding_for_agent(raw, result_ref='result.json', result_pointer='/findings/2', source_sha256=None)
    assert out['metric']['value'] is None and out['metric']['value_status'] == 'NONFINITE'
    assert out['metric']['name'] == 'volume'
    assert out['region']['layer_range'] == [0, 2]
    assert out['applicability_basis'] == 'measured'
    c, = out['source_candidates']
    assert c['source_ids'] == list('abc') and c['source_locations'] == ['x:0', 'x:1', 'x:2']
    assert c['ambiguous'] and c['relation_role'] == 'tab' and c['overlap_score'] == .2
    assert 'attempts' not in json.dumps(out)
    json.dumps(out, allow_nan=False)


def test_evidence_unavailable_stale_unknown_and_no_source_guess(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 1)
    source.write_text('changed = True\n')
    out = project(tmp_path, source, ref, payload)
    evidence = out['evaluation_feedback']['failures'][0]['evidence_refs'][0]
    assert evidence['source_binding'] == 'STALE' and evidence['matches_current_source'] is False
    payload = {'evidence_files': [{'path': '../outside.json'}, {'path': 'missing.json'}]}
    out = payload_for_agent(payload, workspace=tmp_path, source_path=None,
                            source_version=None, role='image_critic')
    assert out['evidence_files'][0]['reason'] == 'outside_workspace'
    assert out['evidence_files'][1]['source_binding'] == 'UNKNOWN'
    assert out['evidence_files'][1]['matches_current_source'] is None


def test_missing_attempt_count_and_old_book_snapshot(tmp_path):
    source, _, ref, _ = inputs(tmp_path, 1)
    payload = {'feedback': {'failure_feedback': [{'code': 'INPUT_GEOMETRY_INVALID', 'stage': 'input_geometry'}]}}
    out = project(tmp_path, source, None, payload)
    group, = out['evaluation_feedback']['failures']
    assert group['attempt_count'] is None
    evidence, = group['evidence_refs']
    assert evidence['source_binding'] == 'UNKNOWN' and evidence['matches_current_source'] is None
    assert (tmp_path / evidence['path']).is_file()
    files = list(tmp_path.rglob('feedback_snapshot_*'))
    assert out == project(tmp_path, source, None, out)
    assert files == list(tmp_path.rglob('feedback_snapshot_*'))


def test_image_data_excluded_from_feedback_budget(tmp_path):
    source, _, ref, payload = inputs(tmp_path, 1)
    payload['images'] = ['data:image/png;base64,' + 'a' * 1000000] * 8
    assert project(tmp_path, source, ref, payload)['images'] == payload['images']


def test_required_facts_over_budget_fail_without_raw_fallback(tmp_path):
    payload = {'checker_summary': [{'status': 'FAIL', 'required': True, 'rule_id': 'x' * 200}] * 300}
    with pytest.raises(AgentFeedbackError) as caught:
        payload_for_agent(payload, workspace=tmp_path, source_path=None, source_version=None, role='engineering')
    assert caught.value.code == 'AGENT_FEEDBACK_TOO_LARGE'
