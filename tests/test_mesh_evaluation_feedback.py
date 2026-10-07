"""Offline evaluation-error routing with real file tools and repair reservation."""
from dataclasses import replace
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from adsl.agents import assembly_topology as adapter, fixed_assembly as flow
from adsl.agents.models import EngineeringCriticDecision, RepairProposal, RepairTarget
from adsl.agents.models import FixedAssemblyPlan
from adsl.agents.service import ObjectWorkflow
from adsl.agents.source_index import parse_source_nodes
from adsl.agents.tools.files import read_file, apply_patch
from test_assembly_feedback_recovery import payload, tool
from test_assembly_topology import setup_flow
from test_fixed_assembly import run_flow, plan_data


def failure(code='TARGET_PRECISION_UNREPRESENTABLE'):
    stage = {'TARGET_PRECISION_UNREPRESENTABLE':'target_precision',
             'BOOLEAN_EVALUATION_FAILED':'internal_evaluation',
             'INPUT_GEOMETRY_INVALID':'input_geometry'}[code]
    kind = {'TARGET_PRECISION_UNREPRESENTABLE':'target_precision',
            'BOOLEAN_EVALUATION_FAILED':'geometry_evaluation',
            'INPUT_GEOMETRY_INVALID':'input_geometry'}[code]
    return {'code':'PART_DISPLAY_UNAVAILABLE', 'failure_kind':'candidate_evaluation',
        'part_id':'crossbar', 'stage':'evaluate_part', 'reason':code,
        'diagnostic':{'code':code, 'stage':stage, 'failure_kind':kind,
            'node_path':'Piece/arm/union', 'operation':'UNION', 'input_count':3,
            'metrics':{'zero_area_faces':2, 'open_edges':4},
            'attempted_actions':['local_retriangulation', 'precision_weld'],
            'source_ids':['source:unverified-diagnostic-claim']}}


def setup_evaluation_flow(tmp_path, monkeypatch, row, *, rounds=2, measured=True):
    state = setup_flow(tmp_path, monkeypatch, ['INDETERMINATE', 'PASS'][:rounds])
    w, request, runtime, source, calls = state
    request = replace(request, max_rounds=rounds,
                      checker_specs=request.checker_specs if measured else ())
    source.write_text('class Piece:\n    value = 1\n')
    original_execute = flow.execute_asset_source
    executed = []
    source_id = next(n.source_id for n in parse_source_nodes(source) if n.kind=='class')

    def execute(current, out, **kwargs):
        execution = original_execute(current, out, **kwargs)
        executed.append(execution)
        if len(executed) == 1:
            path = out/'assembly/assembly_manifest.json'
            report = json.loads(path.read_text())
            report.update(export_status='FAIL', status='NOT_EVALUATED', failures=[row],
                part_declarations=[{'id':'crossbar', 'components':['Piece']}],
                diagnostic={'display_available':False, 'missing_parts':['crossbar']})
            path.write_text(json.dumps(report))
            index = out/'source_index.json'
            index.write_text(json.dumps({'source_sha256':flow.file_hash(current), 'features':[
                {'feature_id':'feature:Piece', 'name':'Piece', 'resolution':'complete',
                 'source_ids':[source_id, 'source:forged-index-id'], 'source_locations':['L1-2']}]}))
            execution = replace(execution, source_index_path=index)
        return execution

    monkeypatch.setattr(flow, 'execute_asset_source', execute)
    return (w, request, runtime, source, calls), executed, source_id


@pytest.mark.parametrize('code', [
    'INPUT_GEOMETRY_INVALID', 'BOOLEAN_EVALUATION_FAILED', 'TARGET_PRECISION_UNREPRESENTABLE',
    'legacy_boolean'])
def test_saved_evaluation_error_engineer_coder_reexport_without_physical_fail(
        tmp_path, monkeypatch, code):
    row = failure(code) if code != 'legacy_boolean' else {
        'code':'PART_DISPLAY_UNAVAILABLE', 'failure_kind':'unknown', 'part_id':'crossbar',
        'stage':'evaluate_part', 'reason':"BOOLEAN_RECOVERY_FAILED: object='geometry_0' attempts=['not manifold']"}
    state, executed, source_id = setup_evaluation_flow(
        tmp_path, monkeypatch, row, measured=code != 'INPUT_GEOMETRY_INVALID')
    w, request, runtime, source, _ = state
    monkeypatch.setattr(w, '_repair', ObjectWorkflow._repair.__get__(w))
    model_calls = []

    async def model(**kwargs):
        data = payload(kwargs)
        model_calls.append(kwargs['role'])
        context = kwargs['context']
        await tool(read_file, context, 'read_source', path=data['assigned_source'])
        feedback = data if kwargs['role'].startswith('engineering') else data['feedback']
        finding = next(f for f in feedback['typed_findings']
                       if f['finding_id'].startswith('assembly_mesh_evaluation:'))
        assert finding['category']=='geometry_failure' and finding['repairability']=='geometry'
        assert finding['region']['part_names']==['crossbar']
        assert finding['region']['details']['physical_verdict']=='NOT_EVALUATED'
        assert finding['source_candidates'][0]['source_ids']==[source_id]
        assert 'source:unverified-diagnostic-claim' not in finding['source_candidates'][0]['source_ids']
        details = json.loads(await tool(read_file, context, 'read_evaluation',
            path=finding['result_ref'], json_pointer=finding['result_pointer']+'/domain'))
        assert details['geometry_repair_allowed']
        if code != 'legacy_boolean':
            assert finding['region']['details']['node_path']=='Piece/arm/union'
            assert finding['region']['details']['input_count']==3
            assert details['diagnostic']['metrics']['open_edges']==4
        if code != 'INPUT_GEOMETRY_INVALID':
            assert feedback['checker_summary'][0]['checker']=='assembly_topology'
            assert feedback['checker_summary'][0]['status']=='INDETERMINATE'
        if kwargs['role'].startswith('engineering'):
            assert data['evaluation_feedback']['failures'][0]['geometry_repair_allowed']
            assert data['evaluation_failure_ids'] == [data['evaluation_feedback']['failures'][0]['failure_id']]
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['Precision/evaluation cause is known; exact source design cause remains uncertain.'],
                repair_proposals=[RepairProposal(proposal_id='local_evaluation_repair',
                    finding_ids=[finding['finding_id']], hypothesis='a bounded local source correction',
                    target=RepairTarget(source_ids=[source_id], feature_ids=['feature:Piece'],
                                        allowed_scopes=['Piece']), action='reshape')]))
        assert data['current_repair_authorized'] and data['remaining_repairs_after_this_attempt']==0
        assert feedback['engineering_proposal']['proposal_id']=='local_evaluation_repair'
        text = await tool(read_file, context, 'read_candidate', path=data['assigned_source'])
        await tool(apply_patch, context, 'patch_candidate', path=data['assigned_source'],
                   old_text=text, new_text=text.replace('value = 1', 'value = 2'))
        return SimpleNamespace(final_output='Applied the bounded source change; recheck required.')

    runtime.run = model
    result, book = run_flow(state)
    assert result.approved and book['retained']=='attempt_0001'
    assert len(executed)==2 and len(model_calls)==2
    assert book['versions']['original']['reviews']['geometry']['status']=='NOT_EVALUATED'
    if code != 'INPUT_GEOMETRY_INVALID':
        assert book['versions']['original']['reviews']['assembly_topology']['status']=='INDETERMINATE'
    assert book['stop_reason']!='export_unassessed_no_geometry_repair'
    assert 'value = 2' in source.read_text()
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines())==1
    run_flow(state)
    assert len(model_calls)==2 and len(executed)==2


@pytest.mark.parametrize('row', [
    {'code':'PART_EXPORT_FAILED', 'failure_kind':'export', 'part_id':'crossbar',
     'stage':'write_glb', 'reason':'OSError: disk full'},
    {'code':'SHARED_ENVIRONMENT_UNAVAILABLE', 'failure_kind':'export',
     'stage':'external_executor', 'reason':'ImportError: bpy unavailable'},
    {'code':'PART_DISPLAY_UNAVAILABLE', 'failure_kind':'unknown', 'part_id':'crossbar',
     'stage':'evaluate_part', 'reason':'unknown evaluator exception'},
])
def test_file_environment_unknown_errors_do_not_authorize_source_geometry_edits(
        tmp_path, monkeypatch, row):
    state, executed, _ = setup_evaluation_flow(tmp_path, monkeypatch, row)
    async def forbidden(**kwargs):
        pytest.fail('No real evaluation finding; do not request Engineering or a source edit')
    state[2].run = forbidden
    result, book = run_flow(state)
    assert not result.approved and not state[-1] and len(executed)==1
    assert book['stop_reason']=='export_unassessed_no_geometry_repair'
    assert not book['feedback']['failure_feedback'][0]['geometry_repair_allowed']


def test_evaluation_no_proposal_no_change_respects_budget_and_completed_resume(tmp_path, monkeypatch):
    state, executed, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure())
    monkeypatch.setattr(state[0], '_repair', ObjectWorkflow._repair.__get__(state[0]))
    calls = []
    async def model(**kwargs):
        calls.append(kwargs['role'])
        data = payload(kwargs)
        await tool(read_file, kwargs['context'], 'read', path=data['assigned_source'])
        if kwargs['role'].startswith('engineering'):
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['No safe design correction follows from precision failure alone.'],
                repair_proposals=[]))
        assert data['feedback']['engineering']['status']=='NO_PROPOSAL'
        return SimpleNamespace(final_output='NO_CHANGE: preserve required geometry; no safe local correction')
    state[2].run = model
    result, book = run_flow(state)
    assert not result.approved and book['stop_reason']=='NO_CHANGE'
    assert len(calls)==2 and len(executed)==1 and book['retained']=='original'
    run_flow(state)
    assert len(calls)==2 and len(executed)==1


def test_evaluation_has_no_model_calls_when_repair_budget_is_exhausted(tmp_path, monkeypatch):
    state, executed, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure(), rounds=1)
    async def forbidden(**kwargs):
        pytest.fail('A classified failure must not create a fresh repair budget')
    state[2].run = forbidden
    result, book = run_flow(state)
    assert not result.approved and len(executed)==1 and not state[-1]
    assert book['stop_reason']=='round_budget_exhausted'


@pytest.mark.parametrize('mismatch', ['manifest', 'source_index'])
def test_evaluation_evidence_rejects_stale_source_provenance(tmp_path, mismatch):
    source = tmp_path/'source.py'
    source.write_text('class Piece:\n    value = 1\n')
    sid = next(n.source_id for n in parse_source_nodes(source) if n.kind=='class')
    index = tmp_path/'source_index.json'
    index.write_text(json.dumps({'source_sha256':'old' if mismatch=='source_index' else flow.file_hash(source),
        'features':[{'feature_id':'feature:Piece', 'name':'Piece', 'source_ids':[sid]}]}))
    report = {'source_sha256':'old' if mismatch=='manifest' else flow.file_hash(source),
              'part_declarations':[{'id':'crossbar', 'components':['Piece']}], 'failures':[failure()]}
    run = adapter.evaluation_evidence_run(report, flow._failure_feedback(report), source, tmp_path/'evidence', index)
    if mismatch=='manifest':
        assert run is None
    else:
        assert run.result.findings[0].source_candidates==[]


def test_stale_evaluation_manifest_cannot_fall_back_to_an_authorized_source_edit(tmp_path, monkeypatch):
    state, _, _ = setup_evaluation_flow(tmp_path, monkeypatch, failure())
    execute = flow.execute_asset_source
    def stale_manifest(*args, **kwargs):
        execution = execute(*args, **kwargs)
        path = execution.output_root/'assembly/assembly_manifest.json'
        report = json.loads(path.read_text())
        report['source_sha256']='stale'
        path.write_text(json.dumps(report))
        return execution
    monkeypatch.setattr(flow, 'execute_asset_source', stale_manifest)
    async def forbidden(**kwargs):
        pytest.fail('Stale evaluation evidence must not permit even the generic Coder fallback')
    state[2].run = forbidden
    result, book = run_flow(state)
    assert not result.approved and not state[-1]
    assert book['stop_reason']=='export_unassessed_no_geometry_repair'
    assert book['feedback']['failure_feedback'][0]['evidence_source_status']=='UNAVAILABLE_OR_SOURCE_MISMATCH'


@pytest.mark.parametrize('invalid', ['finding', 'source', 'feature'])
def test_engineering_evaluation_proposal_rejects_unverified_ids(tmp_path, monkeypatch, invalid):
    from adsl.agents.service import _checker_evidence
    state, _, source_id = setup_evaluation_flow(tmp_path, monkeypatch, failure())
    workflow, request, runtime, source, _ = state
    execution = flow.execute_asset_source(source, tmp_path/'asset',
        fixed_assembly=request.fixed_assembly, export_urdf=False)
    report = json.loads((execution.output_root/'assembly/assembly_manifest.json').read_text())
    run = adapter.evaluation_evidence_run(report, flow._failure_feedback(report), source,
        tmp_path/'evidence', execution.source_index_path)
    feedback = {**_checker_evidence([run], workspace=tmp_path),
                'source_version':'original', 'failure_feedback':flow._failure_feedback(report)}
    adapter.prepare_evidence(feedback, workspace=tmp_path, source_sha256=flow.file_hash(source))
    proposal = RepairProposal(proposal_id='unverified', finding_ids=[run.result.findings[0].finding_id],
        hypothesis='bounded local correction', target=RepairTarget(source_ids=[source_id],
            feature_ids=['feature:Piece'], allowed_scopes=['Piece']), action='reshape')
    if invalid=='finding': proposal.finding_ids=['assembly_mesh_evaluation:invented']
    if invalid=='source': proposal.target.source_ids=['source:forged-index-id']
    if invalid=='feature': proposal.target.feature_ids=['feature:invented']
    async def model(**kwargs):
        data = payload(kwargs)
        await tool(read_file, kwargs['context'], 'read_source', path=data['assigned_source'])
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
            observations=[], repair_proposals=[proposal]))
    runtime.run = model
    root = tmp_path/'engineering'
    root.mkdir()
    accepted = asyncio.run(adapter.engineer(workflow, runtime, request,
        FixedAssemblyPlan.model_validate(plan_data()), source, execution, root, run,
        flow._assembly_context(source, report, version_role='current_candidate'), feedback, 1))
    assert accepted is None and feedback['engineering']['status']=='UNAVAILABLE'
    assert (root/'proposal_rejected.json').is_file()


@pytest.mark.parametrize('code,stage,kind,allowed', [
    ('BOOLEAN_EVALUATION_FAILED','internal_evaluation','geometry_evaluation',True),
    ('INVALID_EVALUATED_MESH','internal_evaluation','geometry_evaluation',True),
    ('EMPTY_REQUIRED_GEOMETRY','internal_evaluation','geometry_evaluation',True),
    ('INVALID_TRANSFORM','input_geometry','input_geometry',True),
    ('INVALID_PRIMITIVE','input_geometry','input_geometry',True),
    ('INVALID_BOOLEAN_OPERATION','input_geometry','input_geometry',True),
    ('INPUT_GEOMETRY_INVALID','canonical_geometry','input_geometry',True),
    ('MESH_COMPONENTS_CHANGED','target_precision','geometry_evaluation',True),
    ('TARGET_PRECISION_UNREPRESENTABLE','target_precision','target_precision',True),
    ('GEOMETRY_DEPENDENCY_UNAVAILABLE','internal_evaluation','environment',False),
    ('PRIMITIVE_TESSELLATION_FAILED','internal_evaluation','environment',False),
    ('MATERIAL_PROVENANCE_UNAVAILABLE','internal_evaluation','geometry_evaluation',False),
])
def test_actual_core_error_codes_and_default_stages_are_classified(code, stage, kind, allowed):
    from adsl.core.export.mesh_validity import MeshEvaluationError
    error = MeshEvaluationError(code, 'offline classification fixture',
                                stage=stage, failure_kind=kind, node_path='part/0')
    # Match the exporter's current wrapper, retaining the full core diagnostic.
    report = {'source_sha256':'current', 'failures':[{
        'code':code, 'stage':stage, 'part_id':'crossbar', 'reason':str(error),
        'failure_kind':'export' if kind=='environment' else 'candidate_evaluation',
        'diagnostic':error.diagnostic}]}
    feedback = flow._failure_feedback(report, source_sha256='current')[0]
    assert feedback['geometry_repair_allowed'] is allowed


@pytest.mark.parametrize('path_source', ['node_path', 'operation_nodes'])
def test_real_generated_part_container_locates_verified_operation_descendants(
        tmp_path, path_source):
    """Archived source/index reproduces the unresolved Boolean part container."""
    from adsl.agents.source_index import build_source_index
    original = Path(__file__).resolve().parents[1]/'reports/benchmark_six_boolean_failure_20261007/source.py'
    source = tmp_path/'source.py'
    source.write_text(original.read_text())
    namespace = {}
    exec(compile(source.read_text(), str(source), 'exec'), namespace)
    index = build_source_index(source, namespace['scene']).model_dump()
    part = next(f for f in index['features'] if f['name']=='upper_structure')
    assert part['source_ids']==[] and part['resolution']=='unresolved'
    operation_path='BooleanUnion/op_0/connected_upper_components/op_1/continuous_rounded_arm'
    row = failure()
    row['part_id']='upper_structure'
    row['diagnostic']['node_path']=operation_path if path_source=='node_path' else 'BooleanUnion'
    if path_source=='operation_nodes':
        row['diagnostic']['operation_nodes']=[{
            'node_path':operation_path, 'operation':'UNION', 'input_count':53}]
    # A same-named helper in another print part is not a location candidate.
    arm = next(f for f in index['features'] if f['name']=='continuous_rounded_arm')
    outside = {**arm, 'feature_id':'feature:other_part/continuous_rounded_arm',
        'semantic_path':'FixedAssembly/base_body/op_0/connected_upper_components/op_1/continuous_rounded_arm'}
    index['features'].append(outside)
    path = tmp_path/'source_index.json'
    path.write_text(json.dumps(index))
    report = {'source_sha256':flow.file_hash(source), 'failures':[row],
        'part_declarations':[{'id':'upper_structure', 'components':[
            'upright_pole','curved_upper_arm','offset_lamp_head']}], 'status':'NOT_EVALUATED'}
    run = adapter.evaluation_evidence_run(report, flow._failure_feedback(report), source,
                                         tmp_path/'evidence', path)
    candidates = run.result.findings[0].source_candidates
    candidate_ids = {c.feature_id for c in candidates}
    assert arm['feature_id'] in candidate_ids and outside['feature_id'] not in candidate_ids
    locations = {span for c in candidates for span in c.source_locations}
    assert {'L58-L58','L69-L69'} <= locations
    verified_ids = {n.source_id for n in parse_source_nodes(source)}
    assert all(set(c.source_ids) <= verified_ids for c in candidates)
    assert all(c.ambiguous for c in candidates)
    # Route the real index-backed finding through the shared typed feedback.
    from adsl.agents.service import _checker_evidence, _actionable_findings
    assert _actionable_findings(run)==run.result.findings
    feedback = _checker_evidence([run], workspace=tmp_path)
    assert feedback['typed_findings'][0]['region']['part_names']==['upper_structure']
    assert any(c['source_ids'] for c in feedback['typed_findings'][0]['source_candidates'])
    path.write_text(json.dumps({**index, 'source_sha256':'stale'}))
    stale = adapter.evaluation_evidence_run(report, flow._failure_feedback(report), source,
                                           tmp_path/'stale_evidence', path)
    assert stale.result.findings[0].source_candidates==[]
