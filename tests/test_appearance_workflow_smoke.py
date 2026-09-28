"""Real controllers with simulated model/executor/checker results; no physics/API."""
import asyncio
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from adsl.agents import service
from adsl.agents.models import GradedImageCriticDecision, GradedCodeCriticDecision, EngineeringCriticDecision
from test_appearance_review_contract import issue
from test_checker_fault_isolation import workflow_fixture, run as checker_run
from test_generation_review_contract import real_review_state
from test_fixed_assembly import run_flow
from test_overhang_candidate_isolation import fixture, run_case


@pytest.mark.parametrize('image_severity,code_severity,approved', [('LOW',None,True), ('HIGH','HIGH',False), ('HIGH','LOW',True)])
def test_last_round_reviews_without_extra_patch(tmp_path,monkeypatch,image_severity,code_severity,approved):
    w,kw,manifest,_,_=workflow_fixture(tmp_path,monkeypatch,[])
    kw['request']=replace(kw['request'],max_rounds=1)
    stages=[]
    agents=[]
    kw['runtime'].agent=lambda **spec: agents.append(spec)
    async def model(**call):
        stages.append(call['stage'])
        if call['stage'].startswith('image'):
            result=GradedImageCriticDecision(approved=True,observations=[],issues=[issue(image_severity)])
        else:
            call['context'].record('read_file',call['context'].source_path)
            result=GradedCodeCriticDecision(approved=True,observations=[],issues=[issue(code_severity)],
                image_critic_corrections=['Backrest is visible in the rear view and its transform is correct.'])
        return SimpleNamespace(final_output=result)
    kw['runtime'].run=model
    result=asyncio.run(w._iterate(**kw))
    assert result.approved is approved
    by_name={a['name']:a for a in agents}
    assert by_name['object-image-critic']['output_type'] is GradedImageCriticDecision
    assert by_name['object-code-critic']['output_type'] is GradedCodeCriticDecision
    assert stages==(['image_critic:1'] if image_severity=='LOW' else ['image_critic:1','code_critic:1'])
    w._repair.assert_not_awaited()
    assert (tmp_path/'rounds/round_01/image_critique.json').is_file()
    assert manifest['finalization_reason']!='round_limit_after_execution'


def test_last_round_after_patch_still_rejected_and_reviewed(tmp_path,monkeypatch):
    w,kw,_,_,_=workflow_fixture(tmp_path,monkeypatch,[],appearance=False)
    kw['request']=replace(kw['request'],max_rounds=2)
    result=asyncio.run(w._iterate(**kw))
    assert not result.approved and w._repair.await_count==1
    assert (tmp_path/'rounds/round_02/code_critique.json').is_file()
    assert w._repair.call_args.kwargs['payload']['resolved_visual_feedback']['required_changes']


@pytest.mark.parametrize('dismissed',[False,True])
def test_routing_ordinary_engineering_gets_resolved_visual(tmp_path,monkeypatch,dismissed):
    rows=[checker_run(tmp_path,'standing','FAIL',40)]
    w,kw,_,_,_=workflow_fixture(tmp_path,monkeypatch,rows,appearance=False)
    old=kw['runtime'].run;engineering=[]
    async def model(**call):
        if call['stage'].startswith('code'):
            call['context'].record('read_file',call['context'].source_path)
            return SimpleNamespace(final_output=GradedCodeCriticDecision(approved=dismissed,observations=[],
                issues=[] if dismissed else [issue('HIGH')]))
        if call['stage'].startswith('engineering'):
            engineering.append(json.loads(call['input'] if isinstance(call['input'],str) else call['input'][0]['content'][0]['text']))
        return await old(**call)
    kw['runtime'].run=model
    result=asyncio.run(w._iterate(**kw))
    assert not result.approved  # Mock required checker still FAIL.
    assert len(engineering)==1
    assert bool(engineering[0]['resolved_visual_feedback']['required_changes']) is not dismissed
    assert engineering[0]['image_critic']['issues'][0]['severity']=='HIGH'
    w._repair.assert_not_awaited()  # No fake engineering proposal was returned.


def test_dismissed_assembly_issue_does_not_trigger_export_shape_repair(tmp_path,monkeypatch):
    from adsl.agents import fixed_assembly
    state,calls=real_review_state(tmp_path,monkeypatch,[('NOT_EVALUATED',False)],code_pass=True)
    execute=fixed_assembly.execute_asset_source
    def unavailable(*args,**kw):
        ex=execute(*args,**kw);path=ex.output_root/'assembly/assembly_manifest.json'
        report=json.loads(path.read_text());report.update(export_status='FAIL',failures=[{
            'code':'EXPORTED_FILE_INVALID','failure_kind':'export','reason':'simulated serialization failure'}])
        path.write_text(json.dumps(report));return ex
    monkeypatch.setattr(fixed_assembly,'execute_asset_source',unavailable)
    result,book=run_flow(state)
    assert not result.approved and not state[-1]
    assert book['stop_reason']=='export_unassessed_no_geometry_repair'
    assert book['feedback']['resolved_visual_feedback']['required_changes']==[]
    assert book['feedback']['image_critic']['required_changes']


@pytest.mark.parametrize('planned',[False,True])
@pytest.mark.parametrize('dismissed',[False,True])
def test_mixed_engineering_one_candidate_shared_budget(tmp_path,monkeypatch,planned,dismissed):
    f=fixture(tmp_path,monkeypatch,original_appearance=False,actions=(90,),budget=1)
    if planned:
        f.options.update(mode='planned_checks',protection_policy='explicit_task_constraints')
    rt=f.kwargs['runtime'];old=rt.run
    async def model(**call):
        if dismissed and call['role'].startswith('code'):
            f.calls.append((call['role'],call['input']))
            call['context'].record('read_file',call['context'].source_path)
            return SimpleNamespace(final_output=GradedCodeCriticDecision(approved=True,observations=[],issues=[],
                image_critic_corrections=['Frame geometry and rear view support dismissing the issue.']))
        return await old(**call)
    rt.run=model
    _,book=run_case(f)
    engineer=[json.loads(p) for role,p in f.calls if role.startswith('engineering')]
    edits=[json.loads(p) for role,p in f.calls if role.startswith('coder')]
    assert len(engineer)==len(edits)==len(book['attempts'])==1
    assert bool(engineer[0]['resolved_visual_feedback']['required_changes']) is not dismissed
    assert bool(edits[0]['resolved_visual_feedback']['required_changes']) is not dismissed
    assert next(iter(book['attempts'].values()))['origin']=='engineering'


def test_supplemental_engineering_keeps_final_visual_feedback(tmp_path,monkeypatch):
    from adsl.agents.service import PlannedEngineeringCriticDecision
    f=fixture(tmp_path,monkeypatch,original_appearance=False,actions=(90,),budget=1)
    f.options.update(mode='planned_checks',protection_policy='explicit_task_constraints')
    old=f.kwargs['runtime'].run
    async def model(**call):
        if call['stage']=='engineering_critic:1':
            f.calls.append((call['role'],call['input']))
            return SimpleNamespace(final_output=PlannedEngineeringCriticDecision(approved=False,observations=[],
                repair_proposals=[],stop_category='insufficient_localization'))
        return await old(**call)
    f.kwargs['runtime'].run=model
    _,book=run_case(f)
    supplement=[json.loads(p) for role,p in f.calls if role.startswith('engineering-critic:supplement')]
    assert len(supplement)==1 and supplement[0]['resolved_visual_feedback']['required_changes']
    assert len(book['attempts'])==1
