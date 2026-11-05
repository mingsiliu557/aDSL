"""Ablation contracts with fake runtime/executor; no model or simulation."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import subprocess

import pytest
from adsl.agents import fixed_assembly as flow
from adsl.agents.models import ObjectRequest, ObjectPlan, FixedAssemblyPlan, FixedAssemblyConfig, RepairPolicy, CheckerSpec
from adsl.agents.service import ObjectWorkflow
from adsl.agents.prompts import object_prompt, support_prompt_options, ROLE_PROMPTS
from adsl.agents.agent_feedback import payload_for_agent
from adsl.agents.tools.context import AgentToolContext
from adsl.agents.utils.ablation_source import check_profile_source, DisabledProfileAPI
from test_fixed_assembly import CONFIG, plan_data, mock_flow, run_flow


def request(tmp_path, variant, **config):
    return ObjectRequest('make the pictured object',tmp_path,'test',fixed_assembly={**CONFIG,'ablation':variant,**config},
        checker_specs=tuple(CheckerSpec(name=n,command=['stub']) for n in ('assembly_topology','assembly_overhang')) if variant != 'review' else ())


def test_config(tmp_path):
    assert flow.ablation({}) is None
    with pytest.raises(ValueError): FixedAssemblyConfig.model_validate({**CONFIG,'ablation':'invalid'})
    for variant in ('tracing','actions','profile_ops'):
        ObjectWorkflow._validate_request(request(tmp_path,variant))
        with pytest.raises(ValueError): ObjectWorkflow._validate_request(request(tmp_path,variant,print_mode='single'))
    good=request(tmp_path,'review',validation_mode='visual_only')
    ObjectWorkflow._validate_request(good)
    for bad in (request(tmp_path,'review'),replace(good,checker_specs=(CheckerSpec(name='assembly_standing',command=['stub']),)),
                request(tmp_path,'review',validation_mode='visual_only',physics={'overhang':{'support_metric':'nsv'}}),
                request(tmp_path,'review',validation_mode='visual_only',physics={'overhang':{'partition_objective':{}}})):
        with pytest.raises(ValueError):ObjectWorkflow._validate_request(bad)
    for bad in (request(tmp_path,'fab_plan'),request(tmp_path,'fab_plan',print_mode='single')):
        with pytest.raises(ValueError):ObjectWorkflow._validate_request(bad)


@pytest.mark.parametrize('role',list(ROLE_PROMPTS))
@pytest.mark.parametrize('metric,shape',[('area',False),('nsv',False),('nsv',True)])
def test_default_prompt_exact_baseline(role,metric,shape):
    # Baseline is pinned and read only; no baseline code is modified.
    root=Path(__file__).resolve().parents[1]
    original=subprocess.check_output(['git','show','e5e13fb:adsl-agents/prompts.py'],cwd=root,text=True)
    namespace={'__name__':'adsl.agents._baseline_prompts','__package__':'adsl.agents','__file__':str(root/'adsl-agents/prompts.py')}
    exec(compile(original,'baseline_prompts','exec'),namespace)
    opts=dict(articulation=False,fixed_assembly=True,support_metric=metric,shape_editable=shape)
    assert object_prompt(role,**opts)==namespace['object_prompt'](role,**opts)
    assert object_prompt(role,**opts,ablation=None)==namespace['object_prompt'](role,**opts)


@pytest.mark.parametrize('role',['planner','coder','engineering_critic','code_critic','code_critic_review','debugger'])
def test_profile_prompt(role):
    text=object_prompt(role,articulation=False,fixed_assembly=True,ablation='profile_ops')
    for token in ('Polygon','linear_extrude','rotate_extrude','loft','hull'):assert token not in text


@pytest.mark.parametrize('role',['coder','code_critic','code_critic_review'])
@pytest.mark.parametrize('metric',['area','nsv'])
def test_actions_prompt(role,metric):
    text=object_prompt(role,articulation=False,fixed_assembly=True,support_metric=metric,shape_editable=True,ablation='actions')
    assert 'Edit code directly' in text
    assert 'Do not change print-part grouping' in text
    assert 'assembly.connect' in text


@pytest.mark.parametrize('code',[
    'from adsl.core import loft', 'from adsl.core import loft as L\nL([])',
    'import adsl.core as core\ncore.loft([])', 'from adsl import core\ncore.loft([])',
    'from adsl.core import *\nloft([])', 'import adsl.core\nadsl.core.loft([])',
])
def test_profile_ast(code):
    with pytest.raises(DisabledProfileAPI,match='loft'):check_profile_source(code)
    check_profile_source('from adsl.core import Cube\nscene=Cube((1,1,1))')


def test_profile_error_survives_summary(tmp_path):
    from adsl.agents.utils.asset_executor import _execute_generated_source
    from adsl.agents.feedback_schema import execution_failure_summary
    source=tmp_path/'source.py';source.write_text('from adsl.core import loft as L')
    with pytest.raises(DisabledProfileAPI):_execute_generated_source(source,tmp_path/'asset',ablation='profile_ops')
    raw=json.loads((tmp_path/'asset/source_execution_error.json').read_text())
    summary=execution_failure_summary(raw,source_path=source)
    assert summary['code']=='GENERATED_SOURCE_EXECUTION_FAILED'
    assert summary['reason']=='ABLATION_DISABLED_API' and summary['name']=='loft'


@pytest.mark.parametrize('role',['engineering','coder','code_critic','image_critic'])
def test_tracing_payload_and_permissions(tmp_path,role):
    from adsl.agents.tools.files import read_file
    from test_assembly_feedback_recovery import tool
    source=tmp_path/'source.py';source.write_text('scene=None')
    raw={'feedback':{'source_candidates':[{'feature_id':'nose','source_locations':[{'line':3}]}],
                     'part_id':'head','region':{'center':[1,2,3]},'metric':.125,
                     'full_ref':{'path':'secret.json'}}}
    before=deepcopy(raw)
    (tmp_path/'secret.json').write_text(json.dumps(raw))
    (tmp_path/'source_index.json').write_text('{}')
    ctx=AgentToolContext(tmp_path,source,ablation='tracing')
    kw=dict(workspace=tmp_path,source_path=source,source_version='original',role=role,ablation='tracing',tool_context=ctx)
    out=payload_for_agent(raw,**kw)
    assert raw==before
    assert out['feedback']['part_id']=='head' and out['feedback']['metric']==.125
    for token in ('source_candidates','source_locations','feature_id','secret.json'):assert token not in json.dumps(out)
    assert payload_for_agent(out,**kw)==out
    logs=list((tmp_path/'ablation_private').glob('*.json'));assert logs and 'line' in logs[0].read_text()
    for path in ['secret.json','source_index.json',str(logs[0])]:
        result=asyncio.run(tool(read_file,ctx,'denied',path=path))
        assert 'ABLATION_READ_DENIED' in result
    for path in ['source.py',out['feedback']['full_ref']['path']]:
        result=asyncio.run(tool(read_file,ctx,'allowed',path=path))
        assert 'ABLATION_READ_DENIED' not in result


def test_fab_planner_ordinary_input(tmp_path):
    req=request(tmp_path,'fab_plan',print_mode='single',physics={'overhang':{'support_metric':'nsv','shape_editable':True,'layer_height_mm':.2,'overhang_threshold_from_horizontal_deg':45}})
    req=replace(req,repair_policy=RepairPolicy(print_orientation_editable=True))
    ObjectWorkflow._validate_request(req)
    data=plan_data(); obj=ObjectPlan.model_validate(data)
    runtime=SimpleNamespace(agent=lambda **kw:kw,run=AsyncMock(return_value=SimpleNamespace(final_output=obj)))
    plan=asyncio.run(ObjectWorkflow.__new__(ObjectWorkflow)._plan_generation(runtime,req))
    call=runtime.run.call_args.kwargs
    assert call['agent']['output_type'] is ObjectPlan
    assert call['agent']['instructions']==object_prompt('planner',articulation=False)
    assert call['input']==req.requirement
    flow.check_single_plan(plan)
    assert plan.root_part=='body' and plan.print_parts[0].components==[c.name for c in obj.components]
    assert runtime.run.await_count==1


@pytest.mark.parametrize('variant',['review','actions'])
def test_engineering_entry_disabled(tmp_path,variant):
    from adsl.agents.assembly_topology import engineer
    feedback={}
    runtime=SimpleNamespace(agent=lambda **kw:pytest.fail('Engineering must not be created'))
    assert asyncio.run(engineer(None,runtime,request(tmp_path,variant),None,None,None,None,None,None,feedback,2)) is None
    assert feedback['engineering']['status']=='DISABLED_ABLATION'


@pytest.mark.parametrize('variant',[None,'tracing','actions','profile_ops'])
def test_flow_records_and_hash(tmp_path,monkeypatch,variant):
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    req=replace(state[1],fixed_assembly={**CONFIG,'ablation':variant})
    state=(state[0],req,*state[2:])
    result,book=run_flow(state)
    assert json.loads((tmp_path/'assembly_result.json').read_text())['ablation']==variant
    import hashlib
    frozen={**CONFIG,'assembly_plan':FixedAssemblyPlan.model_validate(plan_data()).model_dump()}
    if variant is not None:frozen['ablation']=variant
    expected=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
    assert book['config_sha256']==expected


def test_profile_missing_marker():
    from adsl.agents.prompts import _ablation_prompt
    with pytest.raises(ValueError,match='boundary'): _ablation_prompt('changed docs',role='coder',variant='profile_ops')


@pytest.mark.parametrize('variant',['profile_ops','fab_plan','tracing'])
def test_initial_resume_actual_prompts_and_plan_file(tmp_path,monkeypatch,variant):
    cfg={'print_mode':'single','physics':{'overhang':{'support_metric':'nsv','shape_editable':True,'layer_height_mm':.2,'overhang_threshold_from_horizontal_deg':45}}} if variant=='fab_plan' else {}
    req=request(tmp_path/'case',variant,**cfg)
    if variant=='fab_plan': req=replace(req,repair_policy=RepairPolicy(print_orientation_editable=True))
    captures=[];planners=[]
    class Runtime:
        usage=SimpleNamespace(update_manifest=lambda **kw:None)
        def agent(self,**kw):return kw
        async def run(self,**kw):
            if kw['role']=='planner':
                planners.append(kw)
                cls=ObjectPlan if variant=='fab_plan' else FixedAssemblyPlan
                return SimpleNamespace(final_output=cls.model_validate(plan_data()))
            captures.append(kw)
            ctx=kw['context'];ctx.source_path.write_text('stub source')
            ctx.record('write_file',ctx.source_path)
            return SimpleNamespace(final_output='done')
    w=ObjectWorkflow.__new__(ObjectWorkflow)
    monkeypatch.setattr(w,'_runtime',lambda *a,**kw:Runtime())
    async def stop(**kw):return 'stopped before executor'
    monkeypatch.setattr(w,'_iterate',stop)
    assert asyncio.run(w.generate(req))=='stopped before executor'
    saved=json.loads((req.workspace/'plan.json').read_text())
    if variant=='fab_plan':flow.check_single_plan(FixedAssemblyPlan.model_validate(saved))
    (req.workspace/'source.py').write_text('')
    (req.workspace/'checkpoint.json').write_text(json.dumps({'mode':'generate','stage':'planned'}))
    assert asyncio.run(w.resume(req))=='stopped before executor'
    assert len(planners)==1 and len(captures)==2
    for capture in captures:
        if variant=='profile_ops':
            assert all(name not in capture['agent']['instructions'] for name in ('Polygon','loft','hull','linear_extrude','rotate_extrude'))
        elif variant=='tracing':assert capture['context'].ablation=='tracing'
        else: assert 'automatically chooses' in capture['agent']['instructions']


@pytest.mark.parametrize('variant',['review','actions','profile_ops'])
def test_real_coder_receives_failure_and_repairs(tmp_path,monkeypatch,variant):
    from adsl.agents import assembly_topology
    from adsl.agents.utils.execution import AssetExecutionError
    from adsl.agents.tools.files import read_file,apply_patch
    from test_assembly_feedback_recovery import tool
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    cfg={**CONFIG,'ablation':variant}
    if variant=='review':cfg['validation_mode']='visual_only'
    req=replace(state[1],fixed_assembly=cfg,checker_specs=(),max_rounds=2)
    ObjectWorkflow._validate_request(replace(req,checker_specs=request(tmp_path,variant).checker_specs))
    state=(state[0],req,*state[2:])
    execute=flow.execute_asset_source;executions=[];captured=[]
    def run(src,out,**kw):
        executions.append(src.read_text())
        if len(executions)==1:
            if variant=='profile_ops':
                raise AssetExecutionError('disabled operator',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
                    stage='source_execution',exception_type='DisabledProfileAPI',
                    message='ABLATION_DISABLED_API: loft',reason='ABLATION_DISABLED_API',name='loft',
                    source_sha256=flow.file_hash(src),source_locations=[dict(path=str(src),line=1,function='<module>')]))
            raise AssetExecutionError('stub export failed')
        result=execute(src,out,**kw)
        if variant=='review':
            path=out/'assembly/assembly_manifest.json';data=json.loads(path.read_text())
            data.update(status='NOT_EVALUATED',export_status='PASS',diagnostic={'complete':True});path.write_text(json.dumps(data))
        return result
    monkeypatch.setattr(flow,'execute_asset_source',run)
    monkeypatch.setattr(assembly_topology,'engineer',AsyncMock(side_effect=AssertionError('must not invoke engineer')))
    monkeypatch.setattr(state[0],'_repair',ObjectWorkflow._repair.__get__(state[0]))
    async def model(**kw):
        payload=json.loads(kw['input']);captured.append(payload)
        if variant=='profile_ops':
            assert 'ABLATION_DISABLED_API: loft' in json.dumps(payload)
            assert 'loft' not in kw['agent']['instructions']
        else:assert payload['feedback']['engineering']['status']=='DISABLED_ABLATION'
        ctx=kw['context'];old=await tool(read_file,ctx,'read',path=payload['assigned_source'])
        await tool(apply_patch,ctx,'patch',path=payload['assigned_source'],old_text=old,new_text='repaired source')
        return SimpleNamespace(final_output='fixed')
    state[2].run=model
    result,book=run_flow(state)
    assert result.approved and len(captured)==1 and len(executions)==2


def test_actions_optional_stops_without_engineering_or_edit(tmp_path,monkeypatch):
    from test_support_workflow import support_fixture
    from adsl.agents import assembly_topology
    state,checks,captured=support_fixture(tmp_path,monkeypatch,[12,10])
    req=replace(state[1],fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'})
    state=(state[0],req,*state[2:])
    monkeypatch.setattr(assembly_topology,'engineer',AsyncMock(side_effect=AssertionError('disabled')))
    result,book=run_flow(state)
    assert result.approved and len(checks)==2 and captured
    assert book['stop_reason'] != 'no_reasonable_support_proposal'
    assert book['feedback']['engineering']['status']=='DISABLED_ABLATION'
    assert 'support_checkpoints' in book


def test_legacy_checkpoint_resumes_with_null_ablation(tmp_path,monkeypatch):
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    _,old=run_flow(state)
    req=replace(state[1],fixed_assembly={**CONFIG,'ablation':None})
    _,new=run_flow((state[0],req,*state[2:]))
    assert old['config_sha256']==new['config_sha256']
    assert old['next_round']==new['next_round']


def test_tracing_structured_findings_and_symlink(tmp_path):
    from test_agent_feedback import assembly_candidates_input
    from test_assembly_feedback_recovery import tool
    from adsl.agents.tools.files import read_file
    source,raw,ref,payload=assembly_candidates_input(tmp_path,count=6)
    ctx=AgentToolContext(tmp_path,source,ablation='tracing')
    kw=dict(workspace=tmp_path,source_path=source,source_version='original',role='engineering',
            report_ref=ref,ablation='tracing',tool_context=ctx)
    out=payload_for_agent(payload,**kw)
    assert 'source_candidates' not in json.dumps(out)
    assert out['typed_findings'][0]['finding_id']==payload['typed_findings'][0]['finding_id']
    assert payload_for_agent(out,**kw)==out
    alias=tmp_path/'alias.json';alias.symlink_to(tmp_path/ref['path'])
    assert 'ABLATION_READ_DENIED' in asyncio.run(tool(read_file,ctx,'alias',path='alias.json'))


@pytest.mark.parametrize('role',['engineering','coder','code_critic','image_critic'])
def test_tracing_at_actual_role_entry(tmp_path,role):
    from test_agent_feedback import assembly_candidates_input
    from test_assembly_feedback_recovery import tool,payload as unpack
    from adsl.agents.tools.files import read_file
    from adsl.agents.models import EngineeringCriticDecision,GradedCodeCriticDecision,GradedImageCriticDecision,CheckerResult
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.utils.execution import ExecutionResult
    from adsl.agents.assembly_topology import engineer,checker_spec
    source,raw,ref,data=assembly_candidates_input(tmp_path,count=6)
    req=request(tmp_path,'tracing');w=ObjectWorkflow.__new__(ObjectWorkflow)
    root=tmp_path/'round';root.mkdir()
    render=tmp_path/'view.png';render.write_bytes(b'stub')
    execution=ExecutionResult(tmp_path,tmp_path/'scene.glb',None,(render,),'','')
    captures=[]
    async def call(**kw):
        sent=unpack(kw);captures.append(sent)
        assert 'source_candidates' not in json.dumps(sent)
        assert 'source_locations' not in json.dumps(sent)
        assert 'feature_id' not in json.dumps(sent)
        if role!='image_critic':
            assert kw['context'].ablation=='tracing'
            denied=await tool(read_file,kw['context'],'hidden',path=ref['path'])
            assert 'ABLATION_READ_DENIED' in denied
            allowed=await tool(read_file,kw['context'],'summary',path=next(iter(kw['context'].sanitized_evidence_paths)).relative_to(tmp_path).as_posix())
            assert 'ABLATION_READ_DENIED' not in allowed
        if role=='engineering':result=EngineeringCriticDecision(approved=False,observations=[],repair_proposals=[])
        elif role=='code_critic':result=GradedCodeCriticDecision(approved=True,observations=[],issues=[])
        elif role=='image_critic':result=GradedImageCriticDecision(approved=True,observations=[],issues=[])
        else:result={'edit_action':'NO_CHANGE','reason':'stub'}
        return SimpleNamespace(final_output=result)
    rt=SimpleNamespace(agent=lambda **kw:kw,run=call)
    plan=FixedAssemblyPlan.model_validate(plan_data())
    if role=='engineering':
        run=CheckerRun(checker_spec(),CheckerResult(checker='assembly_topology',status='PASS',summary='stub'),root,())
        asyncio.run(engineer(w,rt,req,plan,source,execution,root,run,{},data,1))
    elif role=='coder':
        asyncio.run(w._repair(runtime=rt,repairer={},workspace=tmp_path,source_path=source,
            role='coder',stage='stub',payload=data,allow_no_change=True,ablation='tracing'))
    elif role=='code_critic':
        asyncio.run(w._review_generation_code(runtime=rt,request=req,plan=plan,execution=execution,
            workspace=tmp_path,source_path=source,round_number=1,max_rounds=2,round_root=root,
            code_critic={},image_decision=None,code_history=[],assembly_context=data))
    else:
        asyncio.run(w._review_generation_image(runtime=rt,request=req,plan=plan,execution=execution,
            source_path=source,source_version='original',round_number=1,max_rounds=2,round_root=root,
            image_critic={},image_history=[],code_critic_corrections=[],assembly_context=data))
        assert 'fixed_assembly' not in captures[0]
    assert len(captures)==1



def test_profile_basic_source_reaches_executor_stub(tmp_path,monkeypatch):
    from adsl.agents.utils import asset_executor
    source=tmp_path/'source.py';source.write_text('from adsl.core import Cube\nscene=Cube((1,1,1))')
    class StubAsset: pass
    scene=StubAsset();calls=[]
    monkeypatch.setattr(asset_executor,'Asset',StubAsset)
    def run(path,**kw): calls.append(path);return {'scene':scene}
    monkeypatch.setattr(asset_executor.runpy,'run_path',run)
    result=asset_executor._execute_generated_source(source,tmp_path/'output',ablation='profile_ops')
    assert result['scene'] is scene and calls==[str(source)]


def test_executor_cli_reads_frozen_ablation_before_source(tmp_path,monkeypatch):
    from adsl.agents.utils import asset_executor
    config=tmp_path/'fixed.json';config.write_text(json.dumps({**CONFIG,'ablation':'profile_ops'}))
    source=tmp_path/'source.py';source.write_text('not executed')
    args=SimpleNamespace(source=source,output=tmp_path/'output',fixed_assembly_config=config,render_only=False)
    monkeypatch.setattr(asset_executor,'_parser',lambda:SimpleNamespace(parse_args=lambda argv:args))
    class StopBeforeExecution(Exception):pass
    def execute(*a,**kw):
        assert kw['fixed_assembly'] and kw['ablation']=='profile_ops'
        raise StopBeforeExecution()
    monkeypatch.setattr(asset_executor,'_execute_generated_source',execute)
    with pytest.raises(StopBeforeExecution): asset_executor.main([])
