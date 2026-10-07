"""Actual local float32 repairs and topology/material/gap safeguards."""
import json
import os
from pathlib import Path

import manifold3d as mf
import numpy as np
import pytest

from adsl.core.export.mesh_validity import (
    MeshEvaluationError, mesh_metrics, target_mesh, validate_mesh,
)
from adsl.core.export.local_precision_repair import repair_float32_mesh


def uniform_solid(solid, material=0):
    raw = solid.to_mesh64()
    result,_ = validate_mesh(raw.vert_properties[:,:3],raw.tri_verts,
        face_ids=np.full(len(raw.tri_verts),material,dtype=np.uint64))
    return result


def sliver_cube():
    return uniform_solid(mf.Manifold.cube((1,1,1))) + uniform_solid(
        mf.Manifold.cube((1,1,1)).translate((.5+1e-9,1e-9,0)))


def test_precision_repair_on_valid_source_keeps_original_unmodified():
    solid = sliver_cube()
    raw = solid.to_mesh64()
    v,f = np.asarray(raw.vert_properties[:,:3]),np.asarray(raw.tri_verts)
    center = (v.min(axis=0)+v.max(axis=0))/2
    local = v-center
    original_v,original_f = local.copy(),f.copy()
    rounded,faces,ids,row = repair_float32_mesh(local,f,raw.face_id,displacement_budget=2e-6,
        expected_components=1)
    assert row['status'] == 'APPLIED' and row['operations']
    assert row['metrics_before']['zero_area_triangles'] > 0
    assert row['metrics_after']['valid']
    assert row['surface_displacement_upper_bound'] <= 2e-6
    assert set(ids) == {0}
    np.testing.assert_array_equal(original_v,local)
    np.testing.assert_array_equal(original_f,f)
    assert mesh_metrics(rounded,faces)['valid']


def test_tetrahedron_edge_fails_full_link_condition_and_rolls_back():
    vertices = np.array([(1,0,0),(1+1e-9,0,0),(0,1,0),(0,0,1)],dtype=np.float64)
    faces = np.array([(0,2,1),(0,1,3),(0,3,2),(1,2,3)],dtype=np.uint64)
    _,metrics = validate_mesh(vertices,faces)
    assert metrics['valid']
    before = vertices.copy(),faces.copy()
    with pytest.raises(MeshEvaluationError, match='LOCAL_PRECISION_REPAIR_REJECTED') as error:
        repair_float32_mesh(vertices,faces,np.zeros(4,dtype=np.uint64),displacement_budget=1e-6)
    report = error.value.diagnostic['repair']
    assert report['rejected_candidates']['link_condition'] > 0
    assert report['operations'] == []
    np.testing.assert_array_equal(vertices,before[0])
    np.testing.assert_array_equal(faces,before[1])


def test_material_boundaries_are_not_contracted_for_precision():
    solid = sliver_cube()
    raw = solid.to_mesh64()
    vertices = raw.vert_properties[:,:3]
    vertices = vertices-(vertices.min(axis=0)+vertices.max(axis=0))/2
    ids = np.arange(len(raw.tri_verts),dtype=np.uint64)
    with pytest.raises(MeshEvaluationError, match='LOCAL_PRECISION_REPAIR_REJECTED') as error:
        repair_float32_mesh(vertices,raw.tri_verts,ids,displacement_budget=2e-6)
    assert error.value.diagnostic['repair']['rejected_candidates']['material_boundary'] > 0
    assert not error.value.diagnostic['repair']['operations']


def test_sub_float32_real_gap_is_not_cross_shell_welded():
    separated = uniform_solid(mf.Manifold.cube((1,1,1))) + uniform_solid(
        mf.Manifold.cube((2,1,1)).translate((1+1e-9,0,0)))
    with pytest.raises(MeshEvaluationError, match='TARGET_PRECISION_UNREPRESENTABLE') as error:
        target_mesh(separated)
    assert len(error.value.diagnostic['attempts']) == 3
    for attempt in error.value.diagnostic['attempts']:
        nested = attempt['diagnostic'].get('repair',{})
        assert not nested.get('operations')


def test_real_point_two_mm_interface_gap_is_preserved():
    two = uniform_solid(mf.Manifold.cube((1,1,1))) + uniform_solid(
        mf.Manifold.cube((1,1,1)).translate((1.0002,0,0)))
    mesh,_,row = target_mesh(two,mm_per_unit=1000)
    measured,_ = validate_mesh(mesh.vertices,mesh.faces)
    positive = sorted(measured.decompose(),key=lambda s:s.bounding_box()[0])
    assert len(positive) == 2
    assert (positive[1].bounding_box()[0]-positive[0].bounding_box()[3])*1000 == pytest.approx(.2,abs=2e-4)
    assert row['local_precision_repair'] is None


def test_repaired_shell_preserves_signed_cavity_and_component_count():
    outer = sliver_cube().scale((4,4,4))
    cavity = mf.Manifold.cube((.5,.5,.5)).translate((.2,.2,.2))
    hollow = outer-cavity
    source = hollow.to_mesh64()
    volume = hollow.volume()
    mesh,_,row = target_mesh(hollow)
    measured,_ = validate_mesh(mesh.vertices,mesh.faces)
    assert row['metrics']['shell_components'] == 2
    assert row['metrics']['material_components'] == 1
    assert measured.volume() == pytest.approx(volume,abs=row['volume_budget_mm3'])
    assert any(p.volume()<0 for p in measured.decompose())
    assert len(source.tri_verts)>0


def test_unchanged_direct_cast_reports_actual_nonzero_displacement():
    source = uniform_solid(mf.Manifold.cube((1.123456789,1,1)))
    raw = source.to_mesh64()
    rounded,_,_,row = repair_float32_mesh(raw.vert_properties[:,:3],raw.tri_verts,raw.face_id,
        displacement_budget=1e-6)
    assert row['status']=='UNCHANGED'
    displacement = np.linalg.norm(rounded-raw.vert_properties[:,:3],axis=1).max()
    assert row['surface_displacement_upper_bound'] == pytest.approx(displacement)
    assert displacement>0


@pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL')!='1',reason='explicit native archived operand evaluation')
def test_archived_three_original_operands_local_repair():
    from adsl.core import Asset, boolean_union
    from adsl.core.export.mesh64 import evaluate_shape
    path = Path(__file__).parents[1]/'reports/general_mesh_robustness_v1_20261007/minimal_operands.json'
    specs = json.loads(path.read_text())
    operands=[]
    for spec in specs:
        shape=Asset(spec['label'])
        for primitive in spec['primitives']:
            shape.add_primitive(primitive)
        operands.append(shape)
    piece=evaluate_shape(boolean_union(*operands)).pieces[0]
    mesh,_,row=target_mesh(piece.solid)
    assert row['metrics']['valid']
    assert row['metrics']['material_components']==2
    assert row['local_precision_repair']['status']=='APPLIED'
    assert row['surface_displacement_upper_bound_mm']<=row['displacement_budget_mm']
    assert mesh_metrics(mesh.vertices,mesh.faces)['zero_area_triangles']==0


def test_float32_collinear_triangle_uses_local_planar_diagonal_flip():
    vertices = np.array([(0,0,0),(1,-1e-9,0),(2,0,0),(1,1,0),(1,.5,1)],dtype=np.float64)
    faces = np.array([(0,2,1),(0,3,2),(0,1,4),(1,2,4),(2,3,4),(3,0,4)],dtype=np.uint64)
    vertices -= (vertices.min(axis=0)+vertices.max(axis=0))/2
    original = vertices.copy()
    _,before = validate_mesh(vertices,faces)
    assert before['valid'] and np.all(np.linalg.norm(vertices[faces[:,1]]-vertices[faces[:,0]],axis=1)>1e-6)
    rounded,new_faces,_,row = repair_float32_mesh(vertices,faces,np.zeros(len(faces),dtype=np.uint64),
        displacement_budget=2e-6,expected_components=1)
    assert row['metrics_before']['zero_area_triangles']==1
    assert row['metrics_after']['valid']
    assert row['operations']==[dict(operation='diagonal_flip',edge_before=[0,2],edge_after=[1,3],
        zero_area_before=1,zero_area_after=0,surface_displacement_upper_bound=0.)]
    assert len(new_faces)==len(faces)
    np.testing.assert_array_equal(original,vertices)
    rebuilt,_ = validate_mesh(rounded,new_faces)
    assert rebuilt.volume()==pytest.approx((1+1e-9)/3,abs=1e-8)


def test_display_repair_budget_is_unchanged_by_rigid_global_translation():
    source = sliver_cube()
    first,_,record = target_mesh(source)
    moved,_,other = target_mesh(source.translate((1000.,2000.,3000.)))
    assert record['displacement_budget_mm']==pytest.approx(other['displacement_budget_mm'])
    assert first.extents==pytest.approx(moved.extents,abs=1e-6)
    assert record['metrics']['valid'] and other['metrics']['valid']
