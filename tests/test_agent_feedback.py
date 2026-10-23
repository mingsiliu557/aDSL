"""Offline contract tests; no native geometry, simulation or model requests."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path

import pytest

from adsl.agents.agent_feedback import (
    AgentFeedbackError, FEEDBACK_LIMIT_BYTES, evaluation_details, finding_for_agent, payload_for_agent,
)
from adsl.agents.utils.request_errors import classify_model_request_error


@pytest.mark.parametrize('role', ['engineering','coder','code_critic'])
def test_standing_observation_is_bounded_nonactionable_and_idempotent(tmp_path, role):
    from adsl.agents.service import _checker_evidence, _actionable_findings
    from adsl.agents.models import CheckerResult, CheckerSpec
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.assembly_standing import STANDING_PROTOCOL
    from adsl.agents.agent_feedback import feedback_size_report
    source=tmp_path/'source.py';source.write_text('scene=None')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    output=tmp_path/'standing';output.mkdir()
    observation=dict(status='PASS',observed_tipping=False,observed_interface_exit=False,
        peak_tilt_deg=.0012,final_tilt_deg=.0011,duration_s=5.)
    warning=dict(status='WARNING',code='INITIAL_CONTACT_DEPTH_EXCEEDS_REFERENCE',
        maximum_penetration_mm=.031309,reference_bound_mm=.020572,affects_standing_verdict=False)
    result=CheckerResult(checker='assembly_standing',status='PASS',summary='observation complete',
        metrics=dict(standing_observation=observation,contact_diagnostic=warning,
                     contacts=[{'vertices':[1,2,3]}]*1000),
        assumptions=dict(standing_protocol=STANDING_PROTOCOL,source_sha256=source_hash))
    (output/'result.json').write_text(result.model_dump_json())
    run=CheckerRun(CheckerSpec(name='assembly_standing',command=['stub']),result,output,())
    assert _actionable_findings(run)==[]
    raw={'feedback':_checker_evidence([run],workspace=tmp_path)}
    # Unknown nested data must not leak when a restored summary is projected.
    raw['feedback']['checker_summary'][0]['contact_diagnostic']['contacts']=['large']*1000
    before=deepcopy(raw)
    out=payload_for_agent(raw,workspace=tmp_path,source_path=source,source_version='original',role=role)
    row=out['feedback']['checker_summary'][0]
    assert row['standing_observation']==observation and row['contact_diagnostic']==warning
    assert row['result_pointer']=='/metrics' and row['result_ref']=='standing/result.json'
    assert out['feedback']['typed_findings']==[]
    assert 'vertices' not in json.dumps(out) and 'contacts' not in json.dumps(out)
    assert payload_for_agent(out,workspace=tmp_path,source_path=source,
        source_version='original',role=role)==out
    assert raw==before
    assert feedback_size_report(out)['actual_bytes']>len(json.dumps(warning))


def test_standing_summary_at_actual_engineering_coder_code_critic_boundaries(tmp_path,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from adsl.agents import assembly_topology as topology, assembly_physics as physics
    from adsl.agents.assembly_standing import STANDING_PROTOCOL
    from adsl.agents.models import FixedAssemblyPlan, EngineeringCriticDecision, GradedCodeCriticDecision, CheckerResult
    from adsl.agents.service import ObjectWorkflow, _checker_evidence
    from adsl.agents.checkers import CheckerRun
    from test_fixed_assembly import mock_flow, plan_data
    from test_assembly_feedback_recovery import payload as model_payload, tool
    from adsl.agents.tools.files import read_file
    workflow,request,runtime,source,_=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    out=tmp_path/'standing';out.mkdir()
    warning=dict(status='WARNING',code='INITIAL_CONTACT_DEPTH_EXCEEDS_REFERENCE',
        maximum_penetration_mm=.031309,reference_bound_mm=.020572,affects_standing_verdict=False)
    result=CheckerResult(checker='assembly_standing',status='PASS',summary='complete',
        metrics={'standing_observation':dict(status='PASS',observed_tipping=False,
            observed_interface_exit=False,peak_tilt_deg=.0012,final_tilt_deg=.0011,duration_s=5.),
            'contact_diagnostic':warning}, assumptions={'standing_protocol':STANDING_PROTOCOL,
                'source_sha256':hashlib.sha256(source.read_bytes()).hexdigest()})
    (out/'result.json').write_text(result.model_dump_json())
    check=CheckerRun(physics.checker_spec('assembly_standing'),result,out,())
    feedback={**_checker_evidence([check],workspace=tmp_path),'source_version':'original'}
    captured=[]
    async def run(**kw):
        data=model_payload(kw);stage=kw['stage'].split(':')[0];captured.append(stage)
        context=(data if stage=='assembly_engineering' else data['feedback'] if stage=='repair'
                 else data['assembly_context']['pending_reviews'])
        row=context['checker_summary'][0]
        assert row['contact_diagnostic']==warning
        contents=await tool(read_file,kw['context'],'contact',path=row['result_ref'],
            json_pointer=row['result_pointer']+'/contact_diagnostic')
        assert json.loads(contents)==warning
        if stage=='assembly_engineering':
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=True,
                observations=['Warning alone does not need repair'],repair_proposals=[]))
        if stage=='code_critic':
            await tool(read_file,kw['context'],'source',path=data['assigned_source'])
            return SimpleNamespace(final_output=GradedCodeCriticDecision(approved=True,observations=[],issues=[]))
        return SimpleNamespace(final_output='{"edit_action":"NO_CHANGE","reason":"No necessary edit"}')
    runtime.run=run
    root=tmp_path/'review';root.mkdir()
    plan=FixedAssemblyPlan.model_validate(plan_data())
    async def exercise():
        await topology.engineer(workflow,runtime,request,plan,source,None,root,check,{},feedback,1)
        await ObjectWorkflow._review_generation_code(workflow,runtime=runtime,request=request,
            plan=plan,execution=None,workspace=tmp_path,source_path=source,round_number=1,max_rounds=2,
            round_root=root,code_critic={},image_decision=None,code_history=[],
            assembly_context={'pending_reviews':{'checker_summary':feedback['checker_summary'],
                'evidence_role':'previous_candidate_physical_observation'}})
        result=await ObjectWorkflow._repair(workflow,runtime=runtime,repairer={},workspace=tmp_path,
            source_path=source,role='coder',stage='repair:1',allow_no_change=True,
            payload={'feedback':feedback,'source_version':'original'})
        assert result['status']=='NO_CHANGE'
    asyncio.run(exercise())
    assert captured==['assembly_engineering','code_critic','repair']


@pytest.mark.parametrize('reason_code', [None, 'INTERFACE_EXIT_FIRST',
    'REFERENCE_UNAVAILABLE', 'NO_POSITIVE_CONTRIBUTION', 'legacy'])
def test_tipping_localization_at_repair_boundaries_is_idempotent(tmp_path, monkeypatch, reason_code):
    import asyncio
    from types import SimpleNamespace
    from adsl.agents import assembly_topology as topology, assembly_physics as physics, fixed_assembly as flow
    from adsl.agents.agent_feedback import _tipping_localization_for_agent, feedback_size_report
    from adsl.agents.models import FixedAssemblyPlan, EngineeringCriticDecision, CheckerResult
    from adsl.agents.service import ObjectWorkflow, _checker_evidence
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.tools.files import read_file
    from test_fixed_assembly import mock_flow, plan_data
    from test_assembly_feedback_recovery import payload as model_payload, tool

    workflow, request, runtime, source, _ = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    source_bytes = source.read_bytes()
    source_hash = hashlib.sha256(source_bytes).hexdigest()
    localization = dict(method='initial_directional_gravity_moment_v1', status='RANKED',
        reason_code=None, reason=None, causal_claim=False, root_part_id='crossbar',
        first_tipping_time_s=.24, direction_xy=[1., 0.], support_boundary_m=.1,
        net_moment_nm=.03924, positive_moment_sum_nm=.07848,
        candidates=[dict(part_id='stem', moment_nm=.07848, positive_share=1.)],
        part_contributions=[dict(part_id='stem', mass_kg=.2, com_world_m=[.14, 0., .2],
            lever_m=.04, moment_nm=.07848), dict(part_id='crossbar', mass_kg=.8,
            com_world_m=[.095, 0., .1], lever_m=-.005, moment_nm=-.03924)])
    if reason_code not in (None, 'legacy'):
        localization.update(status='FALLBACK', reason_code=reason_code,
            reason='Precise fallback detail. ' * 20, candidates=[])
        if reason_code != 'NO_POSITIVE_CONTRIBUTION':
            localization.update(direction_xy=None, support_boundary_m=None,
                                net_moment_nm=None, positive_moment_sum_nm=None, part_contributions=[])
        else:
            localization.update(net_moment_nm=-.03924, positive_moment_sum_nm=0.)
    domain = dict(tipped=True, contacts=[dict(position=[1., 2., 3.])] * 30)
    if reason_code != 'legacy':
        domain['tipping_localization'] = localization
    finding = physics.finding('assembly_standing', 'SELF_WEIGHT_TIPPING', 'Observed root tilt exceeds limit.',
        part_ids=['crossbar'], domain=domain)
    if reason_code is None:
        finding.region.part_names = ['stem']
    result = CheckerResult(checker='assembly_standing', status='FAIL', summary='Root tipping observed',
        findings=[finding], metrics=deepcopy(domain), assumptions={'source_sha256': source_hash})
    output = tmp_path / 'standing'; output.mkdir()
    report_path = output / 'result.json'; report_path.write_text(result.model_dump_json())
    report_bytes = report_path.read_bytes()
    check = CheckerRun(physics.checker_spec('assembly_standing'), result, output, ())
    feedback = {**_checker_evidence([check], workspace=tmp_path), 'source_version': 'original'}
    raw = {'feedback': feedback, 'source_version': 'original'}
    before = deepcopy(raw)
    view = payload_for_agent(raw, workspace=tmp_path, source_path=source,
        source_version='original', role='coder')
    assert raw == before
    assert payload_for_agent(view, workspace=tmp_path, source_path=source,
        source_version='original', role='coder') == view

    expected = _tipping_localization_for_agent(localization) if reason_code != 'legacy' else None
    if expected is not None:
        assert set(expected) == set(localization) - {'part_contributions'}
        assert expected['causal_claim'] is False and expected['reason_code'] == reason_code
        assert expected['reason'] == (localization['reason'][:240] if localization['reason'] else None)
        assert all(set(row) == {'part_id', 'moment_nm', 'positive_share'} for row in expected['candidates'])
        # The raw domain wins only when valid; invalid/nonfinite values cannot
        # erase the already projected diagnostic on a restored finding.
        summary_finding = deepcopy(view['feedback']['typed_findings'][0])
        valid_raw = deepcopy(localization)
        valid_raw['first_tipping_time_s'] = .25
        summary_finding['domain'] = {'tipping_localization': valid_raw}
        pure = lambda f: finding_for_agent(f, result_ref='standing/result.json',
            result_pointer='/findings/0', source_sha256=source_hash)
        assert pure(summary_finding)['key_values']['tipping_localization']['first_tipping_time_s'] == .25
        summary_finding['domain']['tipping_localization']['first_tipping_time_s'] = float('nan')
        assert pure(summary_finding)['key_values']['tipping_localization'] == expected
        assert _tipping_localization_for_agent({'status': ['invalid']}) is None
        json.dumps(expected, allow_nan=False)
        if reason_code is None:
            many = deepcopy(localization)
            many['candidates'] = [dict(part_id=str(i), moment_nm=4 - i,
                positive_share=(4 - i) / 10, ignored='full evidence') for i in range(4)]
            bounded = _tipping_localization_for_agent(many)
            assert [row['part_id'] for row in bounded['candidates']] == ['0', '1', '2']
            assert sum(row['positive_share'] for row in bounded['candidates']) == pytest.approx(.9)
            assert all(set(row) == {'part_id', 'moment_nm', 'positive_share'} for row in bounded['candidates'])
        without = deepcopy(view)
        without['feedback']['typed_findings'][0]['key_values'].pop('tipping_localization')
        assert feedback_size_report(view)['actual_bytes'] > feedback_size_report(without)['actual_bytes']

    captures = []
    def forbidden(*args, **kwargs):
        raise AssertionError('Boundary-only test must not execute geometry or a checker')
    monkeypatch.setattr(flow, 'execute_asset_source', forbidden)
    monkeypatch.setattr(physics, 'run_assembly_checks', forbidden)
    async def run(**kwargs):
        data = model_payload(kwargs)
        stage = kwargs['stage'].split(':')[0]
        assert stage in {'assembly_engineering', 'repair'}
        captures.append(stage)
        rows = data['typed_findings'] if stage == 'assembly_engineering' else data['feedback']['typed_findings']
        row = rows[0]
        assert row['finding_id'] == 'assembly_standing:SELF_WEIGHT_TIPPING:crossbar'
        assert row['required'] is True and row['repairability'] == 'geometry'
        assert row['region']['part_names'] == (['stem'] if reason_code is None else ['crossbar'])
        assert row['key_values'].get('tipping_localization') == expected
        assert 'part_contributions' not in json.dumps(data)
        assert 'contacts' not in json.dumps(data)
        role = 'engineering' if stage == 'assembly_engineering' else 'coder'
        copy_before = deepcopy(data)
        assert payload_for_agent(data, workspace=tmp_path, source_path=source,
            source_version='original', role=role) == data
        assert data == copy_before
        ref = row['detail_ref']
        assert ref['json_pointer'] == '/findings/0/domain'
        original = json.loads(await tool(read_file, kwargs['context'], stage + '_detail',
            path=ref['path'], json_pointer=ref['json_pointer']))
        assert original == domain
        if expected is not None:
            details = json.loads(await tool(read_file, kwargs['context'], stage + '_localization',
                path=ref['path'], json_pointer=ref['json_pointer'] + '/tipping_localization'))
            assert details == localization
        if stage == 'assembly_engineering':
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['Diagnostic ranking is not a causal proof'], repair_proposals=[]))
        return SimpleNamespace(final_output='{"edit_action":"NO_CHANGE","reason":"Stub: no edit"}')
    runtime.run = run
    root = tmp_path / 'review'; root.mkdir()
    plan = FixedAssemblyPlan.model_validate(plan_data())
    async def exercise():
        await topology.engineer(workflow, runtime, request, plan, source, None, root, check, {}, feedback, 1)
        outcome = await ObjectWorkflow._repair(workflow, runtime=runtime, repairer={}, workspace=tmp_path,
            source_path=source, role='coder', stage='repair:1', allow_no_change=True,
            payload={'feedback': feedback, 'source_version': 'original'})
        assert outcome['status'] == 'NO_CHANGE'
    asyncio.run(exercise())
    assert captures == ['assembly_engineering', 'repair']
    assert source.read_bytes() == source_bytes and report_path.read_bytes() == report_bytes
    assert result.status == 'FAIL' and result.findings[0].finding_id == finding.finding_id


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


@pytest.mark.parametrize('shared', [False, True])
def test_critic_rejection_blocks_requests_despite_checker_regression(tmp_path, monkeypatch, shared):
    from types import SimpleNamespace
    from test_assembly_topology import setup_flow
    from test_fixed_assembly import run_flow
    from adsl.agents.models import GradedImageCriticDecision, EngineeringCriticDecision
    state = setup_flow(tmp_path, monkeypatch, ['PASS', 'FAIL'])
    workflow, _, runtime, _, repair_calls = state
    original_review = workflow._review_generation_code
    error = RequestFailure(429, 'insufficient_quota') if shared else RequestFailure(400, 'context_too_large')
    followups = []

    async def image(**kwargs):
        return GradedImageCriticDecision(approved=False, observations=[], issues=[{
            'severity':'HIGH', 'target':None, 'problem':'Visible discrepancy', 'suggested_fix':'repair silhouette'}])

    async def code(**kwargs):
        if kwargs['round_number'] == 1:
            return await original_review(**kwargs)
        raise error

    async def engineering(**kwargs):
        followups.append(kwargs['role'])
        return SimpleNamespace(final_output=EngineeringCriticDecision(
            approved=False, observations=[], repair_proposals=[]))

    monkeypatch.setattr(workflow, '_review_generation_image', image)
    monkeypatch.setattr(workflow, '_review_generation_code', code)
    runtime.run = engineering
    if shared:
        with pytest.raises(RequestFailure) as caught:
            run_flow(state)
        assert caught.value is error
        book = json.loads((tmp_path/'assembly_versions.json').read_text())
        assert not book['completed']
    else:
        _, book = run_flow(state)
        assert book['completed'] and book['stop_reason'] == 'agent_input_too_large'
    assert followups == [] and len(repair_calls) == 1
    assert 'attempt_0001' in book['versions'] and book['next_round'] == 3
    assert book['versions']['attempt_0001']['reviews']['reason'] == 'previously_valid_check_regressed'
    assert book['terminal_request_error']['kind'] == ('shared_fault' if shared else 'context_limit')
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines()) == 1


def test_executor_program_error_saved_then_propagated_without_roles(tmp_path, monkeypatch):
    from test_fixed_assembly import mock_flow, run_flow
    from adsl.agents import fixed_assembly as flow
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    error = TypeError('deterministic executor defect')
    calls = []

    def execute(*args, **kwargs):
        raise error

    async def forbidden(**kwargs):
        calls.append(kwargs['role'])
        pytest.fail('Program error cannot trigger model requests')

    monkeypatch.setattr(flow, 'execute_asset_source', execute)
    state[2].run = forbidden
    with pytest.raises(TypeError) as caught:
        run_flow(state)
    assert caught.value is error
    book = json.loads((tmp_path/'assembly_versions.json').read_text())
    assert 'original' in book['versions'] and book['next_round'] == 2
    assert not book['completed'] and book['stop_reason'] == 'agent_call_error'
    error_file = tmp_path/'rounds/round_01/flow_error.json'
    assert json.loads(error_file.read_text())['type'] == 'TypeError'
    assert calls == [] and state[-1] == []
    assert not (tmp_path/'repair_history.jsonl').exists()


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


@pytest.mark.parametrize('complete', [False, True])
@pytest.mark.parametrize('missing_path', [False, True])
def test_missing_report_retains_only_real_raw_finding_evidence(tmp_path, complete, missing_path):
    from adsl.agents.agent_feedback import _at
    source, rows, ref, _ = inputs(tmp_path, 2)
    row = {'finding_id':'assembly_mesh_evaluation:missing:body:err',
        'rule_id':'TARGET_PRECISION_UNREPRESENTABLE', 'result_ref':None if missing_path else 'missing.json',
        'result_pointer':'/findings/2', 'source_sha256':ref['source_sha256']}
    if complete:
        row['domain'] = deepcopy(rows[0])
        row['domain'].pop('geometry_repair_allowed')
        row['region'] = {'kind':'aabb', 'details':{'face_vertices':[[1,2,3]]*20}}
    original = deepcopy(row)
    raw = {'typed_findings':[row]}
    out = project(tmp_path, source, None, raw)
    finding = out['typed_findings'][0]
    assert finding['result_ref'] == row['result_ref']
    snapshots = list(source.parent.glob('diagnostics/agent_feedback/finding_snapshot_*.json'))
    if complete:
        assert finding['association_status'] == 'RESOLVED' and len(snapshots) == 1
        refs = finding['evidence_refs']
        assert any(r['path']==row['result_ref'] and r['availability']=='UNAVAILABLE' for r in refs)
        detail = finding['detail_ref']
        assert detail['availability'] == 'AVAILABLE'
        document = json.loads((tmp_path/detail['path']).read_text())
        assert _at(document, detail['json_pointer']) == original['domain']
        assert document['finding'] == original
        assert len(_at(document, finding['region']['detail_ref']['json_pointer'])['face_vertices']) == 20
        assert 'geometry_repair_allowed' not in out['evaluation_feedback']['failures'][0]
    else:
        assert finding['association_status'] == 'UNRESOLVED' and not snapshots
        assert finding['detail_ref']['availability'] == 'UNAVAILABLE'
        assert 'evaluation_feedback' not in out
    assert row == original
    assert project(tmp_path, source, None, out) == out


def test_budget_reduction_preserves_complete_refs_and_idempotence(tmp_path,monkeypatch):
    from adsl.agents import agent_feedback as adapter
    # Keep exercising the same reduction boundary after changing the default.
    monkeypatch.setattr(adapter,'FEEDBACK_LIMIT_BYTES',32*1024)
    from adsl.agents.agent_feedback import _at, FEEDBACK_LIMIT_BYTES
    source, rows, ref, _ = inputs(tmp_path, 1)
    row = deepcopy(rows[0])
    row['diagnostic']['attempts'] = [deepcopy(row['diagnostic']['attempts'][0]) for _ in range(10)]
    findings = []
    for i in range(6):
        findings.append({'finding_id':f'assembly_mesh_evaluation:{i}:body:err',
            'rule_id':'TARGET_PRECISION_UNREPRESENTABLE', 'required':False,
            'domain':row, 'source_candidates':[
                {'feature_id':f'{i}:{j}:'+'feature_'*18, 'method':'source_index', 'ambiguous':True,
                 'source_ids':[f'id_{k}_'+'x'*120 for k in range(3)],
                 'source_locations':[f'source:{k}:'+'y'*120 for k in range(3)],
                 'evidence':[{'complete':'source evidence'}]} for j in range(6)]})
    result = tmp_path/'result.json'
    result.write_text(json.dumps({'assumptions':{'source_sha256':ref['source_sha256']}, 'findings':findings}))
    typed = [finding_for_agent(f, result_ref=str(result), result_pointer=f'/findings/{i}',
        source_sha256=ref['source_sha256']) for i,f in enumerate(findings)]
    raw = {'typed_findings':typed}
    original = deepcopy(raw)
    assert len(json.dumps(raw).encode()) > FEEDBACK_LIMIT_BYTES
    out = project(tmp_path, source, ref, raw)
    assert len(out['typed_findings'][0]['source_candidates']) < 6
    count = 0

    def check_refs(owner):
        nonlocal count
        if isinstance(owner, dict):
            for field, info in owner.get('preview_info', {}).items():
                if info.get('omitted_count', 0):
                    full = info['full_ref']
                    assert full and full['path']
                    document = json.loads((tmp_path/full['path']).read_text())
                    assert len(_at(document, full['json_pointer'])) == info['total']
                    count += 1
            for value in owner.values():
                check_refs(value)
        elif isinstance(owner, list):
            for value in owner:
                check_refs(value)

    check_refs(out)
    group, = out['evaluation_feedback']['failures']
    assert group['preview_info']['attempts_summary']['total'] == 10
    assert count >= 7
    again = project(tmp_path, source, ref, out)
    (tmp_path/'projected.json').write_text(json.dumps(out, allow_nan=False))
    (tmp_path/'reprojected.json').write_text(json.dumps(again, allow_nan=False))
    assert again == out
    assert raw == original
    json.dumps(out, allow_nan=False)


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


@pytest.mark.parametrize('classified_first', [False, True])
@pytest.mark.parametrize('permission', [False, True])
def test_controller_classification_wins_independent_of_order_and_role(tmp_path, classified_first, permission):
    from adsl.agents.fixed_assembly import _failure_feedback
    source, rows, ref, _ = inputs(tmp_path, 1)
    for row in rows:
        row.pop('failure_kind')
        row.pop('geometry_repair_allowed')
    report = {'source_sha256':ref['source_sha256'], 'display_failures':rows}
    Path(ref['path']).write_text(json.dumps(report))
    classified = _failure_feedback(report, source_sha256=ref['source_sha256'])
    assert all(row['failure_kind'] == 'candidate_evaluation' for row in classified)
    for row in classified:
        row['geometry_repair_allowed'] = permission
    raw = {'display_failures':rows}
    controller = {'failure_feedback':classified}
    combined = ({**controller, 'feedback':raw} if classified_first else
                {**raw, 'feedback':controller})
    coder = project(tmp_path, source, ref, combined, role='coder')
    engineer = project(tmp_path, source, ref, {'evaluation_failures':classified})
    c, = coder['evaluation_feedback']['failures']
    e, = engineer['evaluation_feedback']['failures']
    assert c['failure_id'] == e['failure_id']
    assert c['failure_kind'] == e['failure_kind'] == 'candidate_evaluation'
    assert c['geometry_repair_allowed'] is e['geometry_repair_allowed'] is permission
    assert project(tmp_path, source, ref, coder, role='coder') == coder
    # The unclassified original does not receive inferred permission.
    unclassified = project(tmp_path, source, ref, raw)['evaluation_feedback']['failures'][0]
    assert 'geometry_repair_allowed' not in unclassified


@pytest.mark.parametrize('field,value', [('geometry_repair_allowed', False), ('failure_kind', 'export')])
def test_equal_authority_classification_conflict_is_rejected(tmp_path, field, value):
    source, rows, ref, _ = inputs(tmp_path, 1)
    conflict = deepcopy(rows)
    conflict[0][field] = value
    with pytest.raises(AgentFeedbackError) as caught:
        project(tmp_path, source, ref, {'evaluation_failures':rows,
            'feedback':{'failure_feedback':conflict}})
    assert caught.value.code == 'AGENT_FEEDBACK_INVALID'
    assert caught.value.details['reason'] == 'conflicting_failure_classification'


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


def test_unknown_source_merges_only_within_original_container(tmp_path):
    source, _, _, _ = inputs(tmp_path, 1)
    raw = {'code':'INPUT_GEOMETRY_INVALID', 'stage':'input_geometry', 'part_id':'body'}
    payload = {'feedback':{'report_ref':{'path':'missing_a.json'}, 'failures':[raw]},
               'assembly_context':{'report_ref':{'path':'missing_b.json'}, 'failure_feedback':[raw]}}
    out = project(tmp_path, source, None, payload)
    assert out['evaluation_feedback']['unique_failure_count'] == 2
    payload['assembly_context']['report_ref']['path'] = 'missing_a.json'
    out = project(tmp_path, source, None, payload)
    assert out['evaluation_feedback']['unique_failure_count'] == 1
    assert all(ref['source_binding'] == 'UNKNOWN'
               for ref in out['evaluation_feedback']['failures'][0]['evidence_refs'])
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
    payload = {'checker_summary': [{'status': 'FAIL', 'required': True, 'rule_id': 'x' * 200}] * (FEEDBACK_LIMIT_BYTES//200+1)}
    with pytest.raises(AgentFeedbackError) as caught:
        payload_for_agent(payload, workspace=tmp_path, source_path=None, source_version=None, role='engineering')
    assert caught.value.code == 'AGENT_FEEDBACK_TOO_LARGE'


def test_96k_default_accepts_feedback_above_legacy_limit(tmp_path,monkeypatch):
    from adsl.agents import agent_feedback as adapter
    raw={'checker_summary':[{'status':'FAIL','required':True,'rule_id':'x'*200} for _ in range(180)]}
    before=deepcopy(raw)
    size=adapter.feedback_size_report(raw)['actual_bytes']
    assert 32*1024<size<adapter.FEEDBACK_LIMIT_BYTES==96*1024
    def project(value):
        return payload_for_agent(value,workspace=tmp_path,source_path=None,source_version=None,role='engineering')
    out=project(raw)
    assert out==raw and project(out)==out
    monkeypatch.setattr(adapter,'FEEDBACK_LIMIT_BYTES',32*1024)
    with pytest.raises(AgentFeedbackError) as caught:
        project(raw)
    assert caught.value.code=='AGENT_FEEDBACK_TOO_LARGE'
    assert caught.value.details['actual_bytes']==size and caught.value.details['limit_bytes']==32*1024
    assert raw==before


def test_repair_history_survives_nested_budget_reduction(tmp_path,monkeypatch):
    from adsl.agents import agent_feedback as module
    source=tmp_path/'source.py';source.write_text('A')
    raw={'feedback':{'checker_evidence':{'repair_history':[
        dict(version=f'attempt_{i}',source_sha256='a'*64,parent_version='original',
            action='reshape',hypothesis='h'*240,reason='r'*240,tool_statuses={'assembly_topology':'PASS'},
            execution_error={'exception_type':'AttributeError','message':'missing children'},failure_codes=['GENERATED_SOURCE_EXECUTION_FAILED'])
        for i in range(10)]}}}
    old=deepcopy(raw)
    monkeypatch.setattr(module,'FEEDBACK_LIMIT_BYTES',2200)
    result=payload_for_agent(raw,workspace=tmp_path,source_path=source,source_version='original',role='coder')
    history=result['feedback']['checker_evidence']['repair_history']
    assert len(history)==1 and history[0]['source_version']=='attempt_9'
    assert history[0]['action']=='reshape' and history[0]['tool_statuses']['assembly_topology']=='PASS'
    assert history[0]['execution_error']['exception_type']=='AttributeError'
    assert raw==old
    assert payload_for_agent(result,workspace=tmp_path,source_path=source,source_version='original',role='coder')==result


def test_outside_workspace_evidence_reason_is_idempotent(tmp_path):
    raw={'evidence_files':[{'path':'/unavailable/external/report.json','source_sha256':None}]}
    a=payload_for_agent(raw,workspace=tmp_path,source_path=None,source_version=None,role='coder')
    assert a['evidence_files'][0]['reason']=='outside_workspace'
    assert payload_for_agent(a,workspace=tmp_path,source_path=None,source_version=None,role='coder')==a


def test_reprojection_rehydrates_omitted_groups_before_typed_association(tmp_path):
    source=tmp_path/'source.py';source.write_text('source')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    rows=[dict(code='TARGET_PRECISION_UNREPRESENTABLE',stage='target_precision',part_id=f'p{i}',
        source_sha256=digest,source_version='original',file=f'p{i}.glb',output_role='display',
        failure_kind='candidate_evaluation',geometry_repair_allowed=True) for i in range(7)]
    finding=dict(finding_id='assembly_mesh_evaluation:6',rule_id='TARGET_PRECISION_UNREPRESENTABLE',
        category='evidence_insufficient',required=False,repairability='geometry',domain=rows[-1],
        result_ref='result.json',result_pointer='/findings/0',source_sha256=digest)
    document=dict(source_sha256=digest,source_version='original',failures=rows,findings=[finding])
    (tmp_path/'result.json').write_text(json.dumps(document))
    ref=dict(path='result.json',source_sha256=digest,source_version='original')
    raw=dict(failures=rows,typed_findings=[finding],geometry_report_ref=ref)
    out=payload_for_agent(raw,workspace=tmp_path,source_path=source,source_version='original',role='coder',report_ref=ref)
    assert len(out['evaluation_feedback']['failures'])<out['evaluation_feedback']['unique_failure_count']
    assert payload_for_agent(out,workspace=tmp_path,source_path=source,source_version='original',role='coder',report_ref=ref)==out


def test_legacy_budget_crops_evidence_directory_with_complete_pointer(tmp_path,monkeypatch):
    from adsl.agents import agent_feedback as adapter
    monkeypatch.setattr(adapter,'FEEDBACK_LIMIT_BYTES',32*1024)
    source = tmp_path/'source.py'; source.write_text('source')
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    directory = tmp_path/('evidence_' + 'x'*160); directory.mkdir()
    refs = []
    for i in range(100):
        path = directory/f'result_{i}.json'; path.write_text('{}')
        refs.append(dict(path=str(path),source_sha256=digest,source_version='original'))
    error = dict(code='GENERATED_SOURCE_EXECUTION_FAILED',stage='source_execution',
        exception_type='AttributeError',message='missing children',source_locations=[],
        source_sha256=digest,source_version='original',repair_target='generated_source')
    raw = dict(feedback=dict(evidence_files=refs,execution_failure=error,repair_history=[dict(
        source_version='original',source_sha256=digest,action='reshape',reason='source execution failed',
        execution_error=dict(exception_type='AttributeError',message='missing children'))]))
    original = deepcopy(raw)
    out = payload_for_agent(raw,workspace=tmp_path,source_path=source,source_version='original',role='coder')
    assert len(json.dumps(out,ensure_ascii=False,separators=(',',':')).encode()) <= 32768
    info = out['feedback']['preview_info']['evidence_files']
    assert info['total']==100 and info['omitted_count']==97
    assert len(out['feedback']['evidence_files'])==3
    full_ref = info['full_ref']
    document = json.loads((tmp_path/full_ref['path']).read_text())
    assert len(document['evidence_files'])==info['total']
    assert full_ref['json_pointer']=='/evidence_files'
    assert all(r['availability']=='AVAILABLE' and r['source_binding']=='CURRENT' for r in document['evidence_files'])
    assert out['evaluation_feedback']['failures'][0]['message']=='missing children'
    assert out['feedback']['repair_history'][0]['execution_error']['message']=='missing children'
    assert raw==original
    assert payload_for_agent(out,workspace=tmp_path,source_path=source,source_version='original',role='coder')==out


def test_partition_intent_is_short_read_only_input(tmp_path):
    import copy
    source=tmp_path/'source.py';source.write_text('working')
    intent={'schema_version':1,'proposal_id':'split','comparison_baseline_version':'original',
        'comparison_baseline_source_sha256':'a'*64,'grouping_change':{'operation':'split',
        'source_part_ids':['body'],'target_print_parts':[{'id':'body','components':['body']},
        {'id':'hat','components':['hat']}],'connection_changes':['hidden interface']}}
    payload={'partition_intent':intent,'grouping_change':intent['grouping_change'],
        'grouping_realization':{'status':'MISMATCH','missing_part_ids':['hat']},
        'comparison_baseline_version':'original','edit_purpose':'partition_optimization'}
    before=copy.deepcopy(payload)
    projected=payload_for_agent(payload,workspace=tmp_path,source_path=source,source_version='attempt_0001',role='coder')
    assert projected['partition_intent']==intent and payload==before
    assert payload_for_agent(projected,workspace=tmp_path,source_path=source,source_version='attempt_0001',role='coder')==projected


def support_budget_evidence():
    return dict(source_sha256='a'*64, source_version='original', measurement_config_sha256='b'*64,
        part_count=1, tolerance_mm3=1.2000000000000002, assembled_size_mm=[3.,4.,12.],
        support=dict(status='PASS',total_support_volume_mm3=12.000000000000002,
            total_volume_mm3=100.,nsv_total=.12000000000000002,total_risky_projected_area_mm2=6.),
        items=[dict(part_id='body',support=dict(status='PASS',support_volume_mm3=12.000000000000002,
            mesh_volume_mm3=100.,nsv=.12000000000000002,risky_projected_area_mm2=6.,
            risky_face_count=1,ray_backend='fixture'),bounds_mm=[[0.,0.,0.],[3.,4.,12.]],
            print_transform_mm=[[1.,0.,0.,0.],[0.,1.,0.,0.],[0.,0.,1.,.12345678901234568],[0.,0.,0.,1.]],
            largest_regions=[],evidence_ref={'path':'support.json','json_pointer':'/support'})])


@pytest.mark.parametrize('container',['feedback','assembly_context','pending_reviews','engineering_feedback','checker_evidence'])
def test_support_dedup_is_source_bound_and_does_not_mutate_input(tmp_path,container):
    from adsl.agents.agent_feedback import _support_for_agent
    support=support_budget_evidence()
    baseline=deepcopy(support);baseline['source_version']='previous'
    support['comparison_baseline']=baseline
    raw=dict(support_evidence=support,**{container:dict(support_evidence=deepcopy(support),
        support_round=False,support_baseline_version=None,mode_a_pose_check={'conclusion':'ACCEPT'})})
    before=deepcopy(raw)
    out=payload_for_agent(raw,workspace=tmp_path,source_path=None,source_version='original',role='coder')
    assert 'support_evidence' not in out[container]
    assert out['support_evidence']==_support_for_agent(support)
    assert out[container]['support_round'] is False
    assert out[container]['support_baseline_version'] is None
    assert out[container]['mode_a_pose_check']=={'conclusion':'ACCEPT'}
    assert out['support_evidence']['comparison_baseline']['source_version']=='previous'
    assert out['support_evidence']['tolerance_mm3']==support['tolerance_mm3']
    assert out['support_evidence']['items'][0]['print_transform_mm']==support['items'][0]['print_transform_mm']
    assert raw==before
    assert payload_for_agent(out,workspace=tmp_path,source_path=None,source_version='original',role='coder')==out


@pytest.mark.parametrize('field,value',[('source_sha256','c'*64),('source_version','other'),
    ('measurement_config_sha256','d'*64),('source_sha256',None),('source_version',''),
    ('measurement_config_sha256',None),('part_count',2)])
def test_support_dedup_retains_missing_identity_or_conflicting_evidence(field,value):
    from adsl.agents.agent_feedback import _deduplicate_support_evidence
    first=support_budget_evidence();other=deepcopy(first);other[field]=value
    payload=dict(support_evidence=first,feedback={'support_evidence':other})
    before=deepcopy(payload)
    assert _deduplicate_support_evidence(payload)==[]
    assert payload['support_evidence']==before['support_evidence']
    assert payload['feedback']['support_evidence']==before['feedback']['support_evidence']
    if field=='part_count':
        assert payload['support_evidence_provenance']['raw_sha256']!=payload['feedback']['support_evidence_provenance']['raw_sha256']
    else:
        assert payload==before


def test_support_dedup_compares_complete_strict_json_before_projection():
    from adsl.agents.agent_feedback import _deduplicate_support_evidence
    first=support_budget_evidence();other=deepcopy(first);other['raw_only']='different detail'
    payload=dict(support_evidence=first,feedback={'support_evidence':other})
    assert _deduplicate_support_evidence(payload)==[]
    assert payload['feedback']['support_evidence']['raw_only']=='different detail'
    first['support']['total_support_volume_mm3']=float('nan')
    other=deepcopy(first);payload=dict(support_evidence=first,feedback={'support_evidence':other})
    assert _deduplicate_support_evidence(payload)==[]
    assert payload['support_evidence'] is first and payload['feedback']['support_evidence'] is other


def test_support_dedup_promotes_missing_top_and_keeps_invalid_top():
    from adsl.agents.agent_feedback import _deduplicate_support_evidence
    support=support_budget_evidence()
    payload={'feedback':{'pending_reviews':{'support_evidence':deepcopy(support)}}}
    events=_deduplicate_support_evidence(payload)
    assert payload['support_evidence']==support
    assert 'support_evidence' not in payload['feedback']['pending_reviews']
    assert events[0]['removed_path']=='/feedback/pending_reviews/support_evidence'
    assert events[0]['canonical_path']=='/support_evidence'
    assert _deduplicate_support_evidence(payload)==[]
    for invalid in (None,'invalid',{}, {'source_version':'original'}):
        payload=dict(support_evidence=deepcopy(invalid),feedback={'support_evidence':deepcopy(support)})
        assert _deduplicate_support_evidence(payload)==[]
        assert payload['support_evidence']==invalid and payload['feedback']['support_evidence']==support


def test_support_dedup_ignores_task_data_and_comparison_baseline_and_bounds_events():
    from adsl.agents.agent_feedback import _deduplicate_support_evidence
    support=support_budget_evidence()
    payload=dict(support_evidence=support,task={'support_evidence':deepcopy(support)})
    current=payload
    for _ in range(20):
        current['feedback']={'support_evidence':deepcopy(support)};current=current['feedback']
    events=_deduplicate_support_evidence(payload)
    assert len(events)==15 and payload['task']['support_evidence']==support
    assert 'support_evidence' not in current
    assert set(events[0])=={'canonical_path','removed_path','source_sha256','source_version','measurement_config_sha256'}
    # JSON member ordering does not distinguish otherwise identical evidence.
    nested=dict(reversed(list(support.items())))
    assert _deduplicate_support_evidence(dict(support_evidence=support,feedback={'support_evidence':nested}))


def test_engineering_single_support_and_area_contracts_unchanged(tmp_path,monkeypatch):
    from adsl.agents import agent_feedback as adapter
    support=support_budget_evidence()
    projected=payload_for_agent(dict(support_evidence=support),workspace=tmp_path,
        source_path=None,source_version='original',role='engineering')
    assert projected=={'support_evidence':adapter._support_for_agent(support)}
    assert 'feedback' not in projected
    assert payload_for_agent(projected,workspace=tmp_path,source_path=None,source_version='original',role='engineering')==projected
    def unexpected(_):raise AssertionError('area must not invoke support deduplication')
    monkeypatch.setattr(adapter,'_deduplicate_support_evidence',unexpected)
    area=dict(requirement='unchanged task',fixed_assembly={'physics':{'overhang':{}}},
        feedback={'repair_history':[],'support_round':False})
    assert payload_for_agent(area,workspace=tmp_path,source_path=None,source_version=None,role='coder')==area


def test_feedback_size_report_has_exact_scope_bounded_lists_and_escaped_pointers():
    from adsl.agents.agent_feedback import _feedback_view,_encoded,feedback_size_report
    raw=dict(requirement='unbudgeted'*1000,source='unbudgeted source',image='unbudgeted image',
        support_evidence=support_budget_evidence(),feedback={'support_round':False},
        checker_evidence={**{str(i):'x'*i for i in range(25)},'a~/b':'value'})
    before=deepcopy(raw);report=feedback_size_report(raw)
    assert report['actual_bytes']==len(_encoded(_feedback_view(raw)))
    assert report['limit_bytes']==FEEDBACK_LIMIT_BYTES==96*1024
    assert len(report['top_level_bytes'])<=15 and len(report['nested_field_bytes'])==15
    for rows in (report['top_level_bytes'],report['nested_field_bytes']):
        assert rows==sorted(rows,key=lambda r:(-r['bytes'],r['path']))
        assert all(set(r)=={'path','bytes'} for r in rows)
    escaped=feedback_size_report({'checker_evidence':{'a~/b':'value'}})
    assert escaped['nested_field_bytes']==[dict(path='/checker_evidence/a~0~1b',bytes=len(_encoded('value')))]
    parent=feedback_size_report({'feedback':{'support_evidence':{'support_round':False}}})
    assert parent['actual_bytes']!=sum(r['bytes'] for r in parent['top_level_bytes']+parent['nested_field_bytes'])
    assert raw==before
    assert all('/items/' not in r['path'] for r in report['nested_field_bytes'])
    with pytest.raises(ValueError):feedback_size_report(raw,limit=0)


def test_support_oversize_error_keeps_code_and_bounded_field_distribution(tmp_path):
    from adsl.agents.agent_feedback import _feedback_view,_encoded
    support=support_budget_evidence();support['items']=[deepcopy(support['items'][0]) for _ in range(300)]
    with pytest.raises(AgentFeedbackError) as captured:
        payload_for_agent({'support_evidence':support},workspace=tmp_path,source_path=None,
            source_version='original',role='coder')
    error=captured.value
    assert error.code=='AGENT_FEEDBACK_TOO_LARGE'
    assert set(error.details)=={'actual_bytes','limit_bytes','top_level_bytes','nested_field_bytes'}
    assert error.details['actual_bytes']>error.details['limit_bytes']==FEEDBACK_LIMIT_BYTES
    assert len(error.details['top_level_bytes'])<=15 and len(error.details['nested_field_bytes'])<=15
    assert 'fixture' not in json.dumps(error.details)


def test_saved_snowman_five_part_support_structure_fits_after_strict_dedup(tmp_path):
    from adsl.agents.agent_feedback import feedback_size_report
    fixture=json.loads((Path(__file__).parent/'fixtures/feedback_snowman_support.json').read_text())
    # The full historical request was replayed separately. This fixture retains
    # its real five-part support structure, not the entire historical workflow.
    support=fixture['support_evidence'];raw=dict(support_evidence=support,feedback={'support_evidence':deepcopy(support)})
    before=deepcopy(raw)
    out=payload_for_agent(raw,workspace=tmp_path,source_path=None,source_version='original',role='coder')
    assert len(out['support_evidence']['items'])==out['support_evidence']['part_count']==5
    assert {r['part_id'] for r in out['support_evidence']['items']}=={
        'snow_body','head_module','hat_module','left_arm_piece','right_arm_piece'}
    assert 'support_evidence' not in out['feedback']
    assert out['support_evidence']['support']==support['support']
    assert feedback_size_report(out)['actual_bytes']<=32768
    assert raw==before


@pytest.mark.parametrize('container',['feedback','assembly_context','pending_reviews','engineering_feedback','checker_evidence'])
@pytest.mark.parametrize('difference',['raw_only','truncated_reason'])
def test_support_conflicts_survive_two_full_projections(tmp_path,container,difference):
    from adsl.agents.agent_feedback import _encoded, feedback_size_report
    first=support_budget_evidence();second=deepcopy(first)
    if difference=='raw_only':
        first['raw_only']='original A';second['raw_only']='original B'
    else:
        first['support']['reason']='x'*240+' original A'
        second['support']['reason']='x'*240+' original B'
    raw=dict(support_evidence=first,**{container:{'support_evidence':second}})
    before=deepcopy(raw)
    once=payload_for_agent(raw,workspace=tmp_path,source_path=None,source_version='original',role='coder')
    assert once['support_evidence']==once[container]['support_evidence']
    assert once['support_evidence_provenance']['raw_sha256']==hashlib.sha256(_encoded(first)).hexdigest()
    assert once[container]['support_evidence_provenance']['raw_sha256']==hashlib.sha256(_encoded(second)).hexdigest()
    assert once['support_evidence_provenance']['raw_sha256']!=once[container]['support_evidence_provenance']['raw_sha256']
    for location in (once,once[container]):
        assert location['support_evidence_provenance']['view_sha256']==hashlib.sha256(_encoded(location['support_evidence'])).hexdigest()
    assert any(row['path']=='/support_evidence_provenance' for row in feedback_size_report(once)['top_level_bytes'])
    twice=payload_for_agent(once,workspace=tmp_path,source_path=None,source_version='original',role='coder')
    assert twice==once
    assert raw==before


def test_support_provenance_rebinds_changed_view_and_survives_promotion(tmp_path):
    from adsl.agents.agent_feedback import _encoded
    first=support_budget_evidence();second=deepcopy(first)
    first['raw_only']='A';second['raw_only']='B'
    project=lambda value:payload_for_agent(value,workspace=tmp_path,source_path=None,source_version='original',role='coder')
    once=project(dict(support_evidence=first,feedback={'support_evidence':second}))
    changed=deepcopy(once)
    changed['feedback']['support_evidence']['support']['total_volume_mm3']=101.
    changed_raw_hash=hashlib.sha256(_encoded(changed['feedback']['support_evidence'])).hexdigest()
    rebound=project(changed)
    assert rebound['feedback']['support_evidence_provenance']['raw_sha256']==changed_raw_hash
    assert rebound['support_evidence_provenance']['raw_sha256']==once['support_evidence_provenance']['raw_sha256']
    assert project(rebound)==rebound
    # Promoting an already projected view must not replace its original identity.
    raw={'feedback':deepcopy(once['feedback']),'assembly_context':{
        'support_evidence':deepcopy(once['support_evidence']),
        'support_evidence_provenance':deepcopy(once['support_evidence_provenance'])}}
    before=deepcopy(raw);promoted=project(raw)
    assert 'support_evidence' not in promoted['feedback']
    assert 'support_evidence_provenance' not in promoted['feedback']
    assert promoted['support_evidence_provenance']==once['feedback']['support_evidence_provenance']
    assert promoted['assembly_context']['support_evidence_provenance']==once['support_evidence_provenance']
    assert project(promoted)==promoted and raw==before


@pytest.mark.parametrize('difference',['raw_only','previous_previews'])
def test_support_provenance_tracks_existing_budget_region_reduction(tmp_path,monkeypatch,difference):
    from adsl.agents import agent_feedback as adapter
    first=support_budget_evidence();second=deepcopy(first)
    for evidence,label in ((first,'A'),(second,'B')):
        evidence['raw_only']=label
        evidence['items'][0]['largest_regions']=[{
            'area_mm2':float(i),'centroid_mm':[0.,1.,float(i)],
            'bounds_mm':[[0.,0.,0.],[1.,2.,float(i)]]} for i in (1,2,3)]
    raw=dict(support_evidence=first,feedback={'support_evidence':second});before=deepcopy(raw)
    if difference=='previous_previews':
        # Valid but unequal earlier previews can share the same raw fingerprint.
        # The full entry must preserve their distinction through later reduction.
        raw_hash=hashlib.sha256(adapter._encoded(first)).hexdigest()
        raw['support_evidence']=adapter._support_for_agent(first)
        raw['feedback']['support_evidence']=deepcopy(raw['support_evidence'])
        raw['feedback']['support_evidence']['items'][0]['largest_regions']=raw['feedback']['support_evidence']['items'][0]['largest_regions'][:1]
        for location in (raw,raw['feedback']):
            location['support_evidence_provenance']={'raw_sha256':raw_hash}
        adapter._refresh_support_evidence_provenance(raw)
        before=deepcopy(raw)
    project=lambda value:payload_for_agent(value,workspace=tmp_path,source_path=None,source_version='original',role='coder')
    full=project(raw);reduced=deepcopy(full)
    for location in (reduced,reduced['feedback']):
        location['support_evidence']['items'][0]['largest_regions']=location['support_evidence']['items'][0]['largest_regions'][:1]
    adapter._refresh_support_evidence_provenance(reduced)
    limit=adapter.feedback_size_report(reduced)['actual_bytes']
    assert limit<adapter.feedback_size_report(full)['actual_bytes']
    monkeypatch.setattr(adapter,'FEEDBACK_LIMIT_BYTES',limit)
    once=project(raw)
    assert once==reduced and project(once)==once
    assert adapter.feedback_size_report(once)['actual_bytes']==limit
    assert raw==before


# v5 contracts: written only; no native geometry or real model calls.
def assembly_candidates_input(tmp_path, *, count=6, checker='assembly_topology'):
    source = tmp_path / 'source.py'
    source.write_text('scene = None\n')
    sha = hashlib.sha256(source.read_bytes()).hexdigest()
    row = dict(finding_id=f'{checker}:body:CONTRACT', rule_id='CONTRACT',
        category='geometry_failure', required=True, repairability='geometry',
        applicability='applicable', message='Keep this exact judgement',
        metric=dict(name='gap', value=.123456789012345, unit='mm', comparator='<=', threshold=0),
        region=dict(kind='aabb', frame='assembly', unit='mm', bounds=[[0,0,0],[1,2,3]]),
        domain={'part_id':'body'}, source_candidates=[dict(feature_id=f'feature:{i}',
            source_ids=[f'source:{i}:{j}' for j in range(4)],
            source_locations=[{'path':'source.py','line':j+1,'function':'Piece'} for j in range(4)],
            method='direct', relation_role='tab', ambiguous=True,
            overlap_score=.123456789012345, evidence=['Original full evidence']*3)
            for i in range(count)])
    report = tmp_path/'topology.json'
    report.write_text(json.dumps(dict(source_sha256=sha, source_version='original', findings=[row])))
    ref = dict(path='topology.json', source_sha256=sha, source_version='original')
    typed = finding_for_agent(row, result_ref='topology.json', result_pointer='/findings/0', source_sha256=sha)
    payload = dict(plan=dict(print_parts=[dict(id='body',components=['piece'])], connections=[], root_part='body'),
                   assembly_context={}, typed_findings=[typed])
    return source, row, ref, payload


@pytest.mark.parametrize('role', ['engineering','coder','code_critic'])
@pytest.mark.parametrize('metric', ['area','nsv'])
@pytest.mark.parametrize('checker', ['assembly_topology','assembly_standing','assembly_overhang'])
def test_compact_candidates_preserves_judgements_and_complete_reference(tmp_path, monkeypatch, role, metric, checker):
    from adsl.agents import agent_feedback as a
    source, raw, ref, payload = assembly_candidates_input(tmp_path, checker=checker)
    if role != 'engineering':
        payload['fixed_assembly'] = {'physics':{'overhang':{'support_metric':metric}}}
    if metric == 'nsv':
        payload['support_evidence'] = dict(source_sha256=ref['source_sha256'], source_version='original',
            measurement_config_sha256='ray-config', support={'status':'PASS'}, items=[])
    before = deepcopy(payload)
    with monkeypatch.context() as m:
        m.setattr(a, '_COMPACT_ASSEMBLY_CANDIDATES', False)
        old = project(tmp_path, source, ref, payload, role)
    out = project(tmp_path, source, ref, payload, role)
    f = out['typed_findings'][0]; previous = old['typed_findings'][0]
    expected = [{k:c[k] for k in a._CANDIDATE_FIELDS if k in c} for c in previous['source_candidates']]
    assert f['source_candidates'] == expected and len(expected) == 6
    assert {k:v for k,v in f.items() if k not in {'source_candidates','preview_info'}} == {
        k:v for k,v in previous.items() if k not in {'source_candidates','preview_info'}}
    info = f['preview_info']['source_candidates']; full_ref = info['full_ref']
    assert info['total'] == 6 and info['omitted_count'] == 0
    assert full_ref['source_binding'] == 'CURRENT'
    assert a._at(json.loads((tmp_path/full_ref['path']).read_text()), full_ref['json_pointer']) == raw['source_candidates']
    assert f['detail_ref']['json_pointer'] == '/findings/0/domain'
    assert len(a._encoded(a._feedback_view(out))) < len(a._encoded(a._feedback_view(old)))
    assert payload == before and project(tmp_path, source, ref, out, role) == out
    assert not list(tmp_path.glob('diagnostics/agent_feedback/*.json'))


@pytest.mark.parametrize('count', [6,9])
def test_compact_full_ref_via_read_file_tool_and_budget(tmp_path, monkeypatch, count):
    import asyncio
    from adsl.agents import agent_feedback as a
    from adsl.agents.tools.context import AgentToolContext
    from adsl.agents.tools.files import read_file
    from test_assembly_feedback_recovery import tool
    source, raw, ref, payload = assembly_candidates_input(tmp_path, count=count)
    first = project(tmp_path, source, ref, payload)
    # Force existing budget reductions; no new hard card/field limit.
    size = a.feedback_size_report(first)['actual_bytes']
    monkeypatch.setattr(a, 'FEEDBACK_LIMIT_BYTES', size - 100)
    out = project(tmp_path, source, ref, payload)
    info = out['typed_findings'][0]['preview_info']['source_candidates']
    assert len(out['typed_findings'][0]['source_candidates']) < 6
    assert info['total'] == count
    assert info['omitted_count'] == count - len(out['typed_findings'][0]['source_candidates'])
    full = info['full_ref']
    response = asyncio.run(tool(read_file, AgentToolContext(workspace=tmp_path, source_path=source),
        'candidates', path=full['path'], json_pointer=full['json_pointer'], max_chars=12000))
    assert json.loads(response) == raw['source_candidates']
    assert project(tmp_path, source, ref, out) == out


@pytest.mark.parametrize('fault', ['missing','pointer','stale','conflict','summary','file_hash','wrong_finding'])
def test_compaction_falls_back_without_reliable_full_original(tmp_path, monkeypatch, fault):
    from adsl.agents import agent_feedback as a
    source, raw, ref, payload = assembly_candidates_input(tmp_path)
    typed = payload['typed_findings'][0]
    document = json.loads((tmp_path/'topology.json').read_text())
    if fault == 'missing': (tmp_path/'topology.json').unlink()
    if fault == 'pointer': typed['result_pointer'] = '/findings/999'
    if fault == 'stale': typed['source_sha256'] = 'a'*64
    if fault == 'conflict': document['source_sha256'] = 'b'*64
    if fault == 'summary': document['findings'][0] = deepcopy(typed)
    if fault == 'wrong_finding': document['findings'][0]['finding_id'] = 'assembly_topology:another:CONTRACT'
    if fault in {'conflict','summary','wrong_finding'}:
        (tmp_path/'topology.json').write_text(json.dumps(document))
    if fault == 'file_hash':
        typed['preview_info'] = {'source_candidates':{'total':6,'omitted_count':0,
            'full_ref':{**ref,'file_sha256':'0'*64,'json_pointer':'/findings/0/source_candidates'}}}
    with monkeypatch.context() as m:
        m.setattr(a, '_COMPACT_ASSEMBLY_CANDIDATES', False)
        old = project(tmp_path, source, ref, payload)
    actual = project(tmp_path, source, ref, payload)
    if fault == 'file_hash':
        info = actual['typed_findings'][0]['preview_info']['source_candidates']
        assert info['total'] == 6 and info['omitted_count'] == 0
        assert info['full_ref']['availability'] == 'UNAVAILABLE'
        assert info['full_ref']['reason'] == 'file_hash_mismatch'
        actual['typed_findings'][0].pop('preview_info')
    assert actual == old


def test_compact_recovery_snapshot_and_finding_identity(tmp_path):
    from adsl.agents import agent_feedback as a
    source, raw, ref, payload = assembly_candidates_input(tmp_path)
    (tmp_path/'topology.json').unlink()
    payload['typed_findings'] = [{**raw, 'result_ref':'missing.json',
        'result_pointer':'/findings/0','source_sha256':ref['source_sha256']}]
    first = project(tmp_path, source, ref, payload)
    f = first['typed_findings'][0]; full = f['preview_info']['source_candidates']['full_ref']
    assert 'finding_snapshot_' in full['path']
    assert a._at(json.loads((tmp_path/full['path']).read_text()), full['json_pointer']) == raw['source_candidates']
    snapshots = {p.name:p.read_bytes() for p in tmp_path.glob('diagnostics/agent_feedback/*.json')}
    assert project(tmp_path, source, ref, first) == first
    assert snapshots == {p.name:p.read_bytes() for p in tmp_path.glob('diagnostics/agent_feedback/*.json')}
    assert any(r['availability']=='UNAVAILABLE' for r in f['evidence_refs'])


@pytest.mark.parametrize('role', ['image_critic','debugger'])
def test_non_target_roles_keep_original_candidates(tmp_path, monkeypatch, role):
    from adsl.agents import agent_feedback as a
    source, _, ref, payload = assembly_candidates_input(tmp_path)
    actual = project(tmp_path, source, ref, payload, role)
    monkeypatch.setattr(a, '_COMPACT_ASSEMBLY_CANDIDATES', False)
    assert actual == project(tmp_path, source, ref, payload, role)


@pytest.mark.parametrize('case', ['nonassembly','unknown','mesh','source'])
def test_non_target_findings_and_empty_fields_keep_old_projection(tmp_path, monkeypatch, case):
    from adsl.agents import agent_feedback as a
    source, _, ref, payload = assembly_candidates_input(tmp_path)
    if case == 'nonassembly':
        payload.update(plan={}, fixed_assembly={}, assembly_context={})
    else:
        payload['typed_findings'][0]['finding_id'] = {
            'unknown':'unknown:body:CONTRACT','mesh':'assembly_mesh_evaluation:0:body:ERR',
            'source':'source_execution:body:ERR'}[case]
    actual = project(tmp_path, source, ref, payload)
    monkeypatch.setattr(a, '_COMPACT_ASSEMBLY_CANDIDATES', False)
    assert actual == project(tmp_path, source, ref, payload)


def test_same_features_in_distinct_findings_never_share_wrong_pointer(tmp_path):
    from adsl.agents import agent_feedback as a
    source, raw, ref, payload = assembly_candidates_input(tmp_path)
    second = deepcopy(raw); second['finding_id'] = 'assembly_topology:second:CONTRACT'
    second['source_candidates'][0]['evidence'] = ['Different original evidence']
    (tmp_path/'topology.json').write_text(json.dumps(dict(source_sha256=ref['source_sha256'],
        source_version='original', findings=[raw,second])))
    payload['typed_findings'].append(finding_for_agent(second, result_ref='topology.json',
        result_pointer='/findings/1',source_sha256=ref['source_sha256']))
    out = project(tmp_path, source, ref, payload)
    for i,f in enumerate(out['typed_findings']):
        info=f['preview_info']['source_candidates']; full=info['full_ref']
        assert full['json_pointer'] == f'/findings/{i}/source_candidates'
        assert a._at(json.loads((tmp_path/full['path']).read_text()), full['json_pointer']) == [raw,second][i]['source_candidates']
    assert len(out['typed_findings']) == 2


@pytest.mark.parametrize('metric', ['area','nsv'])
def test_compact_candidates_at_three_actual_runtime_boundaries(tmp_path, monkeypatch, metric):
    import asyncio
    from dataclasses import replace
    from types import SimpleNamespace
    from adsl.agents import assembly_topology as topology, assembly_physics as physics
    from adsl.agents.models import FixedAssemblyPlan, EngineeringCriticDecision, GradedCodeCriticDecision, CheckerResult
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.checkers import CheckerRun
    from test_fixed_assembly import mock_flow, plan_data
    from test_assembly_feedback_recovery import payload as model_payload, tool
    from adsl.agents.tools.files import read_file
    state = mock_flow(tmp_path, monkeypatch, [('PASS',True)])
    workflow, request, runtime, _, _ = state
    source, raw, ref, base = assembly_candidates_input(tmp_path)
    config = deepcopy(request.fixed_assembly)
    config['physics'] = {'overhang':{'support_metric':metric,'shape_editable':True}}
    request = replace(request, fixed_assembly=config)
    captures = {}
    async def run(**kw):
        data = model_payload(kw); stage = kw['stage'].split(':')[0]
        captures[stage] = data
        container = data if stage == 'assembly_engineering' else data['assembly_context'] if stage == 'code_critic' else data['feedback']
        f = container['typed_findings'][0]
        assert len(f['source_candidates']) == 6
        assert 'source_locations' not in f['source_candidates'][0]
        full = f['preview_info']['source_candidates']['full_ref']
        details = await tool(read_file, kw['context'], 'details', path=full['path'], json_pointer=full['json_pointer'])
        assert json.loads(details) == raw['source_candidates']
        if stage == 'assembly_engineering':
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['No justified proposal'], repair_proposals=[]))
        if stage == 'code_critic':
            await tool(read_file, kw['context'], 'source', path=data['assigned_source'])
            return SimpleNamespace(final_output=GradedCodeCriticDecision(approved=True,observations=[],issues=[]))
        return SimpleNamespace(final_output='{"edit_action":"NO_CHANGE","reason":"No appropriate edit"}')
    runtime.run = run
    plan = FixedAssemblyPlan.model_validate(plan_data())
    root = tmp_path/'review';root.mkdir()
    feedback = dict(typed_findings=base['typed_findings'], source_version='original', geometry_report_ref=ref,
                    checker_summary=[], evidence_files=[])
    if metric == 'nsv':
        feedback['support_evidence'] = dict(source_sha256=ref['source_sha256'], source_version='original',
            measurement_config_sha256='ray-config', support={'status':'PASS'}, items=[])
    context = dict(typed_findings=base['typed_findings'], report_ref=ref)
    result = CheckerResult(checker='assembly_topology',status='PASS',summary='stub',metrics={})
    check = CheckerRun(topology.checker_spec(),result,tmp_path/'checks',())
    async def exercise():
        # Empty context is the real Engineering-positive case without fixed_assembly at top level.
        await topology.engineer(workflow,runtime,request,plan,source,None,root,check,{},feedback,1)
        await ObjectWorkflow._review_generation_code(workflow,runtime=runtime,request=request,plan=plan,
            execution=None,workspace=tmp_path,source_path=source,round_number=1,max_rounds=2,
            round_root=root,code_critic={},image_decision=None,code_history=[],assembly_context=context)
        outcome = await ObjectWorkflow._repair(workflow,runtime=runtime,repairer={},workspace=tmp_path,
            source_path=source,role='coder',stage='repair:1',allow_no_change=True,
            payload=dict(fixed_assembly=config,plan=plan.model_dump(),assembly_context={},
                         feedback=feedback,source_version='original'))
        assert outcome['status'] == 'NO_CHANGE'
    asyncio.run(exercise())
    assert set(captures) == {'assembly_engineering','code_critic','repair'}
    assert source.read_text() == 'scene = None\n'


@pytest.mark.parametrize('container', ['feedback','assembly_context','pending_reviews','engineering_feedback','checker_evidence'])
@pytest.mark.parametrize('field', ['typed_findings','checker_evidence'])
def test_candidate_compaction_uses_only_known_recursive_containers(tmp_path, container, field):
    source, _, ref, payload = assembly_candidates_input(tmp_path)
    rows = payload.pop('typed_findings')
    payload['fixed_assembly'] = {'mm_per_unit':1}
    payload[container] = {field:rows}
    payload['task_data'] = {'typed_findings':deepcopy(rows)}
    out = project(tmp_path, source, ref, payload)
    f = out[container][field][0]
    assert 'source_locations' not in f['source_candidates'][0]
    assert f['preview_info']['source_candidates']['total'] == 6
    assert out['task_data'] == payload['task_data']
    assert project(tmp_path, source, ref, out) == out



def _assert_compact_reference_survives_invalid_evidence(tmp_path, *, modified):
    source, _, ref, payload = assembly_candidates_input(tmp_path)
    first = project(tmp_path, source, ref, payload)
    first_before = deepcopy(first)
    finding = first['typed_findings'][0]
    original_info = deepcopy(finding['preview_info']['source_candidates'])
    assert original_info['total'] == 6 and original_info['omitted_count'] == 0
    assert 'source_locations' not in finding['source_candidates'][0]
    snapshots = {p.name:p.read_bytes() for p in tmp_path.glob('diagnostics/agent_feedback/*.json')}
    evidence_path = tmp_path / original_info['full_ref']['path']
    if modified:
        document = json.loads(evidence_path.read_text())
        document['findings'][0]['source_candidates'][0]['evidence'] = ['Changed since projection']
        evidence_path.write_text(json.dumps(document))
    else:
        evidence_path.unlink()
    again = project(tmp_path, source, ref, first)
    current = again['typed_findings'][0]
    info = current['preview_info']['source_candidates']
    assert current['source_candidates'] == finding['source_candidates']
    assert (info['total'], info['omitted_count']) == (6, 0)
    for key in ('path','json_pointer','source_sha256','source_version','file_sha256'):
        assert info['full_ref'][key] == original_info['full_ref'][key]
    assert info['full_ref']['availability'] == 'UNAVAILABLE'
    assert info['full_ref']['reason'] == ('file_hash_mismatch' if modified else 'file_not_found')
    assert first == first_before
    assert project(tmp_path, source, ref, again) == again
    assert snapshots == {p.name:p.read_bytes() for p in tmp_path.glob('diagnostics/agent_feedback/*.json')}


def test_compact_full_reference_survives_deleted_original(tmp_path):
    _assert_compact_reference_survives_invalid_evidence(tmp_path, modified=False)


def test_compact_full_reference_survives_modified_original(tmp_path):
    _assert_compact_reference_survives_invalid_evidence(tmp_path, modified=True)
