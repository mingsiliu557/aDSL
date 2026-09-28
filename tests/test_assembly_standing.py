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
    xml=standing.xml_model(report,meshes,{},flex_config(),MATERIAL['density_kg_m3'])
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
        assert contact.get('selfcollide')=='none' and contact.get('internal')=='false'
        assert contact.get('contype')==contact.get('conaffinity')=='1'
        assert contact.get('margin')==contact.get('gap')=='0'
    for body in root.findall('worldbody/body'):
        mesh=meshes[body.get('name')].copy();mesh.apply_scale(.001)
        assert float(body.find('inertial').get('mass'))==pytest.approx(mesh.volume*MATERIAL['density_kg_m3'])
        assert body.find('geom').get('contype')==body.find('geom').get('conaffinity')=='0'


def test_radius_budget_and_legacy_backend(tmp_path):
    _,report=fixture(tmp_path)
    c=standing.flex_contact_settings(report,flex_config())
    assert c['remaining_minimum_clearance_mm']==pytest.approx(.18)
    for radius in [0,-1,.1,float('nan')]:
        with pytest.raises(ValueError,match='RIGID_FLEX_CONTACT_UNVERIFIED'):
            standing.flex_contact_settings(report,flex_config()|{'rigid_flex':{'radius_mm':radius}})
    args,_=fixture(tmp_path/'old');_,_,meshes=load_parts(args)
    old=ET.fromstring(standing.xml_model(report,meshes,{k:[v] for k,v in meshes.items()},STANDING,1240))
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


def test_deep_initial_intersection_never_becomes_stable_pass(tmp_path,monkeypatch):
    args,_=fixture(tmp_path)
    def fake_simulate(*a,**kw):
        return dict(tipped=False,exits={},settled=True,interface_retention_verified=True,
            assessment_time_seconds=5.,final_tilt_deg=0.,final_exited_interfaces=[],
            initial_minimum_contact_distance_mm=-30.),[]
    monkeypatch.setattr(standing,'simulate',fake_simulate)
    monkeypatch.setattr(standing,'save_frames',lambda *a:[])
    result=standing.analyze(args,{'standing':flex_config(),'material':MATERIAL})
    assert result.status=='INDETERMINATE'
    assert result.summary.startswith('INITIAL_CONTACT_INTERPENETRATION')
    assert not result.findings  # Do not guide geometry edits from this simulation.


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
