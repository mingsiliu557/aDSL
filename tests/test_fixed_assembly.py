"""Small v1 API/flow regressions; opt-in real tests use the existing executor."""
import asyncio
from dataclasses import replace
import json
import math
import os
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from adsl.core import Asset, Cube, FixedAssembly, InterfaceFrame, TabSlot, shape_aabb
from adsl.agents.models import (FixedAssemblyPlan, ObjectPlan, ObjectRequest, CheckerSpec,
                                ImageCriticDecision, CodeCriticDecision)
from adsl.agents.prompts import object_prompt
from adsl.agents import fixed_assembly as flow
from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.execution import ExecutionResult, AssetExecutionError, execute_asset_source
from adsl.agents.utils.usage import UsageRecorder

REPO = Path(__file__).resolve().parents[1]
CONFIG = {'mm_per_unit':1., 'fit_offset_mm':.2, 'final_size_mm':[60.,20.,62.]}


def plan_data():
    return dict(object_name='T bracket', components=[{'name':n,'description':n} for n in ('crossbar','stem')],
        relations=['stem inserts into crossbar'], critic_checklist=['T silhouette'],
        print_parts=[{'id':n,'components':[n]} for n in ('crossbar','stem')], root_part='crossbar',
        connections=[dict(id='joint',tab_part='stem',slot_part='crossbar',tab_port='tab',slot_port='slot',
            parameter_name='joint',interface_type='tab_slot',insertion_direction='+Z',fit_intent='demonstration clearance')],
        **CONFIG)


def build():
    a = FixedAssembly(root_id='bar',mm_per_unit=1)
    a.add_part('bar',Cube((60,20,12),center=(0,0,6)),components=('bar',))
    a.add_part('stem',Cube((20,12,50),center=(0,0,-25)),components=('stem',))
    return a


def params(**kw):
    return TabSlot(**(dict(width_mm=12,thickness_mm=6,insertion_mm=6,slot_depth_mm=7,fit_offset_mm=.2)|kw))


def connect(a,**kw):
    a.connect('joint',**(dict(tab_part='stem',slot_part='bar',tab_frame=InterfaceFrame(),
        slot_frame=InterfaceFrame(),parameters=params(),parameter_name='joint')|kw))


def test_old_schema_and_prompt_unchanged():
    data=plan_data()
    old={k:data[k] for k in ('object_name','components','relations','critic_checklist')}
    assert ObjectPlan.model_validate(old).model_dump()==old
    assert 'Optional fixed manufacturing assembly' not in object_prompt('coder',articulation=False)
    assert 'Optional fixed manufacturing assembly' in object_prompt('coder',articulation=False,fixed_assembly=True)
    assert FixedAssemblyPlan.model_validate(data).connections[0].tab_part=='stem'


def test_actual_planner_and_initial_coder_inputs(tmp_path,monkeypatch):
    calls=[]
    request=ObjectRequest('make T',tmp_path/'new','test',max_rounds=3,fixed_assembly=CONFIG)
    class Runtime:
        def agent(self,**kw):return kw
        async def run(self,**kw):
            calls.append(kw)
            if kw['role']=='planner':
                assert kw['agent']['output_type'] is FixedAssemblyPlan
                assert 'fixed_assembly' in kw['input']
                return SimpleNamespace(final_output=FixedAssemblyPlan.model_validate(plan_data()))
            assert 'print_parts' in kw['input'] and 'connections' in kw['input']
            assert 'tab_part' in kw['input'] and 'slot_part' in kw['input']
            assert 'incoming' in kw['agent']['instructions']
            ctx=kw['context'];ctx.source_path.write_text('mock complete program')
            ctx.record('write_file',ctx.source_path)
            return SimpleNamespace(final_output='done')
    w=ObjectWorkflow.__new__(ObjectWorkflow)
    monkeypatch.setattr(w,'_runtime',lambda *a,**kw:Runtime())
    async def stop(**kw):
        assert kw['request'].fixed_assembly==CONFIG and not kw['request'].checker_specs
        return 'entered assembly flow'
    monkeypatch.setattr(w,'_iterate',stop)
    assert asyncio.run(w.generate(request))=='entered assembly flow'
    assert len(calls)==2


def test_geometry_timeout_uses_existing_process_group_cleanup(tmp_path,monkeypatch):
    from adsl.agents.utils import execution as ex
    import subprocess
    source=tmp_path/'source.py';source.write_text('unused')
    stopped=[]
    class Process:
        pid=123
        def communicate(self,timeout):
            assert timeout==120
            raise subprocess.TimeoutExpired('geometry',timeout,output=b'stage:slot',stderr=b'')
    monkeypatch.setattr(ex.subprocess,'Popen',lambda *a,**k:Process())
    monkeypatch.setattr(ex,'_stop_process_group',lambda p:stopped.append(p.pid))
    with pytest.raises(subprocess.TimeoutExpired):
        ex.execute_asset_source(source,tmp_path/'out',render=False,fixed_assembly=CONFIG)
    assert stopped==[123]
    assert json.loads((tmp_path/'out/geometry_timeout.json').read_text())['code']=='ASSEMBLY_GEOMETRY_TIMEOUT'
    assert (tmp_path/'out/geometry.stdout.log').read_text()=='stage:slot'


@pytest.mark.parametrize('fit_offset', [.35, -.1])
def test_empty_source_resume_preserves_complete_initial_coder_payload(tmp_path,monkeypatch,fit_offset):
    from adsl.agents.cli import _resume_request
    from adsl.agents.utils.runner import _json_value
    config = dict(mm_per_unit=2., final_size_mm=[120.,40.,124.], fit_offset_mm=fit_offset)
    frozen = json.loads(json.dumps(config))
    plan = FixedAssemblyPlan.model_validate({**plan_data(), **config})
    calls = []
    request = ObjectRequest('make T',tmp_path/'case','test',max_rounds=3,fixed_assembly=config)
    class Runtime:
        usage = SimpleNamespace(update_manifest=lambda **kw:None)
        def agent(self,**kw): return kw
        async def run(self,**kw):
            if kw['role']=='planner': return SimpleNamespace(final_output=plan)
            calls.append(json.loads(kw['input']))
            ctx=kw['context'];ctx.source_path.write_text('mock complete program')
            ctx.record('write_file',ctx.source_path)
            return SimpleNamespace(final_output='done')
    w=ObjectWorkflow.__new__(ObjectWorkflow)
    monkeypatch.setattr(w,'_runtime',lambda *a,**kw:Runtime())
    async def stop(**kw):
        assert kw['request'].fixed_assembly==frozen
        return 'ready'
    monkeypatch.setattr(w,'_iterate',stop)
    assert asyncio.run(w.generate(request))=='ready'
    workspace=request.workspace
    # Recreate the real interruption point: plan saved, initial source empty.
    (workspace/'source.py').write_text(' \n')
    (workspace/'checkpoint.json').write_text(json.dumps({'mode':'generate','stage':'planned'}))
    saved=_json_value(request)  # Same serializer as AgentRuntime.write_runtime_config.
    (workspace/'runtime_config.json').write_text(json.dumps({'request':saved}))
    resumed=_resume_request(SimpleNamespace(output=workspace,requirement=None,task_id=None,
        max_rounds=3,checker_config=[],repair_policy_config=None,overhang_experiment_config=None,
        fixed_assembly_config=None))
    assert asyncio.run(w.resume(resumed))=='ready'
    assert len(calls)==2 and calls[0]==calls[1]
    assert calls[1]['fixed_assembly']==frozen==config
    assert calls[1]['fixed_assembly']['fit_offset_mm']==fit_offset


@pytest.mark.parametrize('name',['scene','exploded'])
def test_reserved_print_part_names_fail_before_export(name):
    from adsl.agents.models import PrintPartPlan
    message='reserved for assembly export filenames'
    with pytest.raises(ValueError,match=message):
        PrintPartPlan(id=name,components=['semantic_component'])
    with pytest.raises(ValueError,match=message):
        FixedAssembly(root_id=name,mm_per_unit=1)
    a=build()
    with pytest.raises(ValueError,match=message):
        a.add_part(name,Cube(1),components=['extra'])
    assert name not in a.parts
    # export_assembly calls validate() before writing anything. Catch even an
    # externally mutated mapping without introducing a different export scheme.
    a.parts[name]=a.parts['bar']
    with pytest.raises(ValueError,match=message):a.validate()


@pytest.mark.parametrize('bad',['unknown','duplicate','self','cycle','ownership'])
def test_plan_errors(bad):
    d=plan_data()
    if bad=='unknown':d['connections'][0]['tab_part']='missing'
    if bad=='duplicate':d['connections']*=2
    if bad=='self':d['connections'][0]['tab_part']='crossbar'
    if bad=='cycle':d['connections'][0].update(tab_part='crossbar',slot_part='stem')
    if bad=='ownership':d['print_parts'][1]['components']=['crossbar']
    with pytest.raises(ValueError):FixedAssemblyPlan.model_validate(d)


@pytest.mark.parametrize('kw',[{'width_mm':0},{'slot_depth_mm':5},{'fit_offset_mm':-3},
    {'lead_in_mm':3},{'root_overlap_mm':0},{'thickness_mm':float('nan')}])
def test_bad_parameters(kw):
    with pytest.raises(ValueError):params(**kw)


def test_paired_parameters_and_nonaxis_frames():
    a=build()
    phi=math.pi/5
    f=InterfaceFrame(origin=(3,4,5),x_axis=(math.cos(phi),math.sin(phi),0))
    connect(a,tab_frame=f,slot_frame=InterfaceFrame((0,0,0)))
    assert np.allclose(a.transforms['stem']@f.matrix(),np.eye(4))
    old=shape_aabb(a.parts['stem'])
    a.scene(exploded_mm=100)
    assert np.allclose(old,shape_aabb(a.parts['stem']))
    tab,slot=params(width_mm=14).geometry(1)
    assert np.isclose(np.diff(np.asarray(shape_aabb(tab)),axis=0)[0,0],14)
    assert np.isclose(np.diff(np.asarray(shape_aabb(slot)),axis=0)[0,0],14.4)


def test_overlap_and_repeated_instance_copy():
    body=Asset();child=body.attach_part('child',Cube(2))
    a=FixedAssembly(root_id='parent',mm_per_unit=1)
    a.add_part('parent',body,components=('parent',))
    with pytest.raises(ValueError,match='overlapping'):a.add_part('child',child,components=('child',))
    a=build();original=a.bodies['stem'].copy()
    a.add_part('second',original.copy(),components=('second',))
    connect(a,slot_frame=InterfaceFrame((-15,0,0)))
    a.connect('joint2',tab_part='second',slot_part='bar',tab_frame=InterfaceFrame(),
        slot_frame=InterfaceFrame((15,0,0)),parameters=params(),parameter_name='joint',slot_port='slot2')
    a.validate()
    assert np.allclose(a.transforms['second'][:3,3]-a.transforms['stem'][:3,3],[30,0,0])
    assert np.allclose(shape_aabb(original),shape_aabb(a.bodies['second']))


def test_mixed_mode_rejected_but_requested_physics_not_assumed_pass(tmp_path):
    r=ObjectRequest('bracket',tmp_path,'case',fixed_assembly=CONFIG)
    ObjectWorkflow._validate_request(r)
    with pytest.raises(ValueError):ObjectWorkflow._validate_request(replace(r,articulation=True))
    with pytest.raises(ValueError):ObjectWorkflow._validate_request(replace(r,overhang_experiment={'mode':'planned_checks'}))


def mock_flow(tmp_path,monkeypatch, outcomes, patch_status='CHANGED'):
    tmp_path.mkdir(exist_ok=True)
    src=tmp_path/'source.py';src.write_text('original')
    request=ObjectRequest('T bracket',tmp_path,'test',max_rounds=3,fixed_assembly=CONFIG,
        checker_specs=(CheckerSpec(name='fea',command=['SHOULD_NOT_RUN']),))
    runtime=SimpleNamespace(usage=UsageRecorder(tmp_path), agent=lambda **kw:kw)
    workflow=ObjectWorkflow.__new__(ObjectWorkflow)
    calls=[]
    async def repair(**kw):
        calls.append(kw)
        assert kw['allow_no_change']
        if patch_status=='CHANGED':kw['source_path'].write_text('candidate'+str(len(calls)))
        return {'status':patch_status,'reason':'mock outcome'}
    async def review(**kw):
        return mock_flow.appearance, {'appearance_approved':mock_flow.appearance,
             'image_critic':{'approved':mock_flow.appearance},'code_critic':None}
    def execute(source,out,**kw):
        assert kw['fixed_assembly']['mm_per_unit']==1 and not kw['export_urdf']
        out.mkdir(parents=True)
        folder=out/'assembly';folder.mkdir()
        render=out/'render';render.mkdir()
        glb=render/'scene.glb';glb.write_text(source.read_text())
        png=render/'view.png';png.write_text(source.read_text())
        status,appearance=outcomes.pop(0)
        mock_flow.appearance=appearance
        (folder/'assembly_manifest.json').write_text(json.dumps({'status':status,'failures':[],'source_sha256':flow.file_hash(source)}))
        (folder/'part.stl').write_text(source.read_text())
        return ExecutionResult(out,glb,None,(png,),'','')
    monkeypatch.setattr(workflow,'_repair',repair)
    monkeypatch.setattr(workflow,'_review_candidate_appearance',review)
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    return workflow,request,runtime,src,calls


def run_flow(f):
    w,r,rt,src,calls=f
    result=asyncio.run(flow.iterate_fixed_assembly(w,runtime=rt,request=r,workspace=r.workspace,
        source_path=src,plan=FixedAssemblyPlan.model_validate(plan_data())))
    return result,json.loads((r.workspace/'assembly_versions.json').read_text())


def test_rejected_geometry_keeps_original_and_budget(tmp_path,monkeypatch):
    f=mock_flow(tmp_path,monkeypatch,[('PASS',False),('FAIL',True),('FAIL',True)])
    result,book=run_flow(f)
    assert not result.approved and len(f[-1])==2
    assert book['retained']=='original'
    assert (tmp_path/'source.py').read_text()==(tmp_path/'scene.glb').read_text()=='original'
    assert (tmp_path/'assembly/part.stl').read_text()=='original'
    rows=(tmp_path/'repair_history.jsonl').read_text().splitlines()
    assert len(rows)==2
    checks=json.loads((tmp_path/'checker_results.json').read_text())
    assert checks['requested_unsupported']['fea']['status']=='INDETERMINATE'
    assert not checks['required_checkers_passed']
    result,book=run_flow(f)  # Completed resume never calls editor again.
    assert len(f[-1])==2


def test_successful_candidate_publishes_matching_results(tmp_path,monkeypatch):
    f=mock_flow(tmp_path,monkeypatch,[('FAIL',False),('PASS',True)])
    f=(f[0],replace(f[1],checker_specs=()),*f[2:])
    result,book=run_flow(f)
    assert result.approved and len(f[-1])==1 and book['retained']=='attempt_0001'
    assert (tmp_path/'source.py').read_text()==(tmp_path/'assembly/part.stl').read_text()=='candidate1'
    report=json.loads((tmp_path/'assembly_result.json').read_text())
    assert report['source_sha256']==flow.file_hash(tmp_path/'source.py')
    assert report['physical_validation']=='NOT_EVALUATED'


@pytest.mark.parametrize('status',['NO_CHANGE','TOOL_ERROR','NO_PATCH_UNEXPLAINED'])
def test_no_change_and_errors_preserve_assets(tmp_path,monkeypatch,status):
    f=mock_flow(tmp_path,monkeypatch,[('FAIL',False)],patch_status=status)
    result,book=run_flow(f)
    assert not result.approved and book['stop_reason']==status
    assert len(f[-1])==1 and (tmp_path/'scene.glb').read_text()=='original'


def test_changed_frozen_config_rejected_on_resume(tmp_path,monkeypatch):
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    run_flow(f)
    w,r,rt,src,calls=f
    f=(w,replace(r,fixed_assembly={**CONFIG,'mm_per_unit':2}),rt,src,calls)
    with pytest.raises(ValueError,match='frozen'):run_flow(f)


@pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL')!='1',reason='explicit real geometry validation only')
@pytest.mark.parametrize('variant',['normal','shared_width','tilted','interference','missing_slot','disconnected'])
def test_real_boolean_export(tmp_path,variant):
    text=(REPO/'examples/fixed_assembly/t_bracket.py').read_text()
    config=dict(CONFIG)
    if variant=='shared_width':text=text.replace('width_mm=12','width_mm=14')
    if variant=='interference':
        text=text.replace('fit_offset_mm=0.2','fit_offset_mm=-0.1')
        config['fit_offset_mm']=-.1
    if variant=='tilted':
        phi=math.pi/5;c,s=math.cos(phi),math.sin(phi)
        text=text.replace("mm_per_unit=1)",f"mm_per_unit=1, root_frame=InterfaceFrame(x_axis=({c},{s},0)))")
        config['final_size_mm']=[60*c+20*s,60*s+20*c,62]
    if variant=='missing_slot':text=text.replace('scene = assembly.scene()',"assembly.parts['crossbar'] = assembly.bodies['crossbar'].copy()\nscene = assembly.scene()")
    if variant=='disconnected':text=text.replace("self.attach_part('body', Cube((20, 12, 50), center=(0, 0, -25)))",
        "self.attach_part('body', Cube((20, 12, 50), center=(0, 0, -25)))\n        self.attach_part('floating', Cube(1, center=(30,0,-25)))")
    source=tmp_path/'source.py';source.write_text(text)
    result=execute_asset_source(source,tmp_path/'output',render=False,export_urdf=False,fixed_assembly=config)
    report=json.loads((result.output_root/'assembly/assembly_manifest.json').read_text())
    assert report['status']==('FAIL' if variant in ('missing_slot','disconnected') else 'PASS'),report
    if variant=='missing_slot':assert any(f['code']=='EXPORTED_INTERFACE_GEOMETRY_MISMATCH' for f in report['failures'])
    if variant=='disconnected':assert any(f['code']=='DISCONNECTED_PRINT_PART' for f in report['failures'])
    assert all((result.output_root/'assembly'/part['stl']).is_file() for part in report['parts'])
