"""Area-weighted samples on artificial surfaces; no Standing/support runs."""
import numpy as np
import pytest
import trimesh

from benchmark.metrics import contact_area as area
from benchmark.metrics import geometry as g


def box(center, size=(1,1,1)):
    mesh = trimesh.creation.box(extents=size)
    mesh.apply_translation(center)
    return mesh


def triangle(z, reverse=False):
    return trimesh.Trimesh([[0,0,z],[1,0,z],[0,1,z]],
                          [[0,2,1] if reverse else [0,1,2]],process=False)


def rectangle(x0,x1,y0,y1,z=0.,reverse=False):
    faces=np.array([[0,1,2],[0,2,3]])
    if reverse:faces=faces[:,::-1]
    return trimesh.Trimesh([[x0,y0,z],[x1,y0,z],[x1,y1,z],[x0,y1,z]],faces,process=False)


def test_parallel_faces_have_area_estimates_and_preserve_mesh():
    a,b = box((0,0,0)),box((1.01,0,0))
    before=[(m.vertices.copy(),m.faces.copy()) for m in (a,b)]
    r=area.facing_area(a,b,delta_mm=.02,minimum_area_mm2=.1)
    assert r['status']=='PASS' and r['edge'] is True
    assert r['facing_area_estimate_mm2']==pytest.approx(1.,abs=.02)
    assert r['method']=='area_weighted_surface_sampling_v1'
    assert 'facing_area_lower_bound_mm2' not in r
    for row in r['directional_areas']:
        assert row['sample_count']==65536 and row['seed']==0
        assert row['area_estimate_mm2']==row['sampling_area_mm2']*row['hit_count']/row['sample_count']
    for mesh,(vertices,faces) in zip((a,b),before):
        assert np.array_equal(mesh.vertices,vertices) and np.array_equal(mesh.faces,faces)


def test_tiny_corner_neighborhood_does_not_pass_large_area_threshold():
    r=area.facing_area(box((0,0,0)),box((1.01,.999,0)),delta_mm=.02,minimum_area_mm2=.2)
    assert r['status']=='PASS' and r['edge'] is False
    assert r['facing_area_estimate_mm2']<.2


def test_same_normals_zero_hits_do_not_claim_exact_zero_area():
    r=area.facing_area(triangle(0),triangle(.001),delta_mm=.01,minimum_area_mm2=.01)
    assert r['edge'] is False and r['facing_area_estimate_mm2']==0
    for row in r['directional_areas']:
        assert row['hit_count']==0 and row['is_exact_zero'] is False
        assert row['area_interval_mm2'][1]>0


def test_area_and_distance_equality_are_inclusive_but_remain_estimates():
    r=area.facing_area(triangle(0),triangle(.125,True),delta_mm=.125,minimum_area_mm2=.5)
    assert r['status']=='PASS' and r['edge'] is True
    assert r['facing_area_estimate_mm2']==.5
    assert r['threshold_in_sampling_interval'] is True
    assert r['facing_area_interval_mm2'][0]<.5


def test_bidirectional_area_is_minimum_not_sum():
    r=area.facing_area(triangle(0),triangle(.001,True),delta_mm=.01,minimum_area_mm2=.75)
    assert r['edge'] is False and r['facing_area_estimate_mm2']==.5
    assert r['facing_area_estimate_mm2']==min(x['area_estimate_mm2'] for x in r['directional_areas'])


def test_invalid_sampling_configuration_is_not_zero_area(monkeypatch):
    monkeypatch.setitem(area.CONTACT_AREA_CONFIG,'samples_per_direction',0)
    with pytest.raises(ValueError,match='sampling configuration'):
        area.facing_area(triangle(0),triangle(.001,True),delta_mm=.01,minimum_area_mm2=.1)


def test_true_material_union_does_not_depend_on_declared_print_groups():
    a,b=box((0,0,0)),box((.999,.999,0))
    def part(name,meshes):
        groups=[];count=0
        for m in meshes:
            groups.append([count,count+len(m.faces)]);count+=len(m.faces)
        return dict(part_id=name,mesh_mm=trimesh.util.concatenate(meshes),face_groups=groups,
                    assembly_transform_mm=np.eye(4))
    one=g.measure_connectivity([part('whole',[a,b])],delta_ratio=.005,delta_floor_mm=0.)
    two=g.measure_connectivity([part('a',[a]),part('b',[b])],delta_ratio=.005,delta_floor_mm=0.)
    assert one['status']==two['status']=='PASS'
    assert one['overall_pass'] is two['overall_pass'] is True
    assert one['material_component_count']==two['material_component_count']==1
    assert one['part_count']==1 and two['part_count']==2
    assert two['details']['nodes'][0]['part_ids']==['a','b']
    assert one['details']['pairs']==two['details']['pairs']==[]


def test_large_source_small_target_uses_local_area_weight_not_entire_face():
    large=trimesh.Trimesh([[-1000,-1000,0],[1000,-1000,0],[0,1000,0]],[[0,1,2]],process=False)
    small=triangle(.125,True)
    before=large.vertices.copy()
    r=area.facing_area(large,small,delta_mm=.125,minimum_area_mm2=.1)
    assert r['status']=='PASS' and r['edge'] is True
    assert r['directional_areas'][0]['sampling_area_mm2']<2.
    assert r['facing_area_estimate_mm2']==pytest.approx(.5,abs=.015)
    assert np.array_equal(large.vertices,before)


def test_coarse_and_nonuniformly_refined_meshes_have_same_area_distribution():
    # Refine only the left half. Equal samples per face would bias this case.
    left=rectangle(-2,0,-2,2);right=rectangle(0,2,-2,2)
    coarse=trimesh.util.concatenate([left,right])
    for _ in range(4):left=left.subdivide()
    fine=trimesh.util.concatenate([left,right])
    target=rectangle(-.75,.25,-.5,.5,.125,True)
    a=area.facing_area(coarse,target,delta_mm=.125,minimum_area_mm2=.5)
    b=area.facing_area(fine,target,delta_mm=.125,minimum_area_mm2=.5)
    assert len(fine.faces)>100*len(coarse.faces)
    assert a['edge'] is b['edge'] is True
    for row in (a,b):
        assert row['directional_areas'][0]['area_estimate_mm2']==pytest.approx(1.,abs=.02)
    assert a['facing_area_estimate_mm2']==pytest.approx(b['facing_area_estimate_mm2'],abs=.02)


def test_repeated_projection_and_swapped_directions_are_reproducible():
    a,b=box((0,0,0)),box((1.01,.3,0))
    first=area.facing_area(a,b,delta_mm=.02,minimum_area_mm2=.1)
    second=area.facing_area(a,b,delta_mm=.02,minimum_area_mm2=.1)
    swapped=area.facing_area(b,a,delta_mm=.02,minimum_area_mm2=.1)
    assert first==second
    assert first['facing_area_estimate_mm2']==swapped['facing_area_estimate_mm2']
    assert first['directional_areas']==swapped['directional_areas'][::-1]


def test_empty_possible_contact_region_is_exact_zero_without_query(monkeypatch):
    def forbidden(*args,**kwargs):raise AssertionError('no eligible surface')
    monkeypatch.setattr(trimesh.proximity,'closest_point',forbidden)
    r=area.facing_area(triangle(0),triangle(10,True),delta_mm=.01,minimum_area_mm2=.1)
    assert r['edge'] is False
    assert all(x['sample_count']==0 and x['is_exact_zero'] for x in r['directional_areas'])


def test_nearest_query_failure_is_not_zero_or_geometric_rejection(monkeypatch):
    def broken(*args,**kwargs):raise ValueError('nearest backend unavailable')
    monkeypatch.setattr(trimesh.proximity,'closest_point',broken)
    with pytest.raises(ValueError,match='backend unavailable'):
        area.facing_area(triangle(0),triangle(.01,True),delta_mm=.02,minimum_area_mm2=.1)


def test_sampling_settings_change_measurement_identity(monkeypatch):
    def measure():
        return g.measure_connectivity([dict(part_id='box',mesh_mm=box((0,0,0)),
            assembly_transform_mm=np.eye(4))],delta_ratio=.005,delta_floor_mm=0.)
    before=measure()
    monkeypatch.setitem(area.CONTACT_AREA_CONFIG,'seed',1)
    assert measure()['measurement_config_sha256']!=before['measurement_config_sha256']
