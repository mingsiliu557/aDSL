"""Small submission/isolation tests; no real API, rendering or geometry."""
import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from experiments.fixed_assembly_prompt import run_paired as paired
from adsl.agents.overhang_edit import version_record
from adsl.agents.utils.io import read_json,write_json


def test_pair_inputs_same_prompt_scale_and_budget_only_feedback_enabled(tmp_path):
    for arm in ('wo','w'):
        paired.prompt.prepare(tmp_path/arm,cases=('SF02',),sizes=paired.SIZES,
                              max_rounds=5,assembly_topology=arm=='w')
    a=read_json(tmp_path/'wo/SF02/input.json');b=read_json(tmp_path/'w/SF02/input.json')
    assert a['original_task']==b['original_task']
    assert a['fixed_assembly']==b['fixed_assembly']
    assert a['fixed_assembly']['final_size_mm']==[90.,90.,150.]
    assert a['source_repair_limit']==b['source_repair_limit']==4
    assert not a.get('checker_specs')
    assert [s['name'] for s in b['checker_specs']]==['assembly_topology']
    assert not (tmp_path/'wo/SF02/generate/source.py').exists()


def test_offline_image_excludes_arm_checker_source_and_history():
    p=paired.image_payload({'original_task':{'prompt':'a bookshelf'},'arm':'w',
        'topology':'FAIL','code_history':['ignore missing shelf'],'source':'secret'},8)
    assert p['requirement']=='a bookshelf'
    assert p['previous_image_decisions']==p['code_critic_corrections']==[]
    assert not {'arm','topology','code_history','source'} & p.keys()


def test_selection_uses_retained_not_better_working(tmp_path):
    src=tmp_path/'retained.py';src.write_text('original saved source')
    (tmp_path/'source.py').write_text(src.read_text())
    v=version_record('original',src,None)
    write_json(tmp_path/'assembly_versions.json',dict(retained='original',working='attempt_0001',
        versions={'original':v,'attempt_0001':{'reviews':{'appearance_approved':True}}}))
    assert paired.selected_version(tmp_path)[1]['id']=='original'
    (tmp_path/'source.py').write_text('mismatched')
    with pytest.raises(ValueError,match='Published source'):paired.selected_version(tmp_path)


def test_generation_finishes_before_offline_and_reused_not_regenerated(tmp_path,monkeypatch):
    jobs=[dict(id='a_wo',arm='wo',case='a',origin='fresh'),
          dict(id='a_w',arm='w',case='a',origin='historical_reuse'),
          dict(id='b_w',arm='w',case='b',origin='fresh')]
    write_json(tmp_path/'paired_plan.json',dict(jobs=jobs))
    monkeypatch.setattr(paired,'verify_frozen',lambda root:read_json(root/'paired_plan.json'))
    calls=[]
    monkeypatch.setattr(paired,'launch_phase',lambda root,item,phase:calls.append((item['id'],phase)))
    monkeypatch.setattr(paired,'summarize',lambda root:None)
    paired.run(tmp_path)
    assert calls==[('a_wo','generate'),('b_w','generate'),('a_wo','evaluate'),('a_w','evaluate'),('b_w','evaluate')]


def test_api_failure_phase_preserves_error_and_next_phase_runs(tmp_path,monkeypatch):
    monkeypatch.setattr(paired.subprocess,'run',lambda *a,**kw:SimpleNamespace(returncode=1))
    monkeypatch.setattr(paired,'summarize',lambda root:None)
    item={'id':'SF02_wo'}
    paired.launch_phase(tmp_path,item,'generate')
    assert read_json(tmp_path/'jobs/SF02_wo/generate.json')['status']=='ERROR'
    # A started phase is never silently replayed or billed again.
    monkeypatch.setattr(paired.subprocess,'run',lambda *a,**kw:pytest.fail('replayed'))
    paired.launch_phase(tmp_path,item,'generate')


@pytest.mark.parametrize('current_bad',[False,True,'shared'])
def test_batch_continues_case_export_error_but_stops_confirmed_fault(tmp_path,monkeypatch,current_bad):
    work=tmp_path/'wo/SF02/generate'
    failure={'code':'EXPORTED_FILE_INVALID','file':'scene.glb','reason':'No such file'}
    result_path=write_json(work.parent/'result.json',{'geometry_failures':[failure]})
    original=result_path.read_bytes()
    write_json(work/'rounds/round_01/assembly_manifest.json',{'failures':[failure]})
    current_failure={'code':'SHARED_ENVIRONMENT_UNAVAILABLE','reason':'confirmed service fault'} if current_bad=='shared' else failure
    write_json(work/'rounds/round_02/assembly_manifest.json',{'failures':[current_failure] if current_bad else []})
    jobs=[dict(id='SF02_wo',arm='wo',case='SF02',origin='fresh',workspace=str(work))]
    write_json(tmp_path/'paired_plan.json',{'jobs':jobs})
    monkeypatch.setattr(paired,'verify_frozen',lambda root:read_json(root/'paired_plan.json'))
    calls=[]
    monkeypatch.setattr(paired,'launch_phase',lambda root,item,phase:calls.append(phase))
    monkeypatch.setattr(paired,'summarize',lambda root:None)
    paired.run(tmp_path)
    assert calls==(['generate'] if current_bad=='shared' else ['generate','evaluate'])
    assert (tmp_path/'paused.json').exists() is (current_bad=='shared')
    assert result_path.read_bytes()==original


def test_topology_error_still_allows_independent_image_review(tmp_path,monkeypatch):
    work=tmp_path/'work';work.mkdir();(work/'source.py').write_text('unchanged')
    book=dict(retained='original',versions={})
    write_json(work/'assembly_versions.json',book)
    version=dict(id='original',record_hash='frozen',execution={})
    monkeypatch.setattr(paired,'selected_version',lambda w:(book,version))
    execution=SimpleNamespace(render_paths=('mock.png',))
    monkeypatch.setattr(paired,'version_assets',lambda v:(work/'source.py',execution,[]))
    monkeypatch.setattr(paired,'assert_version',lambda v:None)
    def fail(*a,**kw):raise RuntimeError('simulated topology timeout')
    monkeypatch.setattr(paired,'run_assembly_topology',fail)
    write_json(tmp_path/'wo/SF02/input.json',{'original_task':{'prompt':'a chair'}})
    decision=paired.ImageCriticDecision(approved=True,observations=[],required_changes=[])
    runtime=SimpleNamespace(agent=lambda **kw:None,run=AsyncMock(return_value=SimpleNamespace(final_output=decision)))
    workflow=SimpleNamespace(_runtime=lambda *a,**kw:runtime,_typed_output=lambda out,typ:out)
    monkeypatch.setattr(paired.prompt,'PromptWorkflow',lambda *a:workflow)
    monkeypatch.setattr(paired,'user_input',lambda text,paths:text)
    asyncio.run(paired.evaluate(tmp_path,dict(id='SF02_wo',case='SF02',arm='wo',origin='fresh',workspace=str(work))))
    r=read_json(tmp_path/'offline/SF02_wo/evaluation.json')
    assert r['topology_status']=='ERROR' and r['image_status']=='PASS'
    runtime.run.assert_awaited_once()
    assert (work/'source.py').read_text()=='unchanged'


@pytest.mark.parametrize('previous_edits',[0,1])
def test_interrupted_initial_review_resumes_without_generation_or_added_budget(tmp_path,monkeypatch,previous_edits):
    root=tmp_path/'next'
    paired.prompt.prepare(root,cases=('SF07',),max_rounds=5)
    old=tmp_path/'old/SF07/generate';(old/'original').mkdir(parents=True)
    for path in (old/'source.py',old/'original/source.py'):path.write_text('unchanged initial source')
    write_json(old/'plan.json',{'plan':'saved'})
    write_json(old.parent/'input.json',read_json(root/'SF07/input.json'))
    write_json(old.parent/'input_audit.json',{'input_verified':True,'assembly_api_verified':True})
    write_json(old.parent/'result.json',{'repair_attempts':previous_edits,'stop_reason':'FLOW_ERROR'})
    calls=[]
    async def resume(request):
        calls.append(request)
        assert (request.workspace/'source.py').read_text()=='unchanged initial source'
        assert read_json(request.workspace/'plan.json')=={'plan':'saved'}
        return SimpleNamespace(approved=False)
    workflow=SimpleNamespace(resume=resume,generate=lambda *a:pytest.fail('regenerated'))
    monkeypatch.setattr(paired.prompt,'PromptWorkflow',lambda *a:workflow)
    if previous_edits:
        with pytest.raises(AssertionError):asyncio.run(paired.prompt.run_case(root,'SF07',resume_from=old))
        assert not calls
    else:
        result=asyncio.run(paired.prompt.run_case(root,'SF07',resume_from=old))
        assert len(calls)==1 and calls[0].max_rounds==5 and not calls[0].checker_specs
        assert result['initial_generations']==0 and result['source_repair_limit']==4
        assert read_json(root/'SF07/input_audit.json')['new_initial_generation'] is False
    assert (old/'source.py').read_text()=='unchanged initial source'
