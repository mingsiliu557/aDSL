"""Artificial CPU solids only; no generated case execution or Standing runs."""
from copy import deepcopy
import importlib.util
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from benchmark.metrics import geometry as g

FCL = pytest.mark.skipif(importlib.util.find_spec('fcl') is None, reason='native python-fcl unavailable')


def box(center=(0,0,0), size=(1,1,1)):
    mesh = trimesh.creation.box(extents=size); mesh.apply_translation(center); return mesh


def part(name, *meshes, transform=None):
    groups, n = [], 0
    for mesh in meshes:
        groups.append([n,n+len(mesh.faces)]); n += len(mesh.faces)
    return dict(part_id=name,mesh_mm=trimesh.util.concatenate(meshes),face_groups=groups,
                assembly_transform_mm=np.eye(4) if transform is None else transform)


def connectivity(parts, floor=0., ratio=.005):
    return g.measure_connectivity(parts,delta_ratio=ratio,delta_floor_mm=floor)


def test_one_part_success_and_empty_unavailable():
    p=part('whole',box()); before=p['mesh_mm'].vertices.copy()
    r=connectivity([p]);assert r['status']=='PASS' and r['overall_pass'] is True
    assert r['internal_connected'] is True
    assert r['part_count']==r['material_component_count']==r['graph_component_count']==1
    assert r['protocol_id']=='geometric_facing_contact_connectivity_v1'
    assert 'interpart_connected' not in r and 'interpart_graph_component_count' not in r
    assert r['reason_codes']==[]
    assert np.array_equal(before,p['mesh_mm'].vertices)
    r=connectivity([]);assert r['status']=='UNAVAILABLE' and r['overall_pass'] is None


@FCL
@pytest.mark.parametrize('gap,expected',[(.001,True),(.1,False)])
def test_same_gap_uses_same_rule_with_one_or_two_print_parts(gap,expected):
    a,b=box(),box((1+gap,0,0))
    r=connectivity([part('whole',a,b)])
    assert r['status']=='PASS' and r['internal_connected'] is False
    s=connectivity([part('A',a),part('B',b)])
    assert r['overall_pass'] is s['overall_pass'] is expected
    assert r['part_count']==1 and s['part_count']==2
    assert r['material_component_count']==s['material_component_count']==2
    assert r['delta_mm']==s['delta_mm']
    assert r['details']['pairs'][0]['distance_mm']==s['details']['pairs'][0]['distance_mm']
    assert r['reason_codes']==s['reason_codes']==([] if expected else ['GEOMETRIC_GRAPH_DISCONNECTED'])


@FCL
def test_other_part_bridges_material_islands_without_internal_veto():
    p=part('A',box((-1,0,0)),box((1,0,0)))
    bridge=part('B',box(size=(1,1,1)))
    r=connectivity([p,bridge],floor=.01)
    assert r['internal_connected'] is False and r['overall_pass'] is True
    # Exact shared faces form one material island in the assembled union;
    # A's disconnected source grouping remains diagnostic, not a veto.
    assert r['part_count']==2
    assert r['material_component_count']==1 and r['graph_component_count']==1
    assert r['details']['nodes'][0]['part_ids']==['A','B']
    assert r['reason_codes']==[]
    r=connectivity([p,part('far',box((10,0,0)))])
    assert r['internal_connected'] is False and r['overall_pass'] is False
    assert r['graph_component_count']==3
    assert r['reason_codes']==['GEOMETRIC_GRAPH_DISCONNECTED']


@FCL
def test_chain_connected_not_clique_and_ground_is_not_a_node():
    parts=[part(str(i),box((i*1.01,0,0))) for i in range(3)]
    r=connectivity(parts,floor=.02)
    assert r['overall_pass']
    assert len([x for x in r['details']['pairs'] if x['edge']])==2
    r=connectivity([parts[0],part('distant',box((20,0,0)))])
    assert r['overall_pass'] is False and r['graph_component_count']==2


@FCL
def test_delta_below_equal_above_uses_backend_distance_no_extra_tolerance():
    parts=[part('a',box()),part('b',box((1.125,0,0)))]
    r=connectivity(parts,floor=.5,ratio=0.)
    distance=r['details']['pairs'][0]['distance_mm'];assert distance==.125
    assert connectivity(parts,floor=distance,ratio=0.)['overall_pass']
    assert connectivity(parts,floor=np.nextafter(distance,0).item(),ratio=0.)['overall_pass'] is False
    assert connectivity(parts,floor=np.nextafter(distance,1).item(),ratio=0.)['overall_pass']
    assert connectivity(parts)['delta_floor_mm']==0.


@FCL
def test_overlap_containment_contact_and_cavity_are_full_material_queries():
    from adsl.core.assembly_topology import mesh_solid, solid_mesh
    for center in [(0,0,0),(.2,0,0),(1,0,0)]:
        contact=connectivity([part('a',box()),part('b',box(center))])
        assert contact['overall_pass'] and contact['part_count']==2
        assert contact['material_component_count']==1
        assert contact['details']['nodes'][0]['part_ids']==['a','b']
    r=connectivity([part('a',box(size=(3,3,3))),part('inside',box())])
    assert r['material_component_count']==1 and r['part_count']==2
    assert r['details']['nodes'][0]['part_ids']==['a','inside']
    assert r['details']['nodes'][0]['volume_mm3']==pytest.approx(27.)
    # The underlying distance query must still recognize containment rather
    # than measuring the positive separation between the boundary surfaces.
    pair=g._distance(mesh_solid(box(size=(3,3,3))),mesh_solid(box()),delta=.01)
    assert pair['method']=='MANIFOLD_POSITIVE_INTERSECTION' and pair['distance_mm']==0
    hollow=solid_mesh(mesh_solid(box(size=(4,4,4)))-mesh_solid(box(size=(3,3,3))))
    assert connectivity([part('hollow',hollow)])['part_components'][0]['component_count']==1
    r=connectivity([part('hollow',hollow),part('inside_cavity',box())],floor=2.)
    pair=r['details']['pairs'][0]
    assert pair['intersection_mm3']==0 and pair['distance_mm']==pytest.approx(1.)
    assert connectivity([part('union',box(),box((.5,0,0)))])['internal_connected']


@FCL
def test_same_part_cavity_island_preserves_void_and_material_count():
    from adsl.core.assembly_topology import mesh_solid, solid_mesh
    hollow=solid_mesh(mesh_solid(box(size=(4,4,4)))-mesh_solid(box(size=(3,3,3))))
    whole=part('whole',hollow,box())
    r=connectivity([whole])
    assert r['status']=='PASS' and r['overall_pass'] is False
    assert r['part_count']==1 and r['material_component_count']==2
    assert r['graph_component_count']==2
    pair=connectivity([whole],floor=2.)['details']['pairs'][0]
    assert pair['intersection_mm3']==0 and pair['distance_mm']==pytest.approx(1.)
    assert pair['method']=='FCL_TRIANGLE_DISTANCE'
    assert sorted(n['volume_mm3'] for n in r['details']['nodes'])==pytest.approx([1.,37.])
    assert connectivity([whole],floor=1.,ratio=0.)['overall_pass'] is True
    assert [n['component_id'] for n in r['details']['nodes']]==[
        n['component_id'] for n in connectivity([whole])['details']['nodes']]


@FCL
def test_nested_cavities_belong_to_their_own_material_islands():
    from adsl.core.assembly_topology import mesh_solid, solid_mesh
    outer=mesh_solid(box(size=(10,10,10)))-mesh_solid(box(size=(8,8,8)))
    inner=mesh_solid(box(size=(6,6,6)))-mesh_solid(box(size=(4,4,4)))
    r=connectivity([part('whole',solid_mesh(outer+inner))],floor=2.)
    assert r['status']=='PASS' and r['material_component_count']==2
    assert sorted(n['volume_mm3'] for n in r['details']['nodes'])==pytest.approx([152.,488.])
    pair=r['details']['pairs'][0]
    assert pair['intersection_mm3']==0 and pair['distance_mm']==pytest.approx(1.)
    assert r['overall_pass'] is True


def test_multiple_cavities_remain_one_material_component():
    from adsl.core.assembly_topology import mesh_solid, solid_mesh
    solid=(mesh_solid(box(size=(10,10,10)))-mesh_solid(box((-3,0,0),size=(2,2,2)))
           -mesh_solid(box((3,0,0),size=(2,2,2))))
    r=connectivity([part('whole',solid_mesh(solid))])
    assert r['overall_pass'] is True and r['material_component_count']==1
    assert r['details']['nodes'][0]['volume_mm3']==pytest.approx(984.)


@FCL
def test_edge_edge_interior_and_overlapping_aabbs_not_sampled_vertices():
    a=box(size=(10,.2,.2));b=box(center=(0,0,.3),size=(.2,10,.2))
    r=connectivity([part('a',a),part('b',b)],floor=.11)
    pair=r['details']['pairs'][0]
    assert pair['method']=='FCL_TRIANGLE_DISTANCE' and pair['distance_mm']==pytest.approx(.1)
    assert min(np.linalg.norm(a.vertices-np.array(pair['witness_points_mm'][0]),axis=1))>1
    tetra=trimesh.convex.convex_hull(np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]]))
    other=tetra.copy();other.apply_translation((.7,.7,.7))
    r=connectivity([part('a',tetra),part('b',other)],floor=.1)
    assert not r['overall_pass'] and r['details']['pairs'][0]['distance_mm']>0


def test_missing_or_broken_fcl_is_unavailable_not_sampling_fallback(monkeypatch):
    def fail(*args,**kw):raise ValueError('FCL unavailable')
    monkeypatch.setattr(trimesh.collision,'CollisionManager',fail)
    r=connectivity([part('a',box()),part('b',box((1.001,0,0)))])
    assert r['status']=='UNAVAILABLE' and r['overall_pass'] is None
    assert r['internal_connected'] is True and r['graph_component_count'] is None
    assert r['material_component_count']==2
    assert 'DISTANCE_UNAVAILABLE' in r['reason_codes']


def test_open_material_is_unavailable_and_internal_diagnostic_is_not_failure_reason():
    broken=box();broken.update_faces(np.arange(len(broken.faces)-1))
    r=connectivity([part('split',box(),box((3,0,0))),part('open',broken)])
    assert r['status']=='UNAVAILABLE' and r['overall_pass'] is None
    assert r['internal_connected'] is False
    assert r['material_component_count'] is None and r['graph_component_count'] is None
    assert r['reason_codes']==['PART_MATERIAL_UNAVAILABLE']


def fixed_input(tmp_path):
    source=tmp_path/'source.py';source.write_text('raise AssertionError("must never execute")\n')
    mesh=box(size=(2,3,4));print_pose=trimesh.transformations.rotation_matrix(np.pi/2,[0,0,1]);print_pose[:3,3]=[10,20,30]
    printed=mesh.copy();printed.apply_transform(print_pose);stl=tmp_path/'body.stl';printed.export(stl)
    assembly=np.eye(4);assembly[:3,3]=[2,3,4]
    report=dict(source_sha256=g.file_hash(source),mm_per_unit=10.,part_declarations=[{'id':'body'}],
        parts=[dict(id='body',stl=stl.name,print_transform_mm=print_pose.tolist(),assembly_transform=assembly.tolist())],
        files_sha256={stl.name:g.file_hash(stl)},connections=[])
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps(report))
    return dict(geometry_input=dict(kind='fixed_assembly',manifest_path=str(manifest),source_path=str(source),manifest_sha256=g.file_hash(manifest)))


def test_fixed_adapter_inverse_print_transform_and_mm_translation(tmp_path):
    rec=fixed_input(tmp_path);r=g.load_evaluation_parts(rec);assert r['status']=='PASS'
    p=r['parts'][0];assert np.allclose(p['mesh_mm'].extents,[2,3,4])
    assert np.allclose(p['assembly_transform_mm'][:3,3],[20,30,40])
    assert np.allclose(g.assembly_bounds(r['parts'])[0].mean(axis=0),[20,30,40])
    Path(rec['geometry_input']['source_path']).write_text('# changed')
    assert g.load_evaluation_parts(rec)['status']=='UNAVAILABLE'


def single_input(tmp_path):
    scene=trimesh.Scene()
    for name,x in [('a',0),('b',4),('aux',100)]:
        t=np.eye(4);t[0,3]=x;scene.add_geometry(box(),node_name=name,transform=t)
    path=tmp_path/'scene.glb';scene.export(path)
    transform=np.eye(4);transform[2,3]=20.
    return dict(geometry_input=dict(kind='single_print_mesh',mesh_path=str(path),mesh_sha256=g.file_hash(path),
        length_unit_to_mm=2.,file_to_assembly_mm=transform.tolist(),excluded_auxiliary_nodes=['aux']))


def test_scene_adapter_includes_all_nodes_applies_units_once(tmp_path):
    rec=single_input(tmp_path);r=g.load_evaluation_parts(rec);assert r['status']=='PASS'
    assert len(r['parts'])==1
    assert np.allclose(g.assembly_bounds(r['parts'])[0],[[-1,-1,19],[9,1,21]])
    assert connectivity(r['parts'])['part_components'][0]['component_count']==2
    changed=deepcopy(rec);changed['geometry_input']['length_unit_to_mm']=3.
    assert g.load_evaluation_parts(changed)['asset_sha256']!=r['asset_sha256']
    changed['geometry_input']['length_unit_to_mm']=0
    assert g.load_evaluation_parts(changed)['status']=='UNAVAILABLE'


@FCL
def test_multinode_glb_and_equivalent_merged_mesh_have_same_material_graph(tmp_path):
    scene=trimesh.Scene()
    for name,x in [('a',0.),('b',1.001)]:
        transform=np.eye(4);transform[0,3]=x
        scene.add_geometry(box(),node_name=name,transform=transform)
    split=tmp_path/'nodes.glb';scene.export(split)
    merged=tmp_path/'merged.glb'
    trimesh.Scene(trimesh.util.concatenate([box(),box((1.001,0,0))])).export(merged)
    rows=[]
    for path in (split,merged):
        record=dict(geometry_input=dict(kind='single_print_mesh',mesh_path=str(path),
            mesh_sha256=g.file_hash(path),length_unit_to_mm=1.,file_to_assembly_mm=np.eye(4).tolist()))
        loaded=g.load_evaluation_parts(record);assert loaded['status']=='PASS'
        rows.append(connectivity(loaded['parts']))
    assert all(r['status']=='PASS' and r['overall_pass'] for r in rows)
    assert [r['part_count'] for r in rows]==[1,1]
    assert [r['material_component_count'] for r in rows]==[2,2]
    assert [r['graph_component_count'] for r in rows]==[1,1]
    assert rows[0]['delta_mm']==pytest.approx(rows[1]['delta_mm'],abs=1e-8)


def test_support24_prefers_s_over_fake_g_ties_and_records_failed_pose(monkeypatch):
    from adsl.agents import support_volume as sv
    calls=[]
    def measure(mesh,**kw):
        i=len(calls);calls.append(i)
        return dict(status='PASS',support_volume_mm3=1. if i in (7,8) else 5.,mesh_volume_mm3=10.,
            nsv=.1 if i in (7,8) else .5,risky_projected_area_mm2=1.,risky_face_count=1,ray_backend='fixture')
    monkeypatch.setattr(sv,'support_volume',measure)
    from adsl.agents import partition_score
    monkeypatch.setattr(partition_score,'best_print_pose',lambda *a,**k: (_ for _ in ()).throw(AssertionError('G must not select')))
    r=g.measure_support24([part('a',box())],overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.2)
    assert len(calls)==24 and r['details']['items'][0]['selected_rotation_id']==7
    assert r['nsv_total']==.1
    calls.clear()
    def broken(mesh,**kw):
        if len(calls)==3:
            calls.append(3);raise sv.SupportMeasurementError('ray error')
        return measure(mesh,**kw)
    monkeypatch.setattr(sv,'support_volume',broken)
    r=g.measure_support24([part('a',box())],overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.2)
    assert len(calls)==24 and r['status']=='UNAVAILABLE' and r['nsv_total'] is None
    assert r['details']['items'][0]['selected_rotation_id'] is None


def test_native_asymmetric_support24_and_union_weighted_volume():
    from adsl.core.assembly_topology import mesh_solid,solid_mesh
    from adsl.agents.support_volume import support_volume
    stem=mesh_solid(box((0,0,1),size=(2,2,2)))
    cap=mesh_solid(box((0,0,2.5),size=(6,2,1)))
    mesh=solid_mesh(stem+cap)
    parts=[part('T',mesh),part('overlap_union',box(),box((.5,0,0)))]
    r=g.measure_support24(parts,overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.01)
    assert r['status']=='PASS'
    assert r['total_volume_mm3']==pytest.approx(20+1.5)
    for p,item in zip(parts,r['details']['items']):
        selected=item['orientations'][item['selected_rotation_id']]
        material=g._material(p)[1].copy();material.apply_transform(selected['print_transform_mm'])
        measured=support_volume(material,overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.01)
        assert measured['support_volume_mm3']==pytest.approx(item['support']['support_volume_mm3'])
    assert r['nsv_total']==pytest.approx(sum(i['support']['support_volume_mm3'] for i in r['details']['items'])/r['total_volume_mm3'])


def test_two_adapters_to_json_and_main_csv_smoke(tmp_path):
    from benchmark.scripts.evaluate_physical_metrics import evaluate
    from benchmark.metrics.physical import STANDING_PROTOCOL
    one=tmp_path/'fixed';one.mkdir();two=tmp_path/'single';two.mkdir()
    records={}
    for method,rec in [('ours',fixed_input(one)),('baseline',single_input(two))]:
        rec.update(case_id='artificial',applicable=True,state='TERMINAL',protocol_id=STANDING_PROTOCOL,
            checker={},generation_status='COMPLETED')
        records[method]=[rec]
    path=tmp_path/'records.json';path.write_text(json.dumps({'methods':records}));before=path.read_bytes()
    config=Path('benchmark/configs/physical_main_v1.json')
    out=tmp_path/'results';summary=evaluate(path,config,out)
    assert summary['table'][0]['structural_connectivity_rate']==1.
    assert summary['table'][1]['structural_connectivity_rate']==0.
    assert all(r['standing_success_rate']==0 for r in summary['table'])
    assert (out/'main_table.csv').read_text().startswith('method,standing_success_rate,structural_connectivity_rate,nsv,N')
    data=json.loads((out/'measured_records.json').read_text())
    for recs in data['methods'].values():
        rec=recs[0]
        for key in ('connectivity','support_evaluation'):
            ref=rec[key]['evidence_ref'];assert g.file_hash(out/ref['path'])==ref['file_sha256']
    assert path.read_bytes()==before
    with pytest.raises(FileExistsError):evaluate(path,config,out)
    audit=evaluate(path,config,tmp_path/'audit',audit_only=True)
    assert audit=={'audited':2,'missing':0}
    assert not (tmp_path/'audit/main_table.csv').exists()


def test_invalid_coordinates_and_missing_part_id_are_unavailable():
    bad=part('a',box());bad['assembly_transform_mm'][0,0]=float('nan')
    for parts in ([],[bad],[{'mesh_mm':box()}]):
        assert connectivity(parts)['status']=='UNAVAILABLE'
        result=g.measure_support24(parts,overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.2)
        assert result['status']=='UNAVAILABLE' and result['nsv_total'] is None


def test_fixed_mesh_hash_and_composite_asset_binding_are_checked(tmp_path):
    rec=fixed_input(tmp_path)
    bad=deepcopy(rec);bad['asset_sha256']='wrong'
    assert 'binding mismatch' in g.load_evaluation_parts(bad)['reason']
    (tmp_path/'body.stl').write_bytes(b'changed mesh')
    assert 'mismatch' in g.load_evaluation_parts(rec)['reason']


def test_negative_noncollision_distance_is_not_clamped(monkeypatch):
    class BrokenManager:
        def add_object(self,*args):pass
        def in_collision_single(self,*args):return False
        def min_distance_single(self,*args,**kwargs):return -1.,None
    monkeypatch.setattr(trimesh.collision,'CollisionManager',BrokenManager)
    result=connectivity([part('a',box()),part('b',box((1.001,0,0)))])
    assert result['status']=='UNAVAILABLE' and result['overall_pass'] is None
    assert 'invalid FCL' in result['details']['pairs'][0]['reason']


def test_support24_uses_volume_weighted_total_not_mean_ratios(monkeypatch):
    from adsl.agents import support_volume as sv
    calls=[]
    def measure(mesh,**kw):
        index=len(calls)//24;calls.append(index)
        s,v=[(1.,10.),(9.,30.)][index]
        return dict(status='PASS',support_volume_mm3=s,mesh_volume_mm3=v,nsv=s/v,
            risky_projected_area_mm2=1.,risky_face_count=1,ray_backend='fixture')
    monkeypatch.setattr(sv,'support_volume',measure)
    result=g.measure_support24([part('a',box()),part('b',box(size=(2,2,2)))],
        overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.2)
    assert len(calls)==48 and result['nsv_total']==.25
    assert result['nsv_total']!=pytest.approx((.1+.3)/2)


def test_connectivity_config_hash_excludes_case_size_and_asset():
    first=connectivity([part('a',box())]);second=connectivity([part('b',box(size=(3,3,3)))])
    assert first['measurement_config_sha256']==second['measurement_config_sha256']
    assert first['asset_sha256']!=second['asset_sha256']
    assert first['delta_mm']!=second['delta_mm']
