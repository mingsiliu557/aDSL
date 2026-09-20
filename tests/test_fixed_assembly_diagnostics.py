"""Small candidate/critic regressions, without model API or Blender CSG."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from adsl.agents import fixed_assembly as flow
from adsl.agents.models import ImageCriticDecision, CodeCriticDecision, RepairProposal, RepairTarget
from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.execution import ExecutionResult
from adsl.core.export import export_assembly as exporter
from test_fixed_assembly import mock_flow, run_flow


def critic_runtime(runtime):
    calls = []
    async def run(**kw):
        calls.append(kw)
        if kw['stage'].startswith('image_critic'):
            return SimpleNamespace(final_output=ImageCriticDecision(approved=mock_flow.appearance, observations=['visible']))
        context = kw['context']
        context.source_path.read_text()
        context.record('read_file', context.source_path)
        return SimpleNamespace(final_output=CodeCriticDecision(approved=False,
            observations=['read assigned source'], required_changes=['inspect invalid part']))
    runtime.run = run
    return calls


@pytest.mark.parametrize('pictures,complete,status,roles,approved', [
    (True,False,'FAIL',['image'],None),
    (False,False,'ERROR',['code'],None),
    (True,True,'PASS',['image'],True),
])
def test_actual_critic_requests_for_diagnostic_and_success(tmp_path, monkeypatch,
        pictures, complete, status, roles, approved):
    from test_fixed_assembly import plan_data
    from adsl.agents.models import FixedAssemblyPlan
    w,r,rt,source,_ = mock_flow(tmp_path,monkeypatch,[])
    mock_flow.appearance=True
    calls = critic_runtime(rt)
    png = tmp_path/'view.png'; png.write_bytes(b'mock image bytes')
    glb = tmp_path/'view.glb'; glb.write_bytes(b'mock mesh')
    execution = ExecutionResult(tmp_path, glb, None, (png,), '', '') if pictures else None
    issue = None if complete else {'stage':'render','reason':'Unavailable/partial display',
                                  'report_path':str(tmp_path/'error.json')}
    (tmp_path/'error.json').write_text('{}')
    async def review():
        image=await ObjectWorkflow._review_generation_image(w,runtime=rt,request=r,
            plan=FixedAssemblyPlan.model_validate(plan_data()),execution=execution,round_number=1,
            max_rounds=3,round_root=tmp_path,image_critic='image',image_history=[],
            code_critic_corrections=[],render_issue=issue)
        code=None
        if image is None or not image.approved:
            code=await ObjectWorkflow._review_generation_code(w,runtime=rt,request=r,
                plan=FixedAssemblyPlan.model_validate(plan_data()),execution=execution,
                workspace=tmp_path,source_path=source,round_number=1,max_rounds=3,
                round_root=tmp_path,code_critic='code',image_decision=image,code_history=[],render_issue=issue)
        return image,code
    image,code=asyncio.run(review())
    assert (image.approved if pictures and complete else None) is approved
    assert [c['agent'] for c in calls] == roles
    for call in calls:
        payload = call['input']
        if isinstance(payload,list): payload = payload[0]['content'][0]['text']
        payload=json.loads(payload)
        assert 'assembly_diagnostic' not in payload and 'review_mode' not in payload
        assert payload.get('render_issue') == issue
    if not pictures:
        assert json.loads((tmp_path/'image_critique.json').read_text())['status']=='SKIPPED'
        assert code.observations==['read assigned source']


def test_no_image_failure_reaches_code_critic_and_coder(tmp_path,monkeypatch):
    state = mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=2),*state[2:])
    w,r,rt,source,repairs=state
    critic_calls=critic_runtime(rt)
    monkeypatch.setattr(w,'_review_generation_image',ObjectWorkflow._review_generation_image.__get__(w))
    monkeypatch.setattr(w,'_review_generation_code',ObjectWorkflow._review_generation_code.__get__(w))
    execute=flow.execute_asset_source
    count=[]
    def fail_first(*args,**kwargs):
        count.append(1)
        if len(count)==1: raise flow.AssetExecutionError('no renderable part')
        return execute(*args,**kwargs)
    monkeypatch.setattr(flow,'execute_asset_source',fail_first)
    result,book=run_flow(state)
    assert result.approved and book['qualified']=='attempt_0001'
    assert len(repairs)==1 and len(count)==2
    assert repairs[0]['payload']['feedback']['image_critic'] is None
    assert repairs[0]['payload']['feedback']['code_critic']['observations']==['read assigned source']
    assert critic_calls[0]['stage'].startswith('code_critic')


def test_failed_visible_candidate_reaches_both_critics_and_repair(tmp_path,monkeypatch):
    state=mock_flow(tmp_path,monkeypatch,[('FAIL',False),('PASS',True)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=2),*state[2:])
    w,r,rt,_,repairs=state
    calls=critic_runtime(rt)
    monkeypatch.setattr(w,'_review_generation_image',ObjectWorkflow._review_generation_image.__get__(w))
    monkeypatch.setattr(w,'_review_generation_code',ObjectWorkflow._review_generation_code.__get__(w))
    result,book=run_flow(state)
    assert result.approved
    assert [c['stage'].split(':')[0] for c in calls]==[
        'image_critic','code_critic','image_critic']
    assert repairs[0]['payload']['feedback']['geometry_status']=='FAIL'
    assert repairs[0]['payload']['feedback']['code_critic']


def test_next_edit_uses_working_candidate_not_original(tmp_path,monkeypatch):
    state=mock_flow(tmp_path,monkeypatch,[('FAIL',True),('FAIL',True),('FAIL',True)])
    w=state[0]; repair=w._repair; parents=[]
    async def capture(**kw):
        parents.append(kw['source_path'].read_text())
        return await repair(**kw)
    monkeypatch.setattr(w,'_repair',capture)
    result,book=run_flow(state)
    assert parents==['original','candidate1']
    assert book['working']=='attempt_0002' and book['qualified'] is None
    assert book['retained']=='original' and not result.approved
    assert (tmp_path/'source.py').read_text()=='original'
    assert Path(book['versions'][book['working']]['source']).read_text()=='candidate2'
    assert json.loads((tmp_path/'working_candidate.json').read_text())['approved'] is False
    run_flow(state)
    assert len(parents)==2  # Completed resume cannot increase budget.


def test_diagnostic_places_saved_invalid_mesh_and_marks_missing(tmp_path,monkeypatch):
    box=trimesh.creation.box(extents=(2,4,6))
    box.export(tmp_path/'invalid.glb')
    transform=np.eye(4);transform[:3,3]=[10,20,30]
    assembly=SimpleNamespace(parts={'invalid':None,'missing':None},
        transforms={'invalid':transform,'missing':np.eye(4)},mm_per_unit=1.)
    monkeypatch.setattr(exporter,'export_glb',lambda *a,**k:pytest.fail('must not rerun CSG'))
    report={'status':'FAIL','failures':[{'code':'PART_GEOMETRY_INVALID'}]}
    display=exporter._diagnostic_view(assembly,tmp_path,{},report)
    assert display['invalid_parts']==['invalid','missing'] and display['missing_parts']==['missing']
    assert not display['display_available'] and report['status']=='FAIL'
    scene=trimesh.load(tmp_path/display['glb'],force='scene',process=False)
    assert np.allclose(scene.bounds.mean(axis=0),[10,30,-20])
    assert np.allclose(trimesh.load(tmp_path/'invalid.glb',force='scene').bounds.mean(axis=0),[0,0,0])


def test_no_available_mesh_has_explicit_diagnostic_reason(tmp_path):
    assembly=SimpleNamespace(parts={'missing':None},transforms={'missing':np.eye(4)},mm_per_unit=1.)
    display=exporter._diagnostic_view(assembly,tmp_path,{}, {'status':'FAIL'})
    assert display['glb'] is None and display['reason'] and display['missing_parts']==['missing']


def test_render_only_attempts_diagnostic_and_labels_image(tmp_path,monkeypatch):
    from adsl.agents.utils import asset_executor
    from PIL import Image
    root=tmp_path/'out';root.mkdir()
    glb=root/'candidate.glb';glb.write_bytes(b'fixture')
    (root/'execution.json').write_text(json.dumps({'glb_path':str(glb), 'assembly_diagnostic':{
        'diagnostic_only':True,'invalid_parts':['backrest'],'missing_parts':['arm']}}))
    calls=[]
    def render(**kw):
        calls.append(kw)
        kw['output_dir'].mkdir()
        Image.new('RGB',(512,512),'blue').save(kw['output_dir']/'view.png')
    monkeypatch.setattr(asset_executor,'render_video',render)
    assert asset_executor.main(['--source',str(tmp_path/'unused.py'),'--output',str(root),'--render-only'])==0
    assert len(calls)==1 and calls[0]['glb_path']==glb
    with Image.open(root/'render/view.png') as picture:
        assert picture.height>512 and picture.width==512
        assert picture.getpixel((511,picture.height-1))==(0,0,255)


def test_executor_selects_failed_candidate_display_not_formal_scene(tmp_path,monkeypatch):
    from adsl.agents.utils import asset_executor
    from test_fixed_assembly import build,connect
    assembly=build();connect(assembly)
    source=tmp_path/'source.py';source.write_text('mock source')
    output=tmp_path/'out';output.mkdir()
    config=tmp_path/'config.json';config.write_text('{}')
    monkeypatch.setattr(asset_executor.runpy,'run_path',lambda *a,**k:{'scene':assembly.scene(),'assembly':assembly})
    def export(a,path,**kw):
        path.mkdir()
        (path/'diagnostic_scene.glb').write_bytes(b'current failed candidate')
        return {'status':'FAIL','failures':[{'code':'PART_GEOMETRY_INVALID'}],
                'diagnostic':{'glb':'diagnostic_scene.glb','complete':False,'invalid_parts':['stem']}}
    monkeypatch.setattr(exporter,'export_assembly',export)
    monkeypatch.setattr(asset_executor,'build_source_index',lambda *a:(_ for _ in ()).throw(ValueError('no index')))
    assert asset_executor.main(['--source',str(source),'--output',str(output),'--fixed-assembly-config',str(config)])==0
    result=json.loads((output/'execution.json').read_text())
    assert Path(result['glb_path']).read_bytes()==b'current failed candidate'
    assert result['assembly_diagnostic']['diagnostic_only'] and not result['assembly_diagnostic']['complete']
