from pathlib import Path
import ast
import copy
import hashlib
import importlib.util
import json
import sys
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import trimesh
import yaml

from snap3d import rollout as physics
from snap3d.vendor import rigid_flex as vendor

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def box(size,center):
    mesh=trimesh.creation.box(size);mesh.apply_translation(center);return mesh


def parts():
    return {'receiver':box([10,10,2],[0,0,1]),'peg':box([2,2,2],[0,0,5])}


def run(meshes=None,*,clearance=.1,config=None,**kwargs):
    return physics.rollout(parts() if meshes is None else meshes,D=150.,config=CFG['rollout'] if config is None else config,
        coacd_config=CFG['coacd'],clearance_mm=clearance,fixed_ids=['receiver'],**kwargs)


def test_vendor_source_provenance_and_exact_xml_block_ast():
    directory=Path('snap3d/vendor')
    record=json.loads((directory/'rigid_flex_block_ast.json').read_text())
    provenance=next(r for r in json.loads((directory/'provenance.json').read_text()) if r['file']=='rigid_flex.py')
    assert provenance['commit']==record['commit']=='9e1f01dc32812bf649dc2f58fdc027c7c3dd8d7d'
    assert provenance['source']==record['source']=='adsl-agents/assembly_standing.py'
    assert len(provenance['upstream_sha256'])==64
    tree=ast.parse((directory/'rigid_flex.py').read_text())
    helper=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==record['helper'])
    block=ast.Module(body=helper.body[record['helper_prefix_statements']:],type_ignores=[])
    assert hashlib.sha256(ast.dump(block,include_attributes=False).encode()).hexdigest()==record['block_ast_sha256']
    # Exact copied settings/legacy/values functions are also covered by the
    # existing vendor-function AST test and its source-generated hash record.


@pytest.mark.parametrize('clearance,expected',[(.1,.005),(.4,.01)])
def test_rigid_flex_xml_display_disabled_radius_and_com_frame(clearance,expected):
    result=run(clearance=clearance,initial_only=True)
    assert result['status']=='PASS' and result['collision_backend']=='rigid_flex'
    assert result['proxy_counts']=={} and result['simulation_performed'] is False
    root=ET.fromstring(result['xml'])
    displays=root.findall('./worldbody/body/geom')
    assert len(displays)==2
    assert all(g.get('contype')=='0' and g.get('conaffinity')=='0' and g.get('density')=='0' for g in displays)
    flexes=root.findall('./deformable/flex')
    assert len(flexes)==2 and result['flex_element_count']==24
    for f in flexes:
        assert f.get('dim')=='2' and float(f.get('radius'))==pytest.approx(expected*.001)
        c=f.find('contact')
        assert c.get('contype')==c.get('conaffinity')=='1'
        assert c.get('margin')==c.get('gap')=='0' and c.get('selfcollide')=='none'
    # Body origin and explicit material inertia are preserved; only the vendor
    # input view is translated to COM-local coordinates, once.
    meshes=parts()
    for k,name in enumerate(sorted(meshes)):
        body=root.find(f"./worldbody/body[@name='part_{k}']")
        pos=np.fromstring(body.get('pos'),sep=' ')
        display=root.find(f"./asset/mesh[@name='display_{k}']")
        local=np.fromstring(display.get('vertex'),sep=' ').reshape(-1,3)
        np.testing.assert_allclose((local+pos)*1000,meshes[name].vertices,atol=1e-12)
        assert (body.find('freejoint') is None)==(name=='receiver')
        inertia=body.find('inertial')
        assert float(inertia.get('mass'))==pytest.approx(meshes[name].volume*1e-9*CFG['rollout']['density'])
        np.testing.assert_allclose(np.fromstring(inertia.get('pos'),sep=' '),0)


def test_shell_sum_cannot_consume_clearance():
    for radius in [.05,.06]:
        config=dict(CFG['rollout'],rigid_flex=dict(radius_mm=radius))
        with pytest.raises(ValueError,match='RIGID_FLEX_CONTACT_UNVERIFIED'):
            vendor.flex_contact_settings({'connections':[{'parameters':{'fit_offset_mm':.1}}]},config)
        result=run(config=config,clearance=.1,initial_only=True)
        assert result['status']=='UNAVAILABLE' and result['reason']=='RIGID_FLEX_CONTACT_UNVERIFIED'


def test_initial_flex_penetration_is_nonblocking_with_fake_driver(monkeypatch):
    model=SimpleNamespace(nflex=2,flex_vertadr=[0,1],flex_vertbodyid=[1,2],flex_elemnum=[12,12],geom_bodyid=[0,1,2])
    contact=SimpleNamespace(geom=[-1,-1],flex=[0,1],dist=-.001)
    fake=SimpleNamespace(MjModel=SimpleNamespace(from_xml_string=lambda _:model),
        MjData=lambda _:SimpleNamespace(contact=[contact],ncon=1),mj_forward=lambda *_:None)
    monkeypatch.setitem(sys.modules,'mujoco',fake)
    result=run(initial_only=True)
    assert result['status']=='PASS'
    assert result['initial_contact']['maximum_penetration_mm']==1.
    assert result['initial_contact']['contact_count']==1
    assert result['initial_contact']['affects_verdict'] is False
    assert result['initial_contact']['pass'] is None


def test_contact_ids_resolve_flex_and_ground_without_negative_index():
    model=SimpleNamespace(geom_bodyid=[0,7,9])
    contact=SimpleNamespace(geom=[-1,-1],flex=[0,1])
    assert physics._contact_bodies(model,contact,{0:7,1:9})==(7,9)
    contact=SimpleNamespace(geom=[-1,0],flex=[0,-1])
    assert physics._contact_bodies(model,contact,{0:7})==(7,0)


def test_rigid_flex_pipeline_skips_every_proxy_builder(tmp_path,monkeypatch):
    from snap3d import pipeline
    from snap3d.common import write_json,digest,code_identity
    write_json(tmp_path/'case.json',dict(D_mm=150.,config_sha256=digest(CFG),code_identity=code_identity()))
    write_json(tmp_path/'cgr/stage.json',dict(status='PASS'))
    write_json(tmp_path/'cgr/contact.json',dict(edges=[],parameters={},connectors=[]))
    monkeypatch.setattr(pipeline,'load_parts',lambda _:parts())
    def forbidden(*a,**k):raise AssertionError('rigid_flex must not build or verify zone proxies')
    monkeypatch.setattr(pipeline,'build_zone_proxies',forbidden)
    monkeypatch.setattr(pipeline,'verify_zone_proxies',forbidden)
    calls=[]
    def fake(*a,**k):
        calls.append(k)
        return dict(status='PASS',reason=None,stable=True,flex_element_count=24,initial_contact={'affects_verdict':False})
    monkeypatch.setattr(pipeline,'rollout',fake)
    pipeline.evaluate_geometry(tmp_path,CFG,stage='rollout')
    assert len(calls)==1 and 'proxy_cache' not in calls[0]
    result=json.loads((tmp_path/'rollout/result.json').read_text())
    assert result['status']=='PASS'
    evidence=json.loads((tmp_path/'rollout/proxy_check_0000.json').read_text())
    assert evidence['collision_backend']=='rigid_flex' and evidence['proxy_construction'] is False


def test_rigid_flex_gate_stops_and_runs_only_coplanar_control(tmp_path,monkeypatch):
    spec=importlib.util.spec_from_file_location('rigid_flex_gate',Path('scripts/lock_gate.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    def fake(meshes,**kwargs):
        calls.append(kwargs)
        pose={n:dict(position_mm=[0.,0.,0.],rotation=np.eye(3).tolist()) for n in meshes}
        return dict(status='PASS',reason=None,frames=[copy.deepcopy(pose),copy.deepcopy(pose)],
            metrics={'receiver':{'max_displacement_mm':0.},'peg':{'max_displacement_mm':1.}},
            flex_element_count=24,flex_element_counts={'peg':12,'receiver':12},
            initial_contact={'affects_verdict':False},initial_contact_count=1,max_contact_count=1,final_contact_count=1)
    monkeypatch.setattr(module,'rollout',fake)
    def forbidden(*a,**k):raise AssertionError('no zone builder in rigid_flex gate')
    monkeypatch.setattr(module,'build_zone_proxies',forbidden)
    report=module.rigid_flex_gate(tmp_path,CFG)
    assert report['status']=='FAIL' and report['not_run']==['no_head','headed']
    assert len(calls)==2 and report['coplanar_control']['diagnostic_only']
    assert report['coplanar_control']['connector_count']==0
    assert calls[0]['config']==calls[1]['config']
    assert calls[0]['clearance_mm']==calls[1]['clearance_mm']


def test_default_backend_and_real_rigid_flex_free_fall():
    assert CFG['rollout']['collision_backend']=='rigid_flex'
    result=run(config=dict(CFG['rollout'],duration=.03))
    assert result['status']=='PASS' and result['proxy_counts']=={}
    assert result['metrics']['receiver']['max_displacement_mm']==0
    assert result['frames'][-1]['peg']['position_mm'][2]<result['frames'][0]['peg']['position_mm'][2]
    assert result['initial_contact']['affects_verdict'] is False
    assert result['flex_element_count']==24
    assert result['wall_seconds']>0


def test_solver_warning_retains_available_contact_and_flex_metadata(monkeypatch):
    import mujoco
    def warning_step(model,data):data.warning[0].number=1
    monkeypatch.setattr(mujoco,'mj_step',warning_step)
    result=run(config=dict(CFG['rollout'],duration=.002))
    assert result['status']=='UNAVAILABLE' and result['reason']=='SIMULATION_ERROR'
    assert result['flex_element_count']==24
    assert result['initial_contact']['affects_verdict'] is False
    assert result['initial_contact_count']>=0 and result['max_contact_count']>=0
    assert result['simulated_seconds']==0 and result['wall_seconds']>0
