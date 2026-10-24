"""Rigid surface flex: no CoACD, no mesh edits, existing standing verdicts."""
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path

import numpy as np
import pytest

from test_assembly_physics import fixture, STANDING, MATERIAL
from adsl.agents import assembly_standing as standing
from adsl.agents.assembly_physics import load_parts, run_assembly_checks
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.utils.execution import ExecutionResult
from adsl.agents.utils.io import write_json


def flex_config():
    return {k:v for k,v in STANDING.items() if k!='coacd'} | {'collision_backend':'rigid_flex'}


def test_flex_xml_rigid_vertices_contact_and_inertia(tmp_path):
    args,report=fixture(tmp_path)
    _,_,meshes=load_parts(args)
    xml=standing.xml_model(report,meshes,{},flex_config(),MATERIAL['density_kg_m3'],
        body_layout=standing._prepare_simulation_bodies(report,meshes))
    root=ET.fromstring(xml)
    assert len(root.findall('worldbody/body/freejoint'))==2
    assert root.find('equality') is None
    flexes=root.findall('deformable/flex')
    for flex in flexes:
        mesh=meshes[flex.get('body')]
        assert flex.get('dim')=='2' and float(flex.get('radius'))==pytest.approx(.00001)
        assert np.array_equal(np.fromstring(flex.get('vertex'),sep=' ').reshape(-1,3),mesh.vertices*.001)
        assert np.array_equal(np.fromstring(flex.get('element'),sep=' ',dtype=int).reshape(-1,3),mesh.faces)
        contact=flex.find('contact')
        assert contact.get('selfcollide')=='none'
        assert contact.get('internal')==standing._legacy_flex_contact_options().get('internal')
        assert contact.get('contype')==contact.get('conaffinity')=='1'
        assert contact.get('margin')==contact.get('gap')=='0'
    for body in root.findall('worldbody/body'):
        mesh=meshes[body.get('name')].copy();mesh.apply_scale(.001)
        assert float(body.find('inertial').get('mass'))==pytest.approx(mesh.volume*MATERIAL['density_kg_m3'])
        assert body.find('geom').get('contype')==body.find('geom').get('conaffinity')=='0'


@pytest.mark.parametrize('legacy_internal',[True,False])
def test_flex_xml_internal_option_follows_solver_capability(tmp_path,monkeypatch,legacy_internal):
    import sys
    from types import SimpleNamespace
    model=type('Model',(),{'flex_internal':object()} if legacy_internal else {})
    monkeypatch.setitem(sys.modules,'mujoco',SimpleNamespace(MjModel=model))
    args,report=fixture(tmp_path)
    _,_,meshes=load_parts(args)
    root=ET.fromstring(standing.xml_model(report,meshes,{},flex_config(),MATERIAL['density_kg_m3'],
        body_layout=standing._prepare_simulation_bodies(report,meshes)))
    for contact in root.findall('deformable/flex/contact'):
        assert contact.get('internal')==('false' if legacy_internal else None)
        assert contact.get('selfcollide')=='none'
        assert contact.get('margin')==contact.get('gap')=='0'


def test_real_solver_compiles_rigid_flex_xml(tmp_path):
    mujoco=pytest.importorskip('mujoco')
    args,report=fixture(tmp_path)
    _,_,meshes=load_parts(args)
    xml=standing.xml_model(report,meshes,{},flex_config(),MATERIAL['density_kg_m3'],
        body_layout=standing._prepare_simulation_bodies(report,meshes))
    model=mujoco.MjModel.from_xml_string(xml)
    assert model.nflex==len(meshes)
    assert model.nq==7*len(meshes) and model.nv==6*len(meshes)
    data=mujoco.MjData(model)
    mujoco.mj_forward(model,data)
    mujoco.mj_step(model,data)
    assert data.time==pytest.approx(flex_config()['timestep_seconds'])


def test_radius_budget_and_legacy_backend(tmp_path):
    _,report=fixture(tmp_path)
    c=standing.flex_contact_settings(report,flex_config())
    assert c['remaining_minimum_clearance_mm']==pytest.approx(.18)
    for radius in [0,-1,.1,float('nan')]:
        with pytest.raises(ValueError,match='RIGID_FLEX_CONTACT_UNVERIFIED'):
            standing.flex_contact_settings(report,flex_config()|{'rigid_flex':{'radius_mm':radius}})
    args,_=fixture(tmp_path/'old');_,_,meshes=load_parts(args)
    old=ET.fromstring(standing.xml_model(report,meshes,{k:[v] for k,v in meshes.items()},STANDING,1240,
        body_layout=standing._prepare_simulation_bodies(report,meshes)))
    assert old.find('deformable') is None


def test_flex_analyze_does_not_decompose(tmp_path,monkeypatch):
    args,_=fixture(tmp_path)
    def forbidden(*a,**kw): pytest.fail('rigid_flex called CoACD/proxy validation')
    monkeypatch.setattr(standing,'collision_proxies',forbidden)
    monkeypatch.setattr(standing,'verify_proxies',forbidden)
    # Stop at actual simulator boundary: no optional simulator dependency for
    # this routing-only test; real native cases below test contact behavior.
    def simulate(*a,**kw):raise ValueError('SIMULATION_UNVERIFIED: deliberate boundary')
    monkeypatch.setattr(standing,'simulate',simulate)
    with pytest.raises(ValueError,match='deliberate boundary'):
        standing.analyze(args,{'standing':flex_config(),'material':MATERIAL})
    assert (args.output/'rigid_flex_contact.json').is_file()


@pytest.mark.parametrize('tipping,exit_seen,expected', [
    (False,False,'PASS'), (True,False,'FAIL'), (False,True,'FAIL'), (True,True,'FAIL'),
])
def test_deep_initial_contact_warns_without_erasing_observation(tmp_path,monkeypatch,tipping,exit_seen,expected):
    args,_=fixture(tmp_path)
    def fake_simulate(*a,body_layout,**kw):
        component_rows=[dict(component_id=cid,part_id=body['part_id'],
            peak_tilt_deg=26. if tipping and cid==body_layout['root_component_id'] else 0.,
            final_tilt_deg=0.,first_tipping_seconds=.2 if tipping and cid==body_layout['root_component_id'] else None)
            for cid,body in body_layout['bodies'].items()]
        return dict(component_tilt_verified=True,observed_component_tipping=tipping,
            max_component_tilt_deg=26. if tipping else 0.,component_tilts=component_rows,
            first_tipping_seconds=.2 if tipping else None,tipped=tipping,exits={'joint':.4} if exit_seen else {},
            settled=True,interface_retention_verified=True,
            assessment_time_seconds=5.,final_tilt_deg=0.,peak_tilt_deg=26. if tipping else 0.,
            final_exited_interfaces=[], final_interfaces=[{'connection_id':'joint','exited':False}],
            initial_minimum_contact_distance_mm=-30.),[]
    monkeypatch.setattr(standing,'simulate',fake_simulate)
    monkeypatch.setattr(standing,'save_frames',lambda *a,**kw:[])
    result=standing.analyze(args,{'standing':flex_config(),'material':MATERIAL})
    assert result.status==expected
    assert result.summary.startswith('INITIAL_CONTACT_DEPTH_EXCEEDS_REFERENCE')
    assert result.assumptions['standing_protocol']==standing.STANDING_PROTOCOL
    assert not result.metrics['initial_contact_check']['verified']
    assert result.metrics['contact_diagnostic']['status']=='WARNING'
    assert result.metrics['contact_diagnostic']['affects_standing_verdict'] is False
    observation=result.metrics['standing_observation']
    assert observation['status']==expected and observation['observed_tipping'] is tipping
    assert observation['observed_interface_exit'] is exit_seen  # Exit then return still fails.
    assert {f.rule_id for f in result.findings} == (
        ({'SELF_WEIGHT_TIPPING'} if tipping else set()) |
        ({'SELF_WEIGHT_INTERFACE_EXIT'} if exit_seen else set()))
    json.dumps(result.model_dump(),allow_nan=False)


@pytest.mark.skipif(os.environ.get('ADSL_TEST_RIGID_FLEX')!='1',reason='explicit native CPU smoke')
def test_real_rigid_flex_three_controls(tmp_path):
    import manifold3d as mf
    from scipy.spatial.transform import Rotation
    from adsl.core.assembly_topology import solid_mesh
    results={}
    for name in ['seated','exit','tipping']:
        args,report=fixture(tmp_path/name,inverted=name=='exit',pair=name!='tipping')
        if name=='tipping':
            mesh=solid_mesh(mf.Manifold.cube((10,10,80),True))
            # Same narrow block / 20-degree starting pose as existing test.
            mesh.export(args.manifest.parent/'base.stl',file_type='stl_ascii')
            part=report['parts'][0];part['print_transform_mm']=np.eye(4).tolist()
            tf=np.eye(4);tf[:3,:3]=Rotation.from_euler('y',20,degrees=True).as_matrix()
            part['assembly_transform']=tf.tolist()
            report['files_sha256']['base.stl']=sha256_file(args.manifest.parent/'base.stl')
            write_json(args.manifest,report)
        # Reuse production process-group timeout and INDETERMINATE conversion.
        physics={'standing':flex_config(),'material':MATERIAL}
        execution=ExecutionResult(args.manifest.parent,args.manifest.parent/'scene.glb',None,(),'','')
        run=run_assembly_checks([standing.checker_spec(timeout_seconds=120)],execution=execution,
            source=args.source,root=args.output,physics=physics)[0]
        results[name]=run.result.model_dump()
    write_json(tmp_path/'controls.json',results)
    for r in results.values():
        assert r['metrics'].get('simulated_duration_seconds')==5.,r['summary']
        assert r['metrics']['max_contact_count']>0
        assert r['assumptions']['collision_backend']=='rigid_flex'
        assert r['metrics']['initial_contact_check']['verified']
    assert results['seated']['status']=='PASS'
    assert not results['seated']['metrics']['tipped'] and not results['seated']['metrics']['exits']
    assert results['seated']['metrics']['max_interpart_flex_contacts']>0
    assert results['exit']['status']=='FAIL' and results['exit']['metrics']['exits']
    assert results['tipping']['status']=='FAIL' and results['tipping']['metrics']['peak_tilt_deg']>25


def _layout_cube(size, center=(0,0,0)):
    import manifold3d as mf
    return mf.Manifold.cube(size,center=True).translate(center)


def _layout_report(names, connections=(), *, scale=1.):
    parts=[dict(id=name,assembly_transform=np.eye(4).tolist()) for name in names]
    return dict(root_id=names[0],mm_per_unit=scale,parts=parts,part_declarations=parts,
                connections=list(connections))


def test_body_layout_preserves_cavity_net_volume_root_ids_and_local_coordinates():
    from adsl.core.assembly_topology import solid_mesh
    hollow=_layout_cube((4,4,4))-_layout_cube((3.8,3.8,3.8))
    meshes={'base':solid_mesh(hollow+_layout_cube((3,3,3),(10,0,0))),
            'base__material_0000':solid_mesh(_layout_cube((1,1,1),(20,0,0)))}
    report=_layout_report(list(meshes),scale=10.)
    report['parts'][0]['assembly_transform'][0][3]=3.
    layout=standing._prepare_simulation_bodies(report,meshes)
    assert layout['part_to_components']['base']==['base__material_0000_1','base__material_0001']
    assert layout['part_to_components']['base__material_0000']==['base__material_0000']
    assert layout['root_part_id']=='base' and layout['root_component_id']=='base__material_0001'
    hollow_body=layout['bodies']['base__material_0000_1']
    assert hollow_body['volume_mm3']==pytest.approx(4**3-3.8**3,rel=1e-6)
    assert hollow_body['mesh'].volume==pytest.approx(hollow_body['volume_mm3'])
    assert hollow_body['mesh'].bounds[:,0].tolist()==[-2.,2.]
    assert hollow_body['assembly_transform_mm'][0,3]==30.
    assert layout['bodies']['base__material_0000']['mesh'] is meshes['base__material_0000']
    expected_bounds=np.array([[19.5,-2.,-2.],[41.5,2.,2.]])
    assert np.array_equal(layout['assembly_bounds_mm'],expected_bounds)
    assert layout['D_mm']==pytest.approx(np.linalg.norm(expected_bounds[1]-expected_bounds[0]))
    saved=json.loads(json.dumps(standing._body_mapping(layout),allow_nan=False))
    assert saved['root_component_id']==layout['root_component_id']
    assert saved['bodies']['base__material_0000_1']['part_id']=='base'
    assert saved['bodies']['base__material_0000_1']['assembly_transform_mm'][0][3]==30.
    assert all('mesh' not in body for body in saved['bodies'].values())
    assert len(report['parts'])==2  # N remains the original print-part count.


def test_body_layout_equal_volume_root_uses_fixed_component_order():
    from adsl.core.assembly_topology import solid_mesh
    left,right=_layout_cube((2,2,2),(-2,0,0)),_layout_cube((2,2,2),(2,0,0))
    report=_layout_report(['base'])
    layouts=[standing._prepare_simulation_bodies(report,{'base':solid_mesh(s)})
             for s in (left+right,right+left)]
    assert [l['root_component_id'] for l in layouts]==['base__material_0000']*2
    assert standing._body_mapping(layouts[0])==standing._body_mapping(layouts[1])


def test_component_triangle_digest_uses_exact_coordinates_not_face_numbering():
    from adsl.core.assembly_topology import solid_mesh
    mesh=solid_mesh(_layout_cube((2,3,4)))
    reordered=mesh.copy()
    reordered.faces=np.asarray(reordered.faces)[::-1,::-1]
    assert standing._component_triangle_digest(mesh)==standing._component_triangle_digest(reordered)
    moved=mesh.copy();moved.vertices[0,0]=np.nextafter(moved.vertices[0,0],np.inf)
    assert standing._component_triangle_digest(mesh)!=standing._component_triangle_digest(moved)


def _ownership_inputs():
    from dataclasses import asdict
    from adsl.core.assembly import TabSlot
    from adsl.core.assembly_topology import query_solids
    p=TabSlot(12,6,6,7,.2)
    tab,slot=query_solids(p)
    connection=dict(id='joint',tab_part='stem',slot_part='base',parameters=asdict(p),
        tab_frame=np.eye(4).tolist(),slot_frame=np.eye(4).tolist())
    base=_layout_cube((60,30,12),(0,0,6))-slot
    stem=_layout_cube((20,12,30),(0,0,-15))+tab
    missing=_layout_cube((1,1,1),(100,0,0))+_layout_cube((1,1,1),(110,0,0))
    walls=_layout_cube((2,12,6),(-7.2,0,3))+_layout_cube((2,12,6),(7.2,0,3))
    split_tab=_layout_cube((2,2,2),(-3,0,2))+_layout_cube((2,2,2),(3,0,2))
    return connection,base,stem,missing,walls,split_tab


@pytest.mark.parametrize('receiver,tab,status,tab_count,slot_count',[
    ('split_base','split_stem','BOUND',1,1),
    ('walls','stem','AMBIGUOUS',1,2),
    ('walls','missing','AMBIGUOUS',0,2),
    ('missing','split_tab','AMBIGUOUS',2,0),
    ('base','missing','UNAVAILABLE',0,1),
    ('missing','stem','UNAVAILABLE',1,0),
    ('base','split_tab','AMBIGUOUS',2,1),
])
def test_interface_ownership_unique_zero_multiple_and_ambiguity_priority(
        receiver,tab,status,tab_count,slot_count):
    from adsl.core.assembly_topology import solid_mesh
    connection,base,stem,missing,walls,split_tab=_ownership_inputs()
    choices=dict(base=base,stem=stem,missing=missing,walls=walls,split_tab=split_tab,
        split_base=base+_layout_cube((1,1,1),(100,0,0)),
        split_stem=stem+_layout_cube((1,1,1),(100,0,0)))
    layout=standing._prepare_simulation_bodies(_layout_report(['base','stem'],[connection]),
        {'base':solid_mesh(choices[receiver]),'stem':solid_mesh(choices[tab])})
    row=layout['interfaces']['joint']
    assert row['status']==status
    for role,count in (('tab',tab_count),('slot',slot_count)):
        assert len(row['ownership_evidence'][role]['candidate_component_ids'])==count
        assert (row[f'{role}_component_id'] is not None)==(count==1)
    if receiver=='split_base':
        evidence=row['ownership_evidence']['slot']
        assert evidence['band_mm']==4*evidence['length_tolerance_mm']
        assert sum(w['hit'] for hit in evidence['hits'] for w in hit['wall_probes'])==4
        assert len(evidence['candidate_component_ids'])==1  # Four probes, one body.
        assert all(w['hit']==(w['intersection_mm3']>w['uncertainty_mm3'])
                   for hit in evidence['hits'] for w in hit['wall_probes'])
    if tab=='split_stem':
        evidence=row['ownership_evidence']['tab']
        assert all(hit['hit']==(hit['intersection_mm3']>evidence['volume_tolerance_mm3'])
                   for hit in evidence['hits'])


def test_interface_ownership_singletons_keep_existing_tracking_without_geometry_gate():
    from adsl.core.assembly_topology import solid_mesh
    connection,*_=_ownership_inputs()
    meshes={'base':solid_mesh(_layout_cube((1,1,1),(100,0,0))),
            'stem':solid_mesh(_layout_cube((1,1,1),(110,0,0)))}
    layout=standing._prepare_simulation_bodies(_layout_report(['base','stem'],[connection]),meshes)
    binding=layout['interfaces']['joint']
    assert binding['status']=='BOUND'
    assert binding['tab_component_id']=='stem' and binding['slot_component_id']=='base'
    assert all(e['method']=='single_material_component' for e in binding['ownership_evidence'].values())


def test_interface_ownership_queries_remain_in_original_part_local_mm():
    from adsl.core.assembly_topology import solid_mesh
    connection,base,stem,*_=_ownership_inputs()
    offset=(10.,20.,30.)
    connection['tab_frame'][:]=np.eye(4).tolist()
    connection['slot_frame'][:]=np.eye(4).tolist()
    for role in ('tab','slot'):
        for axis in range(3):connection[f'{role}_frame'][axis][3]=offset[axis]/10.
    meshes={'base':solid_mesh(base.translate(offset)+_layout_cube((1,1,1),(100,0,0))),
            'stem':solid_mesh(stem.translate(offset)+_layout_cube((1,1,1),(100,0,0)))}
    report=_layout_report(['base','stem'],[connection],scale=10.)
    for part in report['parts']:
        part['assembly_transform'][0][3]=5.
    layout=standing._prepare_simulation_bodies(report,meshes)
    binding=layout['interfaces']['joint']
    assert binding['status']=='BOUND'
    assert layout['bodies'][binding['slot_component_id']]['mesh'].bounds[0,0]==pytest.approx(-20.)
    assert layout['bodies'][binding['tab_component_id']]['assembly_transform_mm'][0,3]==50.


@pytest.mark.parametrize('backend',['rigid_flex','coacd'])
def test_disconnected_material_has_separate_xml_bodies_mass_inertia_and_collision(backend):
    from adsl.core.assembly_topology import solid_mesh
    hollow=_layout_cube((4,4,4))-_layout_cube((3,3,3))
    meshes={'base':solid_mesh(hollow+_layout_cube((2,2,2),(10,0,0)))}
    report=_layout_report(['base'])
    layout=standing._prepare_simulation_bodies(report,meshes)
    proxies={cid:[body['mesh']] for cid,body in layout['bodies'].items()}
    config=flex_config() if backend=='rigid_flex' else STANDING
    root=ET.fromstring(standing.xml_model(report,meshes,proxies,config,MATERIAL['density_kg_m3'],
        body_layout=layout))
    xml_bodies=root.findall('worldbody/body')
    assert [body.get('name') for body in xml_bodies]==list(layout['bodies'])
    assert len(root.findall('worldbody/body/freejoint'))==2
    assert root.find('equality') is None
    masses=[]
    for xml_body in xml_bodies:
        body=layout['bodies'][xml_body.get('name')]
        material=body['mesh'].copy();material.apply_scale(.001);material.density=MATERIAL['density_kg_m3']
        inertial=xml_body.find('inertial');inertia=material.moment_inertia
        assert float(inertial.get('mass'))==pytest.approx(material.mass)
        assert np.fromstring(inertial.get('pos'),sep=' ')==pytest.approx(material.center_mass)
        assert np.fromstring(inertial.get('fullinertia'),sep=' ')==pytest.approx(
            [inertia[0,0],inertia[1,1],inertia[2,2],inertia[0,1],inertia[0,2],inertia[1,2]])
        masses.append(float(inertial.get('mass')))
        assert len(xml_body.findall('geom'))==1
    assert sum(masses)==pytest.approx((37.+8.)*1e-9*MATERIAL['density_kg_m3'])
    if backend=='rigid_flex':
        assert [f.get('body') for f in root.findall('deformable/flex')]==list(layout['bodies'])
    else:
        assert root.find('deformable') is None


@pytest.mark.parametrize('backend',['rigid_flex','coacd'])
def test_analyze_keeps_disconnected_parts_as_independent_bodies(tmp_path,monkeypatch,backend):
    from types import SimpleNamespace
    from adsl.core.assembly_topology import solid_mesh
    report=_layout_report(['base'])
    meshes={'base':solid_mesh(_layout_cube((4,4,4))+_layout_cube((2,2,2),(10,0,0)))}
    rows=[dict(part_id='base',status='FAIL',code='INTERNAL_PART_DISCONNECTED',component_count=2)]
    monkeypatch.setattr(standing,'load_parts',lambda args:(report,rows,meshes))
    proxy_meshes=[];validated=[];passed_layouts=[]
    def proxies(mesh,config):
        proxy_meshes.append(mesh)
        return [mesh]
    def verify(mesh,values,clearance):
        validated.append(mesh)
        assert values==[mesh] and clearance is None
        return {'accepted':True},None
    monkeypatch.setattr(standing,'collision_proxies',proxies)
    monkeypatch.setattr(standing,'verify_proxies',verify)
    def simulate(xml,report,config,output,*,body_layout):
        passed_layouts.append(body_layout)
        assert len(body_layout['bodies'])==2
        assert {b.get('name') for b in ET.fromstring(xml).findall('worldbody/body')}==set(body_layout['bodies'])
        measurements=[dict(component_id=cid,part_id=b['part_id'],peak_tilt_deg=0.,
            final_tilt_deg=0.,first_tipping_seconds=None) for cid,b in body_layout['bodies'].items()]
        return dict(tipped=False,exits={},settled=True,interface_retention_verified=True,
            component_tilt_verified=True,observed_component_tipping=False,max_component_tilt_deg=0.,
            component_tilts=measurements,assessment_time_seconds=5.,peak_tilt_deg=0.,final_tilt_deg=0.,
            first_tipping_seconds=None,final_interfaces=[],final_exited_interfaces=[]),[]
    def frames(report,meshes,trajectory,output,*,body_layout):
        assert body_layout is passed_layouts[0]
        return []
    monkeypatch.setattr(standing,'simulate',simulate)
    monkeypatch.setattr(standing,'save_frames',frames)
    monkeypatch.setattr(standing.importlib.metadata,'version',lambda name:'stub')
    config=flex_config() if backend=='rigid_flex' else STANDING
    result=standing.analyze(SimpleNamespace(output=tmp_path),{'standing':config,'material':MATERIAL})
    assert result.status=='PASS' and result.metrics['component_tilt_verified'] is True
    assert len(result.metrics['component_tilts'])==2
    assert result.artifacts['body_mapping']==str(tmp_path/'body_mapping.json')
    saved=json.loads((tmp_path/'body_mapping.json').read_text())
    assert saved['part_to_components']=={'base':['base__material_0000','base__material_0001']}
    assert saved['root_part_id']=='base' and saved['root_component_id']=='base__material_0000'
    if backend=='coacd':
        assert len(proxy_meshes)==len(validated)==2
        assert sorted(m.volume for m in proxy_meshes)==pytest.approx([8.,64.])
    else:
        assert proxy_meshes==validated==[]


@pytest.mark.parametrize('error_type',[ValueError,RuntimeError,np.linalg.LinAlgError])
def test_interface_query_failure_keeps_bodies_known_endpoint_and_other_interfaces(monkeypatch,error_type):
    from adsl.core import assembly_topology as topology
    connection,base,stem,*_=_ownership_inputs()
    meshes={'base':topology.solid_mesh(base+_layout_cube((1,1,1),(100,0,0))),
            'stem':topology.solid_mesh(stem)}
    report=_layout_report(['base','stem'],[connection,dict(connection,id='other')])
    native_query=topology.query_solids
    calls=[]
    def query(parameters):
        calls.append(parameters)
        if len(calls)==1:raise error_type('native query failure '+('x'*300))
        return native_query(parameters)
    monkeypatch.setattr(topology,'query_solids',query)
    layout=standing._prepare_simulation_bodies(report,meshes)
    assert len(layout['bodies'])==3 and len(calls)==2
    assert layout['interfaces']['other']['status']=='BOUND'
    failed=layout['interfaces']['joint']
    assert failed['status']=='UNAVAILABLE' and failed['tab_component_id']=='stem'
    assert failed['slot_component_id'] is None
    assert failed['ownership_evidence']['tab']['method']=='single_material_component'
    evidence=failed['ownership_evidence']['slot']
    assert evidence['error_type']==error_type.__name__ and len(evidence['reason'])==240
    assert evidence['candidate_component_ids']==[]
    json.dumps(standing._body_mapping(layout),allow_nan=False)


@pytest.mark.parametrize('error_type',[TypeError,KeyError])
def test_interface_query_programming_errors_propagate(monkeypatch,error_type):
    from adsl.core import assembly_topology as topology
    connection,base,stem,*_=_ownership_inputs()
    meshes={'base':topology.solid_mesh(base+_layout_cube((1,1,1),(100,0,0))),
            'stem':topology.solid_mesh(stem)}
    def broken(parameters):raise error_type('programming failure')
    monkeypatch.setattr(topology,'query_solids',broken)
    with pytest.raises(error_type,match='programming failure'):
        standing._prepare_simulation_bodies(_layout_report(['base','stem'],[connection]),meshes)


def test_interface_query_boundary_does_not_hide_material_or_physical_input_errors(monkeypatch):
    from adsl.core import assembly_topology as topology
    report=_layout_report(['base'])
    meshes={'base':topology.solid_mesh(_layout_cube((2,2,2)))}
    report['mm_per_unit']=0.
    with pytest.raises(ValueError,match='INVALID_PHYSICAL_INPUT'):
        standing._prepare_simulation_bodies(report,meshes)
    report['mm_per_unit']=1.
    def broken(solid):raise ValueError('invalid material input')
    monkeypatch.setattr(topology,'material_components',broken)
    with pytest.raises(ValueError,match='invalid material input'):
        standing._prepare_simulation_bodies(report,meshes)


@pytest.mark.parametrize('component_tipped',[False,True])
def test_interface_query_failure_continues_analyze_without_erasing_known_component_failure(
        tmp_path,monkeypatch,component_tipped):
    from types import SimpleNamespace
    from adsl.core import assembly_topology as topology
    connection,base,stem,*_=_ownership_inputs()
    report=_layout_report(['base','stem'],[connection])
    meshes={'base':topology.solid_mesh(base+_layout_cube((1,1,1),(100,0,0))),
            'stem':topology.solid_mesh(stem)}
    rows=[dict(part_id='base',status='FAIL',code='INTERNAL_PART_DISCONNECTED'),
          dict(part_id='stem',status='PASS')]
    monkeypatch.setattr(standing,'load_parts',lambda args:(report,rows,meshes))
    def failed_query(parameters):raise RuntimeError('native query unavailable')
    monkeypatch.setattr(topology,'query_solids',failed_query)
    observed=[]
    def simulate(xml,report,config,output,*,body_layout):
        observed.append(body_layout)
        assert len(body_layout['bodies'])==3
        assert body_layout['interfaces']['joint']['status']=='UNAVAILABLE'
        measurements=[dict(component_id=cid,part_id=body['part_id'],
            peak_tilt_deg=90. if component_tipped and cid=='stem' else 0.,
            final_tilt_deg=90. if component_tipped and cid=='stem' else 0.,
            first_tipping_seconds=.5 if component_tipped and cid=='stem' else None)
            for cid,body in body_layout['bodies'].items()]
        return dict(tipped=False,exits={},settled=True,interface_retention_verified=False,
            component_tilt_verified=True,observed_component_tipping=component_tipped,
            max_component_tilt_deg=90. if component_tipped else 0.,component_tilts=measurements,
            assessment_time_seconds=5.,peak_tilt_deg=0.,final_tilt_deg=0.,first_tipping_seconds=None,
            final_interfaces=[dict(connection_id='joint',status='UNAVAILABLE',exited=None)],
            final_exited_interfaces=[],observation_errors=[dict(connection_id='joint',
                reason='INTERFACE_COMPONENT_UNAVAILABLE')]),[]
    monkeypatch.setattr(standing,'simulate',simulate)
    monkeypatch.setattr(standing,'save_frames',lambda *args,**kwargs:[])
    monkeypatch.setattr(standing.importlib.metadata,'version',lambda name:'stub')
    result=standing.analyze(SimpleNamespace(output=tmp_path),{'standing':flex_config(),'material':MATERIAL})
    assert len(observed)==1 and len(result.metrics['component_tilts'])==3
    assert result.status==('FAIL' if component_tipped else 'INDETERMINATE')
    assert result.metrics['standing_observation']['status']==('FAIL' if component_tipped else 'UNAVAILABLE')
    assert result.metrics['standing_observation']['observed_component_tipping'] is component_tipped
    assert {f.rule_id for f in result.findings}==({'SELF_WEIGHT_COMPONENT_TIPPING'} if component_tipped else set())
    saved=json.loads((tmp_path/'body_mapping.json').read_text())
    assert saved['interfaces']['joint']['ownership_evidence']['slot']['error_type']=='RuntimeError'
