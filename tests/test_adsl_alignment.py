from pathlib import Path
import importlib.util
import json
import os
import subprocess
import sys
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh
import yaml

from snap3d.rollout import _rigid_flex_xml

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def meshes():
    a=trimesh.creation.box([10,10,10]);a.apply_translation([0,0,5])
    b=trimesh.creation.box([5,5,4]);b.apply_translation([0,0,12])
    return {'receiver':a,'peg':b}


def test_config_matches_frozen_adsl_standing_and_only_protocol_exceptions():
    reference=json.loads(Path('snap3d/vendor/standing_reference.json').read_text())
    standing=reference['standing'];cfg=CFG['rollout']
    assert reference['source_commit']=='9e1f01dc32812bf649dc2f58fdc027c7c3dd8d7d'
    assert reference['source_path']=='benchmark/configs/selection_v1.json'
    assert cfg['dt']==standing['timestep_seconds']==.002
    assert cfg['friction']==standing['friction']==[.5,.005,.0001]
    assert cfg['density']==reference['material']['density_kg_m3']==1240
    assert cfg['collision_backend']==standing['collision_backend']=='rigid_flex'
    assert cfg.get('rigid_flex',{})==standing.get('rigid_flex',{})
    assert cfg['integrator']==reference['xml_model_settings']['integrator']=='implicitfast'
    assert 'iterations' not in cfg and 'solver' not in cfg
    assert cfg['duration']==2 and cfg['displacement_ratio']==15/256 and cfg['rotation_deg']==10


def test_xml_has_source_defaults_and_only_fixed_body_joint_removed():
    import mujoco
    parts=meshes();ids=sorted(parts)
    xml,_=_rigid_flex_xml(parts,ids,['receiver'],CFG['rollout'],150/1024,0.)
    root=ET.fromstring(xml)
    assert CFG['rollout']['memory']=='1G'
    assert root.find('size').get('memory')=='1G'
    assert root.find('compiler').attrib=={'angle':'radian'}
    option=root.find('option')
    assert set(option.attrib)=={'timestep','gravity','integrator'}
    assert float(option.get('timestep'))==.002 and option.get('integrator')=='implicitfast'
    assert root.find('./worldbody/body[@name="part_1"]/freejoint') is None
    assert root.find('./worldbody/body[@name="part_0"]/freejoint') is not None
    default=root.find('./default/geom')
    assert list(map(float,default.get('friction').split()))==CFG['rollout']['friction']
    assert root.find('./worldbody/geom').get('name')=='floor'
    model=mujoco.MjModel.from_xml_string(xml)
    baseline=mujoco.MjModel.from_xml_string('<mujoco/>')
    assert model.opt.iterations==baseline.opt.iterations
    assert model.opt.solver==baseline.opt.solver and model.opt.cone==baseline.opt.cone


def test_normal_only_control_changes_only_condim_and_friction():
    parts=meshes();ids=sorted(parts)
    baseline,_=_rigid_flex_xml(parts,ids,['receiver'],CFG['rollout'],150/1024,0.)
    diagnostic,_=_rigid_flex_xml(parts,ids,['receiver'],dict(CFG['rollout'],friction=[0.,0.,0.]),
        150/1024,0.,normal_only_diagnostic=True)
    a=list(ET.fromstring(baseline).iter());b=list(ET.fromstring(diagnostic).iter())
    assert len(a)==len(b)
    changed=[]
    for before,after in zip(a,b):
        assert before.tag==after.tag
        assert set(before.attrib)==set(after.attrib)
        for key in before.attrib:
            if before.get(key)!=after.get(key):changed.append((before.tag,key,before.get(key),after.get(key)))
    assert changed and all(key in {'friction','condim'} for _,key,_,_ in changed)
    assert sum(key=='condim' and after=='1' for _,key,_,after in changed)==2
    with pytest.raises(ValueError,match='REQUIRES_ZERO_FRICTION'):
        _rigid_flex_xml(parts,ids,['receiver'],CFG['rollout'],150/1024,0.,normal_only_diagnostic=True)


def test_complete_vendor_xml_executes_without_adsl_imports():
    code="""import sys,numpy as np,trimesh
from snap3d.vendor.rigid_flex import xml_model
mesh=trimesh.creation.box([2,2,2])
text=xml_model({'connections':[{'parameters':{'fit_offset_mm':.1}}]}, {}, {},
 {'collision_backend':'rigid_flex','timestep_seconds':.002,'friction':[.5,.005,.0001]},1240,
 body_layout={'bodies':{'body':{'mesh':mesh,'assembly_transform_mm':np.eye(4)}}})
assert '<flex' in text
assert not any(k=='adsl' or k.startswith('adsl.') for k in sys.modules)
"""
    subprocess.run([sys.executable,'-c',code],env=dict(os.environ,PYTHONPATH=str(Path('.').resolve())),check=True)


def load_gate():
    spec=importlib.util.spec_from_file_location('aligned_gate',Path('scripts/lock_gate.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module


def test_gate_completes_order_then_always_runs_formal_friction_boxes(tmp_path,monkeypatch):
    gate=load_gate();calls=[]
    def fake(parts,**kwargs):
        index=len(calls);calls.append(kwargs)
        p={name:dict(position_mm=[0.,0.,0.],rotation=np.eye(3).tolist(),linear_velocity_world_m_s=[0.,0.,0.]) for name in parts}
        q={name:dict(**pose) for name,pose in p.items()}
        if index==1:q['peg']=dict(q['peg'],position_mm=[0.,0.,-10.])
        return dict(status='PASS',reason=None,frames=[p,q],metrics={
            'receiver':{'max_displacement_mm':0.},'peg':{'max_displacement_mm':10. if index==1 else 0.}})
    monkeypatch.setattr(gate,'rollout',fake)
    result=gate.rigid_flex_gate(tmp_path,CFG)
    assert result['status']=='PASS' and result['normal_only_control'] is None
    assert [r['test'] for r in result['tests']]==['zero_gravity','no_head','headed']
    assert len(calls)==4 and [c['config']['gravity'] for c in calls]==[0.,9.81,9.81,0.]
    assert all(c['config']['friction']==CFG['rollout']['friction'] for c in calls)


def test_first_ten_steps_records_xyz_and_speeds_at_aligned_dt():
    gate=load_gate();frames=[]
    for i in range(12):
        frames.append({'peg':dict(position_mm=[10+i,20-2*i,30+3*i],linear_velocity_world_m_s=[1.,2.,2.])})
    record=gate.first_ten_steps(dict(frames=frames,max_linear_speed_m_s=4.),.002)
    assert record['complete'] and record['available_steps']==10
    assert record['steps'][0]['displacement_xyz_mm']==[1.,-2.,3.]
    assert record['steps'][-1]['displacement_xyz_mm']==[10.,-20.,30.]
    assert record['steps'][-1]['time_s']==.02
    assert record['max_speed_first_ten_m_s']==3 and record['max_speed_whole_rollout_m_s']==4
