from pathlib import Path
import copy
import json
import time

import manifold3d as mf
import numpy as np
import pytest
import trimesh
import yaml

from snap3d import proxies as p
from snap3d import rollout as physics
from snap3d.cem import optimize
from snap3d.connector import rebuild, shapes
from snap3d.vendor import checked, mesh_solid, solid_mesh
from snap3d.common import union

CFG = yaml.safe_load(Path('configs/default.yaml').read_text())
D = 150.


def record(**changes):
    T = np.eye(4); T[2, 3] = 20.
    out = dict(edge='lock', donor='donor', receiver='receiver', T=T.tolist(),
               radius=.006*D, length=5*D/256, clearance=D/1024,
               overhang=3*D/1024, head_thickness=D/256, headed=True)
    out.update(changes)
    return out


def box(size, center):
    mesh = trimesh.creation.box(size); mesh.apply_translation(center); return mesh


def assembly(rec=None):
    rec = record() if rec is None else rec
    base = dict(receiver=box([20, 20, 10], [0, 0, 25]), donor=box([5, 5, 4], [0, 0, 18]))
    peg, slot = shapes(radius=rec['radius'], length=rec['length'], D=D, headed=rec['headed'])
    T = np.asarray(rec['T'])[:3]
    parts = dict(receiver=solid_mesh(checked(mesh_solid(base['receiver'])-slot.transform(T))),
                 donor=solid_mesh(checked(mesh_solid(base['donor'])+peg.transform(T))))
    return base, parts, rec


@pytest.fixture
def fast_coacd(monkeypatch):
    calls = []
    def fake(mesh, options):
        calls.append(copy.deepcopy(options))
        return [mesh.convex_hull]
    monkeypatch.setattr(p, 'bounded_proxies', fake)
    return calls


@pytest.fixture
def initial_stub(monkeypatch):
    calls = []
    def fake(*args, **kwargs):
        assert kwargs['initial_only'] is True
        calls.append(kwargs)
        return dict(status='PASS', reason=None, initial_contact=dict(
            maximum_penetration_mm=0., bound_mm=kwargs['clearance_mm']/4, **{'pass':True}))
    monkeypatch.setattr(physics, 'rollout', fake)
    return calls


def test_sector_width_is_largest_allowed_and_blocks_are_convex():
    rec = record()
    blocks, stats = p.analytic_blocks(rec)
    for label, inner in [('1',rec['radius']+rec['clearance']),
                         ('2',rec['radius']+rec['overhang']+2*rec['clearance'])]:
        k = stats['k'+label]
        assert 64 % k == 0
        assert stats['sagitta'+label+'_mm'] <= rec['clearance']/4
        assert all(inner*(1-np.cos(j*np.pi/64)) > rec['clearance']/4
                   for j in [1,2,4,8,16,32,64] if j > k)
    assert all(mesh.is_volume and mesh.is_convex for _,mesh in blocks)


def test_sector_vertices_match_actual_manifold_slot_angles():
    rec = record()
    _, slot = shapes(radius=rec['radius'], length=rec['length'], D=D)
    vertices = np.asarray(solid_mesh(slot).vertices)
    radius = rec['radius']+rec['clearance']
    rim = vertices[np.isclose(vertices[:,2],-rec['clearance']) &
                   np.isclose(np.linalg.norm(vertices[:,:2],axis=1),radius)]
    actual = sorted(map(tuple,np.round(rim[:,:2],10)))
    expected = sorted(map(tuple,np.round(radius*np.column_stack(
        [np.cos(p.cylinder_angles()),np.sin(p.cylinder_angles())]),10)))
    assert len(actual) == 64 and actual == expected


@pytest.mark.parametrize('headed',[True,False])
def test_k1_sleeve_union_equals_exact_material(headed):
    rec = record(headed=headed)
    blocks,_ = p.analytic_blocks(rec,k_override=1)
    sleeve = union(mesh_solid(m) for role,m in blocks if role.startswith('sleeve'))
    r,l,c,h = [rec[n] for n in ['radius','length','clearance','overhang']]
    _,slot = shapes(radius=r,length=l,D=D,headed=headed)
    expected = checked(p.cylinder(r+h+3*c,c,l+2*c)-slot)
    expected = p._world(expected,rec)
    error = float(checked(sleeve-expected).volume())+float(checked(expected-sleeve).volume())
    assert error <= 1e-9*D**3


@pytest.mark.parametrize('rotated',[False,True])
def test_convex_clip_preserves_exact_difference_volume(rotated):
    rec = record()
    if rotated:
        from scipy.spatial.transform import Rotation
        T=np.asarray(rec['T']);T[:3,:3]=Rotation.from_rotvec([.3,-.4,.2]).as_matrix();rec['T']=T.tolist()
    mesh = box([12,12,12],[0,0,20])
    pieces, stats = p.clip_convex(mesh,rec,D=D)
    zone,_ = p.zone(rec)
    expected = checked(mesh_solid(mesh)-zone)
    assert pieces and all(m.is_convex and m.is_volume for m in pieces)
    assert sum(m.volume for m in pieces)+stats['discarded_volume_mm3'] == pytest.approx(expected.volume(),abs=1e-9*D**3)
    for piece in pieces:
        assert checked(mesh_solid(piece)^zone).volume() <= 1e-9*D**3


def test_clip_discards_and_counts_subthreshold_fragments():
    rec=record();top=20+rec['length']+2*rec['clearance']
    mesh=box([.01,.01,.02],[0,0,top-.0099])
    pieces,stats=p.clip_convex(mesh,rec,D=D)
    assert pieces==[] and stats['discarded_count']>=1
    assert 0 < stats['discarded_volume_mm3'] < 1e-9*D**3


def test_all_parts_far_field_clipped_and_roles_recorded(tmp_path,fast_coacd):
    base,_,rec=assembly()
    base['third']=box([8,8,3],[0,0,21])
    values,report=p.build_zone_proxies(base,[rec],D=D,config=CFG['coacd'],cache_dir=tmp_path)
    z,_=p.zone(rec)
    for part,meshes in values.items():
        for mesh,role in zip(meshes,report['blocks'][part]):
            if role['role']=='far':
                assert checked(mesh_solid(mesh)^z).volume()<=1e-9*D**3
    assert len(fast_coacd)==3
    assert all(x['threshold_mm']==pytest.approx(.01*D) and 'far_threshold_ratio' not in x for x in fast_coacd)


def test_cache_hits_mesh_config_changes_and_corrupt_hash(tmp_path,fast_coacd):
    mesh=box([2,2,2],[0,0,2])
    first,a=p.far_proxies(mesh,D=D,config=CFG['coacd'],cache_dir=tmp_path)
    second,b=p.far_proxies(mesh,D=D,config=CFG['coacd'],cache_dir=tmp_path)
    assert not a['cache_hit'] and b['cache_hit'] and len(fast_coacd)==1
    np.testing.assert_array_equal(first[0].vertices,second[0].vertices)
    moved=mesh.copy();moved.apply_translation([.1,0,0])
    p.far_proxies(moved,D=D,config=CFG['coacd'],cache_dir=tmp_path)
    p.far_proxies(mesh,D=D,config=dict(CFG['coacd'],mcts_iterations=151),cache_dir=tmp_path)
    assert len(fast_coacd)==3
    path=next((tmp_path/a['cache_key']).glob('*.stl'))
    with path.open('ab') as f:f.write(b'corrupted')
    _,c=p.far_proxies(mesh,D=D,config=CFG['coacd'],cache_dir=tmp_path)
    assert not c['cache_hit'] and len(fast_coacd)==4


def test_mock_cem_reuses_immutable_base_coacd(tmp_path,fast_coacd):
    base,_,rec=assembly()
    def evaluate(theta):
        candidate=dict(rec,radius=float(theta[4]))
        p.build_zone_proxies(base,[candidate],D=D,config=CFG['coacd'],cache_dir=tmp_path)
        return dict(status='PASS',local_score=float(np.sum(theta**2)),global_score=.5)
    result=optimize([0,0,0,0,rec['radius'],rec['length']],D=D,
        config=dict(CFG['cem'],population=3,elite=2,generations=2,max_draws=6),evaluate=evaluate,baseline_global=1.)
    assert result['evaluations']==6 and len(fast_coacd)==len(base)


def test_watchdog_terminates_blocking_native_worker(monkeypatch):
    import multiprocessing as mp
    if 'fork' not in mp.get_all_start_methods():pytest.skip('fork test stub unavailable')
    get=mp.get_context
    monkeypatch.setattr(p.mp,'get_context',lambda _:get('fork'))
    def block(*_):time.sleep(30)
    monkeypatch.setattr(p,'collision_proxies',block)
    started=time.monotonic()
    with pytest.raises(RuntimeError,match='COACD_TIMEOUT'):
        p.bounded_proxies(box([1,1,1],[0,0,1]),{},seconds=.05)
    assert time.monotonic()-started<5


def verify(parts,values,rec,report):
    return p.verify_zone_proxies(parts,values,[rec],D=D,roles=report['blocks'],
        rollout_config=CFG['rollout'],clearance_mm=rec['clearance'],fixed_ids=['receiver'])


def test_zone_acceptance_positive_with_mock_initial_contacts(tmp_path,fast_coacd,initial_stub):
    base,parts,rec=assembly()
    values,report=p.build_zone_proxies(base,[rec],D=D,config=CFG['coacd'],cache_dir=tmp_path)
    result=verify(parts,values,rec,report)
    assert result['status']=='PASS' and len(initial_stub)==1
    assert result['connectors'][0]['cavity_pass'] and result['connectors'][0]['shoulder_pass']


@pytest.mark.parametrize('bad',['whole_hull','missing_L1','unclipped_far'])
def test_zone_acceptance_rejects_bad_proxies(tmp_path,fast_coacd,initial_stub,bad):
    base,parts,rec=assembly()
    values,report=p.build_zone_proxies(base,[rec],D=D,config=CFG['coacd'],cache_dir=tmp_path)
    if bad=='missing_L1':
        keep=[i for i,r in enumerate(report['blocks']['receiver']) if r['role']!='sleeve_L1']
        values['receiver']=[values['receiver'][i] for i in keep]
        report['blocks']['receiver']=[report['blocks']['receiver'][i] for i in keep]
    else:
        values['receiver']=[parts['receiver'].convex_hull if bad=='whole_hull' else base['receiver']]
        report['blocks']['receiver']=[dict(role='far',edge=None)]
    result=verify(parts,values,rec,report)
    assert result['status']=='FAIL' and result['reason']=='PROXY_UNVERIFIED'
    assert not result['connectors'][0]['shoulder_pass' if bad=='missing_L1' else 'cavity_pass']
    assert not initial_stub


def test_missing_proxies_no_fallback():
    parts={'fixed':box([2,2,2],[0,0,1]),'free':box([2,2,2],[5,0,5])}
    result=physics.rollout(parts,D=D,config=CFG['rollout'],coacd_config=CFG['coacd'],clearance_mm=.1)
    assert result['status']=='UNAVAILABLE' and result['reason']=='PROXY_MISSING'


def test_initial_penetration_rejected_with_fake_mujoco(monkeypatch):
    import sys
    from types import SimpleNamespace
    parts={'fixed':box([2,2,2],[0,0,1]),'free':box([2,2,2],[.5,0,1])}
    depth=min(parts['fixed'].bounds[1]-parts['free'].bounds[0])
    fake=SimpleNamespace(MjModel=SimpleNamespace(from_xml_string=lambda _:SimpleNamespace(geom_bodyid=[0,1,2])),
        MjData=lambda _:SimpleNamespace(contact=[SimpleNamespace(geom1=1,geom2=2,dist=-depth*.001)]),
        mj_forward=lambda *_:None)
    monkeypatch.setitem(sys.modules,'mujoco',fake)
    result=physics.rollout(parts,D=D,config=CFG['rollout'],coacd_config=CFG['coacd'],clearance_mm=.1,
        fixed_ids=['fixed'],proxy_cache={i:[m] for i,m in parts.items()},initial_only=True)
    assert result['reason']=='PROXY_UNVERIFIED'
    assert result['initial_contact']['maximum_penetration_mm']>result['initial_contact']['bound_mm']
    assert result['simulation_performed'] is False


def test_connector_records_do_not_change_rebuild_geometry():
    base,_,rec=assembly()
    edge=dict(id='lock',a='donor',b='receiver',donor='donor',receiver='receiver',anchor=[0,0,20],inward=[0,0,1])
    parts,records=rebuild(base,[edge],{'lock':[0,0,0,0,rec['radius'],rec['length']]},D=D,config=CFG['connector'])
    expected=assembly()[1]
    for part in parts:
        diff=checked(mesh_solid(parts[part])-mesh_solid(expected[part]))
        reverse=checked(mesh_solid(expected[part])-mesh_solid(parts[part]))
        assert diff.volume()+reverse.volume()<=1e-9*D**3
    T=np.asarray(records[0]['T'])
    assert T.shape==(4,4)
    np.testing.assert_allclose(T[:3,3],edge['anchor'])
    assert records[0]['donor']=='donor' and records[0]['receiver']=='receiver'


def test_sequential_zones_leave_no_far_material_in_either(tmp_path,fast_coacd):
    base,_,rec=assembly()
    other=copy.deepcopy(rec);other['edge']='second';other['T'][0][3]=5.
    values,report=p.build_zone_proxies(base,[rec,other],D=D,config=CFG['coacd'],cache_dir=tmp_path)
    for part,meshes in values.items():
        for mesh,role in zip(meshes,report['blocks'][part]):
            if role['role']!='far':continue
            for lock in [rec,other]:
                z,_=p.zone(lock)
                assert checked(mesh_solid(mesh)^z).volume()<=1e-9*D**3


def test_pipeline_records_connectors_and_stops_unverified_rollout(tmp_path,monkeypatch,fast_coacd):
    from snap3d import pipeline
    from snap3d.common import write_json,digest,code_identity
    base,_,rec=assembly()
    edge=dict(id='lock',a='donor',b='receiver',donor='donor',receiver='receiver',anchor=[0,0,20],inward=[0,0,1])
    parameters={'lock':[0,0,0,0,rec['radius'],rec['length']]}
    write_json(tmp_path/'case.json',dict(D_mm=D,config_sha256=digest(CFG),code_identity=code_identity()))
    write_json(tmp_path/'pge/stage.json',dict(status='PASS'))
    monkeypatch.setattr(pipeline,'load_parts',lambda _:base)
    monkeypatch.setattr(pipeline,'contact_graph',lambda *a,**k:([edge],parameters,[]))
    pipeline.evaluate_geometry(tmp_path,CFG,stage='cgr')
    contact=json.loads((tmp_path/'cgr/contact.json').read_text())
    assert contact['connectors'][0]['edge']=='lock'
    assert np.asarray(contact['connectors'][0]['T']).shape==(4,4)
    monkeypatch.setattr(pipeline,'verify_zone_proxies',lambda *a,**k:dict(status='FAIL',reason='PROXY_UNVERIFIED'))
    def forbidden(*a,**k):raise AssertionError('unverified dynamics must not run')
    monkeypatch.setattr(pipeline,'rollout',forbidden)
    with pytest.raises(ValueError,match='PROXY_UNVERIFIED'):
        pipeline.evaluate_geometry(tmp_path,CFG,stage='rollout')
    result=json.loads((tmp_path/'rollout/result.json').read_text())
    assert result['reason']=='PROXY_UNVERIFIED'
    assert json.loads((tmp_path/'rollout/proxy_check_0000.json').read_text())['verification']['status']=='FAIL'


def test_real_mujoco_initial_penetration_is_rejected():
    parts={'fixed':box([2,2,2],[0,0,1]),'free':box([2,2,2],[.5,0,1])}
    result=physics.rollout(parts,D=D,config=CFG['rollout'],coacd_config=CFG['coacd'],clearance_mm=.1,
        fixed_ids=['fixed'],proxy_cache={i:[m] for i,m in parts.items()},initial_only=True)
    assert result['reason']=='PROXY_UNVERIFIED'
    assert result['initial_contact']['maximum_penetration_mm']>.025
    assert result['simulation_performed'] is False


@pytest.mark.parametrize('headed',[True,False])
def test_gate_fixture_is_only_uniformly_rescaled_original(headed):
    import importlib.util
    spec=importlib.util.spec_from_file_location('lock_gate_fixture',Path('scripts/lock_gate.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    parts,base,rec,diagonal,length=module.fixture(headed)
    old_D=float(np.linalg.norm([18.,18.,24.]));factor=150/old_D
    receiver=mf.Manifold.cube([18,18,12]).translate([-9,-9,12])
    for x in [-8,6]:receiver=receiver+mf.Manifold.cube([2,18,12]).translate([x,-9,0])
    donor=mf.Manifold.cube([5,5,4]).translate([-2.5,-2.5,8])
    peg,slot=shapes(radius=.006*old_D,length=5*old_D/256,D=old_D,headed=headed)
    expected={'receiver':checked(receiver-slot.translate([0,0,12])).scale([factor]*3),
              'peg':checked(donor+peg.translate([0,0,12])).scale([factor]*3)}
    for part,mesh in parts.items():
        assert mesh.is_volume
        actual=mesh_solid(mesh)
        assert checked(actual-expected[part]).volume()+checked(expected[part]-actual).volume()<=1e-9*150**3
    bounds=np.array([m.bounds for m in parts.values()])
    assert np.linalg.norm(bounds[:,1].max(axis=0)-bounds[:,0].min(axis=0))==pytest.approx(150)
    assert diagonal==150 and rec['clearance']==pytest.approx(150/1024)
