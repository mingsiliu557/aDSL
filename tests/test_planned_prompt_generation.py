import asyncio
import json
import time
from pathlib import Path
from types import SimpleNamespace

from adsl.agents import service
from adsl.agents.checkers import CheckerRun
from adsl.agents.models import CheckerResult, CheckerSpec
from adsl.agents.utils.execution import ExecutionResult
from experiments.planned_checks import run_prompt_batch as prompt


def test_both_arms_start_from_same_prompt_without_old_assets(tmp_path):
    case={'case_id':'SF03','prompt':'A wooden chair'}
    a=prompt.request_for(case,tmp_path/'adsl');b=prompt.request_for(case,tmp_path/'ours')
    assert a.requirement==b.requirement==case['prompt']
    assert not a.image_paths and not b.image_paths
    assert not a.overhang_experiment and not b.overhang_experiment
    assert a.max_rounds==b.max_rounds==4
    assert a.workspace!=b.workspace


def test_first_executable_own_model_enters_same_round_joint_flow(tmp_path,monkeypatch):
    workspace=tmp_path/'ours/SF03';render=workspace/'rounds/round_02/render';render.mkdir(parents=True)
    source=workspace/'source.py';source.write_text('class OwnChair:\n    pass\n')
    for name in ['scene.glb','scene.urdf','view.png']:(render/name).write_text('own model')
    (workspace/'runtime_config.json').write_text('{}')
    execution=ExecutionResult(render,render/'scene.glb',render/'scene.urdf',(render/'view.png',),'','')
    case={'case_id':'SF03','prompt':'A wooden chair'}
    spec=CheckerSpec(name='overhang',command=['unused'],required=False)
    def registry(case,path):
        return {'overhang_fixed_print':{'spec':spec.model_dump(),'configuration':{'case':{}},
            'config_path':str(path/'overhang.json')}}
    monkeypatch.setattr(prompt.run,'registry',registry)
    calls=[]
    def measure(spec,**kw):
        calls.append(kw)
        dest=kw['round_root']/'checkers/overhang';dest.mkdir(parents=True)
        result=CheckerResult(checker='overhang',status='PASS',summary='measured',metrics={
            'measurement':{'scale_mm_per_source_unit':100},'overhang_area_mm2':10})
        prompt.run.write_json(dest/'result.json',result.model_dump())
        return CheckerRun(spec,result,dest,())
    monkeypatch.setattr(prompt,'run_checker',measure)
    async def plan(root,cid,profile):
        assert (root/cid/'original/source.py').read_text()==source.read_text()
        prompt.run.write_json(root/cid/'tool_plan.resolved.json',{'tools':[{'selected':True,'spec':spec.model_dump()}]})
    monkeypatch.setattr(prompt.run,'plan_case_bounded',plan)
    monkeypatch.setattr(prompt.run,'verify_plan',lambda p:None)
    async def iterate(self,**kw):
        assert kw['mode']=='generate'
        assert kw['runtime'] is runtime
        assert kw['request'].max_rounds==4
        assert kw['request'].overhang_experiment['initial_round']==2
        assert kw['request'].check_first
        assert kw['source_path']==source
        runs=service.run_checkers((spec,),execution=execution,source_path=source,round_root=workspace/'rounds/round_02')
        assert runs[0].output_dir.is_relative_to(workspace)
        assert len(calls)==1  # No duplicate calibration.
        return 'joint entered'
    monkeypatch.setattr(service.ObjectWorkflow,'_iterate',iterate)
    workflow=prompt.PromptWorkflow.__new__(prompt.PromptWorkflow)
    workflow.case=case;workflow.deadline=time.time()+10000
    runtime=object()
    result=asyncio.run(workflow._initialize_generated_checks(runtime=runtime,
        request=prompt.request_for(case,workspace),workspace=workspace,source_path=source,
        execution=execution,round_number=2,plan=SimpleNamespace()))
    assert result=='joint entered'
