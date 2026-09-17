import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace
import pytest

from adsl.agents import service
from adsl.agents.checkers import CheckerRun
from adsl.agents.models import CheckerResult, CheckerSpec
from adsl.agents.utils.execution import ExecutionResult
from experiments.planned_checks import run_prompt_batch as prompt


@pytest.mark.parametrize('fea_status',['PASS','FAIL','INDETERMINATE','ERROR','MISSING'])
def test_dynamic_joint_publication_does_not_trust_initial_user_input(tmp_path,monkeypatch,fea_status):
    from dataclasses import replace
    from adsl.agents.models import PlannedEngineeringCriticDecision
    from test_overhang_candidate_isolation import fixture,run_case
    f=fixture(tmp_path,monkeypatch)
    # This is the real prompt handoff shape: original input had no checker mode.
    (f.workspace/'user_input.json').write_text(json.dumps({'overhang_experiment':{}}))
    f.options.update(mode='planned_checks',protection_policy='explicit_task_constraints',protection={})
    fea=CheckerSpec(name='fea',command=['unused'],required=True)
    f.kwargs['request']=replace(f.kwargs['request'],checker_specs=(*f.kwargs['request'].checker_specs,fea))
    original_check=service.run_checkers
    def check(specs,**kw):
        runs=original_check(specs,**kw)
        if fea_status!='MISSING':
            runs.append(CheckerRun(fea,CheckerResult(checker='fea',status=fea_status,summary='FEA result'),kw['round_root']/'checkers/fea',()))
        return runs
    monkeypatch.setattr(service,'run_checkers',check)
    old=f.kwargs['runtime'].run
    async def model(**kw):
        if kw['role'].startswith('engineering'):
            return SimpleNamespace(final_output=PlannedEngineeringCriticDecision(approved=False,
                observations=['no safe local proposal'],repair_proposals=[],stop_category='no_reasonable_plan'))
        return await old(**kw)
    f.kwargs['runtime'].run=model
    result,book=run_case(f)
    published=json.loads((f.workspace/'checker_results.json').read_text())
    assert result.approved is (fea_status=='PASS')
    assert published['required_checkers_passed'] is (fea_status=='PASS')
    assert f.manifest['approved'] is result.approved
    assert published['planned_checks'] is True
    assert book['retained']=='original'


def test_both_arms_start_from_same_prompt_without_old_assets(tmp_path):
    case={'case_id':'SF03','prompt':'A wooden chair'}
    a=prompt.request_for(case,tmp_path/'adsl');b=prompt.request_for(case,tmp_path/'ours')
    assert a.requirement==b.requirement==case['prompt']
    assert not a.image_paths and not b.image_paths
    assert not a.overhang_experiment and not b.overhang_experiment
    assert a.max_rounds==b.max_rounds==4
    assert a.workspace!=b.workspace


@pytest.mark.parametrize('calibration_ok',[True,False])
@pytest.mark.parametrize('edit_status',['CHANGED','NO_CHANGE','TOOL_ERROR'])
def test_first_executable_own_model_enters_same_round_joint_flow(tmp_path,monkeypatch,calibration_ok,edit_status):
    workspace=tmp_path/'ours/SF03';render=workspace/'rounds/round_02/render';render.mkdir(parents=True)
    source=workspace/'source.py';source.write_text('class OwnChair:\n    pass\n')
    for name in ['scene.glb','scene.urdf','view.png']:(render/name).write_text('own model')
    (workspace/'runtime_config.json').write_text(json.dumps({
        'request':{'overhang_experiment':{},'max_rounds':4},'mode':'generate'}))
    execution=ExecutionResult(render,render/'scene.glb',render/'scene.urdf',(render/'view.png',),'','')
    case={'case_id':'SF03','prompt':'A wooden chair'}
    spec=CheckerSpec(name='overhang',command=['unused'],required=False)
    independent=[CheckerSpec(name=n,command=['unused']) for n in ('topology','standing','fea')]
    def registry(case,path):
        return {'overhang_fixed_print':{'spec':spec.model_dump(),'configuration':{'case':{}},
            'config_path':str(path/'overhang.json')}}
    monkeypatch.setattr(prompt.run,'registry',registry)
    calls=[]
    def measure(spec,**kw):
        calls.append(kw)
        dest=kw['round_root']/'checkers'/spec.name;dest.mkdir(parents=True)
        result=(CheckerResult(checker='overhang',status='PASS' if calibration_ok else 'INDETERMINATE',summary='measured' if calibration_ok else 'EXTERIOR_UNVERIFIED',metrics={
            'measurement':{'scale_mm_per_source_unit':100} if calibration_ok else None,
            'overhang_area_mm2':10 if calibration_ok else None}) if spec.name=='overhang'
            else CheckerResult(checker=spec.name,status='PASS',summary='evaluated'))
        prompt.run.write_json(dest/'result.json',result.model_dump())
        return CheckerRun(spec,result,dest,())
    monkeypatch.setattr(prompt,'run_checker',measure)
    async def plan(root,cid,profile):
        assert (root/cid/'original/source.py').read_text()==source.read_text()
        prompt.run.write_json(root/cid/'tool_plan.resolved.json',{'tools':[{'selected':True,'spec':s.model_dump()} for s in [spec,*independent]]})
    monkeypatch.setattr(prompt.run,'plan_case_bounded',plan)
    monkeypatch.setattr(prompt.run,'verify_plan',lambda p:None)
    async def iterate(self,**kw):
        assert kw['mode']=='generate'
        assert kw['runtime'] is runtime
        assert kw['request'].max_rounds==4
        assert kw['request'].overhang_experiment['initial_round']==2
        assert kw['request'].check_first
        assert kw['source_path']==source
        config=json.loads((workspace/'runtime_config.json').read_text())
        assert config['request']['overhang_experiment']==kw['request'].overhang_experiment
        assert config['request']['max_rounds']==4 and config['mode']=='generate'
        assert config['request']['checker_specs']==[s.model_dump() for s in kw['request'].checker_specs]
        assert config['request']['check_first'] is True
        assert config['request']['repair_policy']==kw['request'].repair_policy.model_dump()
        from adsl.agents.cli import _resume_request
        restored=_resume_request(SimpleNamespace(output=workspace,checker_config=[],repair_policy_config=None,
            overhang_experiment_config=None,requirement=None,task_id=None,max_rounds=5))
        assert restored.checker_specs==kw['request'].checker_specs
        assert restored.max_rounds==4 and restored.check_first
        assert restored.repair_policy==kw['request'].repair_policy
        assert 'overhang_experiment' not in config
        runs=service.run_checkers((spec,*independent),execution=execution,source_path=source,round_root=workspace/'rounds/round_02')
        assert runs[0].output_dir.is_relative_to(workspace)
        assert len(calls)==4  # Calibration reused, three independent tools run.
        assert {r.spec.name for r in runs}=={'overhang','topology','standing','fea'}
        if not calibration_ok:
            overhang=next(r.result for r in runs if r.spec.name=='overhang')
            assert overhang.status=='INDETERMINATE' and overhang.metrics['overhang_area_mm2'] is None
            source.write_text('class ChangedChair:\n    pass\n')
            later=service.run_checkers((spec,*independent),execution=execution,source_path=source,round_root=workspace/'candidate')
            assert next(r for r in later if r.spec.name=='overhang').result.status=='INDETERMINATE'
            assert all(r.result.status=='PASS' for r in later if r.spec.name!='overhang')
        # Exercise the real _repair return contract after the real handoff writes
        # configuration. Mock only the model; a preconfigured fixture hid this bug.
        candidate=workspace/'candidate/source.py'
        candidate.parent.mkdir(exist_ok=True)
        candidate.write_text(source.read_text())
        original_text=source.read_text()
        prompt.run.write_json(workspace/'overhang_versions.json',{'attempts':{
            'attempt_0001':{'status':'EDITING','candidate':'candidate/source.py'}}})
        outcome=await self._repair(runtime=runtime,repairer=None,workspace=workspace,
            source_path=candidate,role='coder:engineering-candidate:2:1',
            stage='engineering:2:1',payload={},reserved_attempt_id='attempt_0001')
        assert outcome['status']==edit_status
        assert (outcome['before_sha256']!=outcome['after_sha256']) is (edit_status=='CHANGED')
        assert source.read_text()==original_text
        assert json.loads((workspace/'overhang_versions.json').read_text())['attempts']['attempt_0001']['status']=='MODEL_STARTED'
        return 'joint entered'
    monkeypatch.setattr(service.ObjectWorkflow,'_iterate',iterate)
    workflow=prompt.PromptWorkflow.__new__(prompt.PromptWorkflow)
    workflow.case=case;workflow.deadline=time.time()+10000
    async def coder(**kw):
        assert kw['context'].record_noop_patch
        assert 'no_change_contract' in json.loads(kw['input'])
        if edit_status=='TOOL_ERROR':
            raise RuntimeError('mock tool error')
        if edit_status=='CHANGED':
            ctx=kw['context']
            ctx.source_path.write_text('class RepairedChair:\n    pass\n')
            ctx.record('apply_patch',ctx.source_path,changed=True)
            return SimpleNamespace(final_output='edited')
        return SimpleNamespace(final_output={'edit_action':'NO_CHANGE','reason':'no safe proposal'})
    runtime=SimpleNamespace(run=coder)
    result=asyncio.run(workflow._initialize_generated_checks(runtime=runtime,
        request=prompt.request_for(case,workspace),workspace=workspace,source_path=source,
        execution=execution,round_number=2,plan=SimpleNamespace()))
    assert result=='joint entered'


@pytest.mark.parametrize('scenario',['improvement','missing_topology','all_invalid','program_error','tool_error'])
def test_real_handoff_joint_loop_and_publication(tmp_path,monkeypatch,scenario):
    from dataclasses import replace
    from adsl.agents.feedback_schema import stable_hash
    from test_overhang_candidate_isolation import fixture
    from test_overhang_local_edit import measured
    action={'program_error':TypeError('mock programming error'),
            'tool_error':RuntimeError('mock tool failure')}.get(scenario,80)
    f=fixture(tmp_path,monkeypatch,actions=(action,))
    workspace=f.workspace
    (workspace/'runtime_config.json').write_text(json.dumps({'request':{
        'checker_specs':[],'check_first':False,'overhang_experiment':{}}}))
    specs=[CheckerSpec(name=n,command=['unused'],required=n!='overhang')
           for n in ('topology','standing','overhang','fea')]
    def registry(case,path):
        return {'overhang_fixed_print':{'spec':specs[2].model_dump(),'configuration':{'case':{}},
                                      'config_path':str(path/'overhang.json')}}
    calls=[]
    def check(spec,**kw):
        dest=kw['round_root']/'checkers'/spec.name;dest.mkdir(parents=True,exist_ok=True)
        value=kw['execution'].glb_path.read_text();calls.append((spec.name,value))
        # A zero-area baseline stops optimization in the missing-plan scenarios.
        area=0 if scenario in ('missing_topology','all_invalid') else 100 if value=='GLB:0' else 80
        result=measured(area) if spec.name=='overhang' else CheckerResult(checker=spec.name,status='PASS',summary='mock pass')
        if spec.name=='overhang':result.metrics['measurement']={'scale_mm_per_source_unit':100}
        prompt.run.write_json(dest/'result.json',result.model_dump())
        return CheckerRun(spec,result,dest,())
    async def plan(root,cid,profile):
        tools=[]
        for spec in specs:
            invalid=scenario=='all_invalid' or (scenario=='missing_topology' and spec.name=='topology')
            tools.append({'name':spec.name,'selected':not invalid,'required':spec.name in ('topology','overhang'),
                'status':'PLAN_INVALID' if invalid else 'SELECTED','reason':'mock plan',
                'spec':None if invalid else spec.model_dump()})
        p={'tools':tools};p['sha256']=stable_hash(p)
        prompt.run.write_json(root/cid/'tool_plan.resolved.json',p)
    monkeypatch.setattr(prompt.run,'registry',registry)
    monkeypatch.setattr(prompt,'run_checker',check)
    monkeypatch.setattr(prompt.run,'plan_case_bounded',plan)
    workflow=prompt.PromptWorkflow.__new__(prompt.PromptWorkflow)
    workflow.case={'case_id':workspace.name,'prompt':'chair'};workflow.deadline=time.time()+1000
    request=replace(prompt.request_for(workflow.case,workspace),max_rounds=1 if scenario=='improvement' else 4)
    result=asyncio.run(workflow._initialize_generated_checks(runtime=f.kwargs['runtime'],request=request,
        workspace=workspace,source_path=workspace/'source.py',execution=f.original,round_number=1,plan=f.kwargs['plan']))
    book=prompt.run.read(workspace/'overhang_versions.json')
    publication=prompt.run.read(workspace/'checker_results.json')
    retained=book['versions'][book['retained']]
    assert result.source_path.read_bytes()==Path(retained['source']).read_bytes()
    assert result.glb_path.read_bytes()==Path(retained['execution']['glb_path']).read_bytes()
    assert publication['record_hash']==retained['record_hash']
    if scenario=='improvement':
        assert book['retained']=='attempt_0001' and result.approved
        assert {n for n,v in calls if v=='GLB:1'}=={s.name for s in specs}
    elif scenario in ('missing_topology','all_invalid'):
        assert not result.approved and not publication['required_checkers_passed']
        assert 'topology' in publication['unverified_checks'] and not book.get('error')
        assert any(t['name']=='topology' for t in publication['unverified_plan_tools'])
        assert book['retained']=='original'
        if scenario=='missing_topology':
            assert ('standing','GLB:0') in calls and not any(n=='fea' for n,v in calls)
            assert publication['checker_statuses']['fea']=='INDETERMINATE'
    else:
        assert len(book['attempts'])==1 and book['retained']=='original'
        attempt=book['attempts']['attempt_0001']
        if scenario=='program_error':
            assert attempt['status']=='FLOW_ERROR' and book['error']['type']=='TypeError'
            assert Path(attempt['report_path']).is_file()
            assert prompt.failure_status(book['error']['type'])=='FLOW_ERROR'
        else:
            assert attempt['status']=='TOOL_ERROR' and not book.get('error')


@pytest.mark.parametrize('failure,starts',[('TypeError',1),('InternalServerError',2)])
def test_batch_stops_for_program_error_not_single_case_api_failure(tmp_path,monkeypatch,failure,starts):
    from experiments.overhang_feedback import run_pilot
    from experiments.standing_fea_30 import run_batch
    monkeypatch.setattr(run_pilot,'runtime_environment',lambda p:{})
    monkeypatch.setattr(run_batch,'checker_environment',lambda e:e)
    monkeypatch.setattr(prompt,'summarize',lambda root:None)
    prompt.run.write_json(tmp_path/'batch.json',{'deadline':time.time()+100,'cases':['SF01','SF03']})
    calls=[]
    def popen(command,**kw):
        cid=command[command.index('--cases')+1];calls.append(cid)
        prompt.run.write_json(tmp_path/'statuses'/f'{cid}_ours.json',{'case_id':cid,'arm':'ours',
            'status':prompt.failure_status(failure),'error_type':failure})
        return SimpleNamespace(wait=lambda **kwargs:0)
    monkeypatch.setattr(prompt.subprocess,'Popen',popen)
    prompt.batch(tmp_path,['SF01','SF03'])
    assert len(calls)==starts
