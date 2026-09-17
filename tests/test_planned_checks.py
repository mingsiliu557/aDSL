import asyncio
import json
from types import SimpleNamespace

import pytest

from adsl.agents.planned_checks import ToolPlan, resolve_plan, verify_plan, PlanValidationError, PLANNER_INSTRUCTIONS
from adsl.agents.repair_policy import assess_candidate
from adsl.agents.models import CheckerResult, RepairPolicy
from experiments.planned_checks.token_budget import BudgetModel


def test_baseline_batch_order_and_completed_case_not_replayed(tmp_path, monkeypatch):
    from experiments.planned_checks import run
    assert run.measurement_case_ids('smoke') == ('SF03', 'SF07', 'SF27')
    order = run.measurement_case_ids('measure-only')
    assert order[:3] == run.measurement_case_ids('smoke')
    assert len(order) == len(set(order)) == 12
    assert set(order) == set(run.IDS)
    folder = tmp_path / 'SF03'
    folder.mkdir()
    (folder / 'state.json').write_text(json.dumps({'status': 'MEASURED'}))
    monkeypatch.setattr(run, 'run_checker', lambda *a, **k: pytest.fail('replayed completed case'))
    run.measure_case(tmp_path, 'SF03')


def fixtures():
    names=('topology','standing','overhang','fea')
    reg={n:{'tool':n,'applicable':True,'configuration':{},
            'spec':{'name':n,'command':['unused'],'required':n!='overhang'}} for n in names}
    p=ToolPlan(object_use='floor-standing chair',tools=[{'name':n,'selected':True,
        'profile_id':n,'reason':'configured use'} for n in names])
    return p,reg


def test_plan_frozen_and_tamper_rejected():
    p,reg=fixtures();r=resolve_plan(p,reg);verify_plan(r)
    r['tools'][0]['spec']['timeout_seconds']=1
    with pytest.raises(ValueError,match='changed'):verify_plan(r)


@pytest.mark.parametrize('kind',['duplicate','profile','required','not_applicable'])
def test_invalid_plan(kind):
    p,reg=fixtures()
    if kind=='duplicate':p.tools[0]=p.tools[1]
    if kind=='profile':p.tools[0].profile_id='invented'
    if kind=='required':p.tools[0].selected=False
    if kind=='not_applicable':reg['fea']['applicable']=False
    with pytest.raises(ValueError):resolve_plan(p,reg)


def test_missing_conditions_distinct_from_not_applicable():
    p,reg=fixtures()
    p.tools[-1].selected=False;p.tools[-1].profile_id=None;p.tools[-1].skip_reason='NEEDS_SPEC'
    r=resolve_plan(p,reg)
    assert r['tools'][-1]['status']=='NEEDS_SPEC' and r['tools'][-1]['spec'] is None


def test_sf03_all_errors_reported_in_single_correction():
    p,reg=fixtures()
    p.tools[0].selected=False;p.tools[0].skip_reason='NEEDS_SPEC'
    p.tools[-1].selected=False;p.tools[-1].skip_reason='NEEDS_SPEC'
    with pytest.raises(PlanValidationError) as caught:resolve_plan(p,reg)
    errors=caught.value.errors
    assert any('topology.selected' in e for e in errors)
    assert any('topology.profile_id' in e for e in errors)
    assert any('fea.profile_id' in e for e in errors)
    assert p.tools[-1].profile_id=='fea'  # Do not silently sanitize the proposal.
    p.tools[0].selected=True;p.tools[0].skip_reason=None
    p.tools[-1].profile_id=None
    resolved=resolve_plan(p,reg)
    assert resolved['tools'][-1]['status']=='NEEDS_SPEC'
    assert resolved['tools'][-1]['spec'] is None


def test_selected_tool_cannot_keep_skip_reason():
    p,reg=fixtures();p.tools[-1].skip_reason='NEEDS_SPEC'
    with pytest.raises(PlanValidationError,match='fea.skip_reason'):resolve_plan(p,reg)


def test_prompt_and_schema_explain_conditional_fields():
    from adsl.agents.planned_checks import PlannedTool
    fields=PlannedTool.model_json_schema()['properties']
    assert 'selected=false' in fields['profile_id']['description']
    assert 'selected=true' in fields['skip_reason']['description']
    assert 'resolve ALL validation_errors' in PLANNER_INSTRUCTIONS
    assert '"selected":false,"profile_id":null' in PLANNER_INSTRUCTIONS


def overhang(area,status='PASS'):
    return CheckerResult(checker='overhang',status=status,summary='measurement',metrics={
        'overhang_area_mm2':area,'area_uncertainty_mm2':1,'measurement':{'backend':'manifold'},
        'print_extent_mm':[1,1,1]})


def decide(before,after,**kw):
    return assess_candidate(before,after,target_finding_ids=[],appearance_approved=kw.get('appearance',True),
        protection={'status':kw.get('protection','PASS')},policy=RepairPolicy(),planned_checks=True)


def test_soft_improvement_and_no_effect():
    assert decide([overhang(20)],[overhang(10)]).accepted
    assert not decide([overhang(20)],[overhang(19)]).accepted


def test_joint_targeted_topology_partial_progress_and_regression_guard():
    from adsl.agents.models import CheckerFinding
    finding=CheckerFinding(finding_id='topology:gap',rule_id='ONE_PIECE_DISCONNECTED',
        category='geometry_failure',repairability='geometry')
    before=CheckerResult(checker='topology',status='FAIL',summary='disconnected',findings=[finding],
        metrics={'mode':'one_piece','component_count':20})
    after=before.model_copy(update={'metrics':{'mode':'one_piece','component_count':5}})
    standing=CheckerResult(checker='standing',status='PASS',summary='stable')
    def check(targets=('topology:gap',),appearance=True,stand=standing):
        return assess_candidate([before,standing,overhang(20)],[after,stand,overhang(30)],
            target_finding_ids=targets,appearance_approved=appearance,policy=RepairPolicy(),
            protection={'status':'PASS'},planned_checks=True)
    assert check().accepted
    assert 'still FAIL' in check().target_improvements[-1]
    assert not check(targets=('local_edit',)).accepted
    assert not check(appearance=False).accepted
    assert not check(stand=standing.model_copy(update={'status':'INDETERMINATE'})).accepted


@pytest.mark.parametrize('targets',[['topology:gap'],['overhang:optimization:0']])
def test_overhang_cannot_offset_increased_disconnected_components(targets):
    from adsl.agents.models import CheckerFinding
    finding=CheckerFinding(finding_id='topology:gap',rule_id='ONE_PIECE_DISCONNECTED',
        category='geometry_failure',repairability='geometry')
    before=CheckerResult(checker='topology',status='FAIL',summary='disconnected',findings=[finding],
        metrics={'mode':'one_piece','component_count':2})
    after=before.model_copy(update={'metrics':{'mode':'one_piece','component_count':5}})
    decision=assess_candidate([before,overhang(100)],[after,overhang(80)],target_finding_ids=targets,
        appearance_approved=True,policy=RepairPolicy(),planned_checks=True,protection={'status':'PASS'})
    assert not decision.accepted and '2 -> 5' in decision.regressions[0]


@pytest.mark.parametrize('after_status',['FAIL','INDETERMINATE','ERROR'])
def test_soft_cannot_hide_hard_regression(after_status):
    a=CheckerResult(checker='standing',status='PASS',summary='baseline')
    b=a.model_copy(update={'status':after_status})
    assert not decide([a,overhang(20)],[b,overhang(10)]).accepted


def test_unavailable_stays_unavailable_can_accept_other_improvement():
    a=CheckerResult(checker='fea',status='INDETERMINATE',summary='mesh unavailable')
    decision=decide([a,overhang(20)],[a,overhang(10)])
    assert decision.accepted and 'fea' in decision.unavailable_checks
    assert not decide([a,overhang(20)],[a,overhang(20)]).accepted


def test_protection_and_measurement_failure():
    assert not decide([overhang(20)],[overhang(10)],protection='INDETERMINATE').accepted
    assert not decide([overhang(20)],[overhang(10)],appearance=False).accepted
    assert not decide([overhang(20)],[overhang(None,'INDETERMINATE')]).accepted


class FakeModel:
    calls=0
    async def get_response(self,*args,**kwargs):
        self.calls+=1
        return SimpleNamespace(usage=SimpleNamespace(input_tokens=8,output_tokens=2))


def test_request_budget_settles_without_duplicate_usage(tmp_path):
    m=FakeModel();b=BudgetModel(m,tmp_path,request_bound=100,evidence={'test':True})
    asyncio.run(b.get_response());asyncio.run(b.get_response())
    state=json.loads((tmp_path/'cliproxy_token_budget.json').read_text())
    assert m.calls==2 and sum(r['charged'] for r in state['requests'].values())==20


def test_budget_exhaustion_prevents_call(tmp_path):
    m=FakeModel();b=BudgetModel(m,tmp_path,request_bound=100,evidence={'test':True})
    b.update(lambda s:s.update(limit=99))
    with pytest.raises(RuntimeError,match='EXHAUSTED'):asyncio.run(b.get_response())
    assert m.calls==0


def test_unknown_usage_blocks_replay(tmp_path):
    class Broken(FakeModel):
        async def get_response(self,*a,**k):
            self.calls+=1;raise TimeoutError('unknown consumption')
    m=Broken();b=BudgetModel(m,tmp_path,request_bound=100,evidence={'test':True})
    with pytest.raises(TimeoutError):asyncio.run(b.get_response())
    with pytest.raises(RuntimeError,match='UNRESOLVED'):asyncio.run(b.get_response())
    assert m.calls==1


def test_stepcode_not_charged_to_new_api_limit(tmp_path):
    step=BudgetModel(FakeModel(),tmp_path,request_bound=100,evidence={'test':True},budget_scope='stepcode')
    api=BudgetModel(FakeModel(),tmp_path,request_bound=100,evidence={'test':True},budget_scope='cliproxy')
    # Exhaust the new API ledger; StepCode must still be allowed and separate.
    api.update(lambda s:s.update(limit=1))
    asyncio.run(step.get_response())
    with pytest.raises(RuntimeError,match='EXHAUSTED'):asyncio.run(api.get_response())
    assert json.loads((tmp_path/'cliproxy_token_budget.json').read_text())['requests']=={}
    state=json.loads((tmp_path/'stepcode_usage.json').read_text())
    assert state['limit'] is None and sum(r['charged'] for r in state['requests'].values())==10


def test_historical_stepcode_ledger_is_not_imported_into_new_api_budget(tmp_path):
    historical={'limit':100_000_000,'requests':{'old':{'charged':100_000_000}}}
    (tmp_path/'token_budget.json').write_text(json.dumps(historical))
    api=BudgetModel(FakeModel(),tmp_path,request_bound=100,evidence={'test':True})
    asyncio.run(api.get_response())
    assert json.loads((tmp_path/'token_budget.json').read_text())==historical


def test_fea_contract_includes_existing_solver_support_and_material_assumption():
    from adsl.agents.planned_checks import FEA_SCREENING_ASSUMPTIONS
    assert 'minimum-Z' in FEA_SCREENING_ASSUMPTIONS['support']
    assert 'all three translational DOFs' in FEA_SCREENING_ASSUMPTIONS['support']
    assert 'NOT itself grounds for NEEDS_SPEC' in PLANNER_INSTRUCTIONS
    assert 'Missing or inapplicable' in PLANNER_INSTRUCTIONS


def test_invalid_fea_plan_keeps_three_independent_tools():
    from adsl.agents.planned_checks import resolve_partial_plan
    p,reg=fixtures();p.tools[-1].selected=False;p.tools[-1].skip_reason='NEEDS_SPEC'
    r=resolve_partial_plan(p,reg);verify_plan(r)
    assert r['status']=='PLAN_PARTIAL'
    assert [t['status'] for t in r['tools']]==['SELECTED','SELECTED','SELECTED','PLAN_INVALID']
    assert r['tools'][-1]['spec'] is None


@pytest.mark.parametrize('problem',['missing','duplicate','invalid_type','broken_profile'])
def test_individual_entry_failure_isolated(problem):
    from adsl.agents.planned_checks import resolve_partial_plan
    p,reg=fixtures();raw=p.model_dump()
    if problem=='missing':raw['tools'].pop()
    if problem=='duplicate':raw['tools'].append(raw['tools'][-1])
    if problem=='invalid_type':raw['tools'][-1]['selected']='invalid boolean'
    if problem=='broken_profile':reg['fea']['spec']={}
    r=resolve_partial_plan(raw,reg)
    assert all(t['status']=='SELECTED' for t in r['tools'][:3])
    assert r['tools'][-1]['status']=='PLAN_INVALID'


def test_correction_keeps_previously_valid_decisions():
    from adsl.agents.planned_checks import resolve_partial_plan,keep_valid_plan_entries
    p,reg=fixtures();p.tools[-1].profile_id='unknown'
    previous=resolve_partial_plan(p,reg)
    correction=p.model_dump();correction['tools'][-1]['profile_id']='fea'
    correction['tools'][0]['profile_id']='new invalid topology'
    r=resolve_partial_plan(keep_valid_plan_entries(previous,correction),reg)
    assert r['status']=='PLAN_VALID'
    assert r['tools'][0]['profile_id']=='topology'


def test_topology_plan_failure_blocks_only_fea_in_real_measure_loop(tmp_path,monkeypatch):
    import time
    from experiments.planned_checks import run
    from adsl.agents.planned_checks import resolve_partial_plan
    from adsl.agents.utils.io import write_json
    p,reg=fixtures();p.tools[0].profile_id='unknown'
    root=tmp_path;folder=root/'SF03'
    write_json(root/'batch.json',{'deadline':time.time()+10000})
    write_json(folder/'state.json',{'status':'PLAN_PARTIAL','input_hashes':{},'planning_seconds':0})
    write_json(folder/'tool_plan.resolved.json',resolve_partial_plan(p,reg))
    write_json(folder/'registry.json',{})
    monkeypatch.setattr(run,'hashes',lambda *a:{})
    monkeypatch.setattr(run,'original_execution',lambda *a:None)
    executed=[]
    def checker(spec,**kwargs):
        executed.append(spec.name)
        status='ERROR' if spec.name=='standing' else 'PASS'
        return SimpleNamespace(result=CheckerResult(checker=spec.name,status=status,summary='mock'),output_dir=folder/spec.name)
    monkeypatch.setattr(run,'run_checker',checker)
    run.measure_case(root,'SF03')
    assert executed==['standing','overhang']
    assert json.loads((folder/'measurement_topology.json').read_text())['status']=='PLAN_INVALID'
    assert json.loads((folder/'measurement_fea.json').read_text())['reason']=='TOPOLOGY_DEPENDENCY_UNAVAILABLE'
    state=json.loads((folder/'state.json').read_text())
    assert state['retained']=='original' and state['joint_pass'] is False


def test_sdk_output_keeps_strict_request_schema_but_isolates_bad_response_field():
    from agents import AgentOutputSchema
    from adsl.agents.planned_checks import ToolPlanOutputSchema,resolve_partial_plan
    p,reg=fixtures();payload=p.model_dump();payload['tools'][-1]['selected']='bad boolean'
    schema=ToolPlanOutputSchema()
    assert schema.json_schema()==AgentOutputSchema(ToolPlan).json_schema()
    received=schema.validate_json(json.dumps(payload))
    result=resolve_partial_plan(received,reg)
    assert [t['status'] for t in result['tools']]==['SELECTED','SELECTED','SELECTED','PLAN_INVALID']


def test_unparseable_whole_response_not_guessed():
    from agents.exceptions import ModelBehaviorError
    from adsl.agents.planned_checks import ToolPlanOutputSchema
    with pytest.raises(ModelBehaviorError):ToolPlanOutputSchema().validate_json('not json')


def test_stepcode_unknown_usage_preserved_but_independent_case_allowed(tmp_path):
    class Broken(FakeModel):
        async def get_response(self, *a, **k):
            self.calls += 1
            raise TimeoutError('unknown consumption')
    model = Broken()
    broken = BudgetModel(model,tmp_path,request_bound=100,evidence={'case':'SF05'},budget_scope='stepcode')
    with pytest.raises(TimeoutError):asyncio.run(broken.get_response())
    with pytest.raises(RuntimeError,match='UNRESOLVED'):asyncio.run(broken.get_response())
    good = BudgetModel(FakeModel(),tmp_path,request_bound=100,evidence={'case':'SF06'},budget_scope='stepcode')
    asyncio.run(good.get_response())
    rows = json.loads((tmp_path/'stepcode_usage.json').read_text())['requests'].values()
    assert sorted(r['status'] for r in rows) == ['RESERVED','SETTLED']
    assert model.calls == 1


@pytest.mark.parametrize('kind', ['api', 'output', 'timeout'])
def test_planning_request_failure_does_not_block_other_cases(tmp_path, monkeypatch, kind):
    import time
    import httpx
    from openai import InternalServerError
    from agents.exceptions import ModelBehaviorError
    from experiments.planned_checks import run
    run.write_json(tmp_path/'batch.json', {'deadline':time.time()+100})
    for cid in ('SF05','SF06'):
        run.write_json(tmp_path/cid/'state.json', {'status':'INPUT_READY','planning_seconds':0,'retained':'original'})
    errors = {'api':InternalServerError('upstream_unavailable',response=httpx.Response(502,
        request=httpx.Request('POST','http://localhost')),body=None),
        'output':ModelBehaviorError('invalid JSON'), 'timeout':asyncio.TimeoutError()}
    seen = []
    async def fake(root,cid,profile):
        seen.append(cid)
        if cid == 'SF05':raise errors[kind]
    monkeypatch.setattr(run,'plan_case',fake)
    for cid in ('SF05','SF06'):asyncio.run(run.plan_case_bounded(tmp_path,cid,None))
    assert seen == ['SF05','SF06']
    state = run.read(tmp_path/'SF05/state.json')
    assert state['status']=='PLAN_ERROR' and state['retained']=='original'
    assert run.read(tmp_path/'SF05/planning_failure.json')['retry_performed'] is False


def test_planning_programming_error_still_stops_batch(tmp_path, monkeypatch):
    import time
    from experiments.planned_checks import run
    run.write_json(tmp_path/'batch.json', {'deadline':time.time()+100})
    run.write_json(tmp_path/'SF03/state.json', {'planning_seconds':0})
    async def broken(*a):raise ValueError('frozen asset changed')
    monkeypatch.setattr(run,'plan_case',broken)
    with pytest.raises(ValueError,match='asset changed'):
        asyncio.run(run.plan_case_bounded(tmp_path,'SF03',None))


def test_correction_failure_preserves_first_partial_plan(tmp_path, monkeypatch):
    import time
    from agents.exceptions import ModelBehaviorError
    from experiments.planned_checks import run
    from adsl.agents.planned_checks import resolve_partial_plan
    run.write_json(tmp_path/'batch.json', {'deadline':time.time()+100})
    run.write_json(tmp_path/'SF03/state.json', {'planning_seconds':0})
    p, reg = fixtures()
    p.tools[-1].profile_id='unknown'
    partial = resolve_partial_plan(p,reg)
    run.write_json(tmp_path/'SF03/tool_plan.partial.0.json',partial)
    async def broken(*a):raise ModelBehaviorError('correction invalid')
    monkeypatch.setattr(run,'plan_case',broken)
    asyncio.run(run.plan_case_bounded(tmp_path,'SF03',None))
    assert run.read(tmp_path/'SF03/tool_plan.resolved.json')==partial
    assert run.read(tmp_path/'SF03/state.json')['status']=='PLAN_PARTIAL'


def test_stepcode_retries_transient_api_with_separate_usage_debits(tmp_path,monkeypatch):
    import httpx
    from openai import InternalServerError
    from experiments.planned_checks import token_budget
    delays=[]
    async def sleep(delay):delays.append(delay)
    monkeypatch.setattr(token_budget.asyncio,'sleep',sleep)
    class Flaky(FakeModel):
        async def get_response(self,*a,**k):
            self.calls+=1
            if self.calls<3:raise InternalServerError('unavailable',response=httpx.Response(502,
                request=httpx.Request('POST','http://localhost')),body=None)
            return SimpleNamespace(usage=SimpleNamespace(input_tokens=8,output_tokens=2))
    m=Flaky();b=BudgetModel(m,tmp_path,request_bound=100,evidence={'test':True},budget_scope='stepcode')
    asyncio.run(b.get_response())
    rows=json.loads((tmp_path/'stepcode_usage.json').read_text())['requests'].values()
    assert m.calls==3 and delays==[30,60]
    assert sum(r['charged'] for r in rows)==210


def test_new_api_error_keeps_full_bound_and_allows_remaining_budget(tmp_path):
    from openai import APIConnectionError
    import httpx
    class Broken(FakeModel):
        async def get_response(self,*a,**k):
            raise APIConnectionError(request=httpx.Request('POST','http://localhost'))
    broken=BudgetModel(Broken(),tmp_path,request_bound=100,evidence={'test':True})
    with pytest.raises(APIConnectionError):asyncio.run(broken.get_response())
    good=BudgetModel(FakeModel(),tmp_path,request_bound=100,evidence={'test':True})
    asyncio.run(good.get_response())
    rows=json.loads((tmp_path/'cliproxy_token_budget.json').read_text())['requests'].values()
    assert sum(r['charged'] for r in rows)==110
    assert {r['status'] for r in rows}=={'SETTLED','BOUNDED_UNKNOWN'}


def test_edit_batch_timeout_is_case_local_and_completed_not_replayed(tmp_path,monkeypatch):
    import time
    import subprocess
    from experiments.planned_checks import run_edit_smoke as edit
    monkeypatch.setattr(edit.run,'prepare',lambda *a,**k:None)
    edit.run.write_json(tmp_path/'batch.json',{'deadline':time.time()+100})
    for cid in ['SF03','SF07']:
        edit.run.write_json(tmp_path/cid/'state.json',{'status':'INPUT_READY'})
        (tmp_path/cid/'original').mkdir()
        (tmp_path/cid/'original/source.py').write_text('original')
    calls=[];stopped=[]
    class Process:
        def __init__(self,command,**kwargs):
            self.cid=command[-1];calls.append(self.cid)
        def wait(self,timeout):
            if self.cid=='SF03':raise subprocess.TimeoutExpired('mock',timeout)
            edit.run.write_json(tmp_path/self.cid/'edit_status.json',{'status':'COMPLETED','retained':'original'})
            return 0
    monkeypatch.setattr(edit.subprocess,'Popen',Process)
    monkeypatch.setattr(edit,'stop_tree',lambda p:stopped.append(p.cid))
    edit.batch(tmp_path,['SF03','SF07'])
    edit.batch(tmp_path,['SF03','SF07'])
    assert calls==['SF03','SF07'] and stopped==['SF03']
    assert edit.run.read(tmp_path/'SF03/edit_status.json')['status']=='TIMEOUT'
    assert edit.run.read(tmp_path/'SF07/edit_status.json')['status']=='COMPLETED'
    assert (tmp_path/'SF03/original/source.py').read_text()=='original'


@pytest.mark.parametrize('matched',[True,False])
def test_historical_geometry_restore_requires_matching_source_and_assets(tmp_path,matched):
    from experiments.planned_checks import run_edit_smoke as edit
    folder=tmp_path/'SF03';original=folder/'original';original.mkdir(parents=True)
    history=tmp_path/'history';round_root=history/'rounds/round_02'
    render=round_root/'render';render.mkdir(parents=True)
    for name in ['source.py','scene.glb','scene.urdf']:(original/name).write_text(name)
    for name in ['scene.glb','scene.urdf']:(render/name).write_text(name)
    if not matched:(render/'scene.glb').write_text('different version')
    edit.run.write_json(round_root/'analysis_geometry.json',{'source_sha256':edit.run.sha(original/'source.py')})
    edit.run.write_json(folder/'state.json',{'historical_asset':str(history),'input_hashes':edit.run.hashes(original)})
    if matched:
        edit.restore_geometry_evidence(folder)
        assert (original/'analysis_geometry.json').is_file()
        assert (original/'scene.glb').read_text()=='scene.glb'
    else:
        with pytest.raises(ValueError,match='no source/GLB/URDF-matched'):edit.restore_geometry_evidence(folder)
        assert not (original/'analysis_geometry.json').exists()


def test_worker_does_not_overwrite_restored_input_hashes(tmp_path,monkeypatch):
    import time
    from experiments.planned_checks import run_edit_smoke as edit
    folder=tmp_path/'SF01';original=folder/'original';original.mkdir(parents=True)
    (original/'source.py').write_text('unchanged')
    edit.run.write_json(tmp_path/'batch.json',{'deadline':time.time()+1800})
    edit.run.write_json(folder/'state.json',{'status':'INPUT_READY','input_hashes':edit.run.hashes(original)})
    config_path=folder/'print.json'
    edit.run.write_json(folder/'registry.json',{'overhang_fixed_print':{
        'spec':{'name':'overhang','command':['mock']},'configuration':{'case':{}},'config_path':str(config_path)}})
    edit.run.write_json(folder/'calibration_result.json',{'checker':'overhang','status':'PASS',
        'summary':'cached','metrics':{'measurement':{'scale_mm_per_source_unit':1}}})
    def restore(path):
        (original/'analysis_geometry.json').write_text('{}')
        state=edit.run.read(path/'state.json');state['input_hashes']=edit.run.hashes(original)
        edit.run.write_json(path/'state.json',state)
    class ReachedPlanner(Exception):pass
    async def planner(*args):
        state=edit.run.read(folder/'state.json')
        assert state['input_hashes']==edit.run.hashes(original)
        assert 'allowed_classes' not in state['protection']
        assert 'policy_notes' in state['protection']
        raise ReachedPlanner()
    monkeypatch.setattr(edit,'restore_geometry_evidence',restore)
    monkeypatch.setattr(edit.run,'plan_case_bounded',planner)
    with pytest.raises(ReachedPlanner):edit.worker(tmp_path,'SF01')
