"""Shared official-style generation review inputs, no model API or geometry."""
import asyncio
import base64
from dataclasses import replace
import json
from types import SimpleNamespace

import pytest

from adsl.agents import fixed_assembly as flow
from adsl.agents.models import CodeCriticDecision, ImageCriticDecision
from adsl.agents.prompts import object_prompt
from adsl.agents.service import ObjectWorkflow
from test_fixed_assembly import mock_flow, run_flow, plan_data, CONFIG


def unpack(call):
    content=call['input'][0]['content']
    payload=json.loads(content[0]['text'])
    images=[base64.b64decode(c['image_url'].split(',',1)[1]) for c in content if c['type']=='input_image']
    return payload,images


def real_review_state(tmp_path,monkeypatch,outcomes,*,code_pass=False):
    state=mock_flow(tmp_path,monkeypatch,outcomes)
    reference=tmp_path/'reference.png';reference.write_bytes(b'user reference')
    state=(state[0],replace(state[1],max_rounds=5,image_paths=(reference,),checker_specs=(),
        fixed_assembly={**CONFIG,'validation_mode':'visual_only'}),*state[2:])
    w,request,runtime,_,_=state
    calls=[]
    async def run(**kw):
        calls.append(kw)
        if kw['stage'].startswith('image_critic'):
            return SimpleNamespace(final_output=ImageCriticDecision(approved=mock_flow.appearance,
                observations=['judge current generated object'],required_changes=[] if mock_flow.appearance else ['fix backrest']))
        assert kw['stage'].startswith('code_critic')
        kw['context'].source_path.read_text()
        kw['context'].record('read_file',kw['context'].source_path)
        return SimpleNamespace(final_output=CodeCriticDecision(approved=code_pass,
            observations=['source reviewed'],required_changes=[] if code_pass else ['fix backrest'],
            image_critic_corrections=['previous view note']))
    runtime.run=run
    for name in ('_review_generation_image','_review_generation_code'):
        monkeypatch.setattr(w,name,getattr(ObjectWorkflow,name).__get__(w))
    monkeypatch.setattr(w,'_review_candidate_appearance',lambda **kw:pytest.fail('generation must not use preservation'))
    execute=flow.execute_asset_source
    def available(*a,**kw):
        result=execute(*a,**kw)
        path=result.output_root/'assembly/assembly_manifest.json'
        row=json.loads(path.read_text())
        row.update(export_status='PASS',diagnostic={'display_available':True,'parts':[{'id':'back','shown':True}],
                                                  'semantic_completeness':'NOT_EVALUATED'})
        path.write_text(json.dumps(row))
        return result
    monkeypatch.setattr(flow,'execute_asset_source',available)
    return state,calls


def test_generation_contract_current_images_plan_history_and_real_repair(tmp_path,monkeypatch):
    state,calls=real_review_state(tmp_path,monkeypatch,[('NOT_EVALUATED',False),('NOT_EVALUATED',True)])
    result,book=run_flow(state)
    assert result.approved and len(state[-1])==1
    assert [c['stage'] for c in calls]==['image_critic:1','code_critic:1','image_critic:2']
    image1,images1=unpack(calls[0]);code1,codeimages=unpack(calls[1]);image2,images2=unpack(calls[2])
    assert images1==codeimages==[b'user reference',b'original']
    assert images2==[b'user reference',b'candidate1']  # No failed baseline image.
    assert image1['planner_checklist']==plan_data()['critic_checklist']
    assert code1['plan']['connections']==plan_data()['connections']
    assert code1['assigned_source']=='original/source.py'
    assert code1['fixed_assembly']['validation_mode']=='visual_only'
    assert image1['max_rounds']==code1['max_rounds']==image2['max_rounds']==5
    assert image1['previous_image_decisions']==[] and code1['previous_code_decisions']==[]
    assert image2['previous_image_decisions']==book['image_history'][:1]
    assert image2['code_critic_corrections']==['previous view note']
    for payload in (image1,code1,image2):
        assert not {'assembly_diagnostic','review_mode','image_order','proposal','protection_checklist'} & payload.keys()
        assert '"complete"' not in json.dumps(payload) and '"shown"' not in json.dumps(payload)
    assert state[-1][0]['payload']['feedback']['code_critic']['required_changes']==['fix backrest']
    assert book['retained']=='attempt_0001'
    assert json.loads((tmp_path/'checker_results.json').read_text())['results']==[]


def test_official_code_override_is_retained_not_silently_changed(tmp_path,monkeypatch):
    state,calls=real_review_state(tmp_path,monkeypatch,[('NOT_EVALUATED',False)],code_pass=True)
    result,book=run_flow(state)
    assert result.approved and len(calls)==2 and not state[-1]
    assert book['retained']=='original'
    assert book['versions']['original']['reviews']['image_critic']['approved'] is False
    assert book['versions']['original']['reviews']['code_critic']['approved'] is True


def test_history_persists_across_interrupted_resume_without_budget_reset(tmp_path,monkeypatch):
    state,calls=real_review_state(tmp_path,monkeypatch,[('NOT_EVALUATED',False),('NOT_EVALUATED',True)])
    w,request,runtime,source,_=state
    repair=w._repair
    async def interrupt(**kw):
        raise KeyboardInterrupt('simulated interruption after attempt reservation')
    monkeypatch.setattr(w,'_repair',interrupt)
    with pytest.raises(KeyboardInterrupt):run_flow(state)
    saved=json.loads((tmp_path/'assembly_versions.json').read_text())
    assert saved['next_round']==3 and len(saved['image_history'])==len(saved['code_history'])==1
    monkeypatch.setattr(w,'_repair',repair)
    result,book=run_flow(state)
    assert result.approved and book['max_rounds']==5
    resumed,_=unpack(calls[-1])
    assert resumed['round']==3 and resumed['max_rounds']==5
    assert resumed['previous_image_decisions']==saved['image_history']
    assert resumed['code_critic_corrections']==['previous view note']


def test_fixed_assembly_does_not_reassign_critic_roles():
    image=object_prompt('image_critic',articulation=False)
    assert object_prompt('image_critic',articulation=False,fixed_assembly=True)==image
    code=object_prompt('code_critic',articulation=False,fixed_assembly=True)
    assert code.startswith(object_prompt('code_critic',articulation=False))
    assert 'M_child = M_receiver @ F_slot @ inverse(F_tab)' in code
    assert 'Planner: return FixedAssemblyPlan' not in code
    assert 'Generate one complete program' not in code
