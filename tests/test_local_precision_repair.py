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
from adsl.core.export.mesh_repair import (
    face_orientation_metrics, repair_float32_mesh, require_face_orientation,
)


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
    assert len(row['operations'])==1
    operation=row['operations'][0]
    assert {key:operation[key] for key in ('operation','edge_before','edge_after',
        'zero_area_before','zero_area_after','surface_displacement_upper_bound')}==dict(
        operation='diagonal_flip',edge_before=[0,2],edge_after=[1,3],
        zero_area_before=1,zero_area_after=0,surface_displacement_upper_bound=0.)
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


def rounding_flip_pyramid():
    ulp = float(np.spacing(np.float32(1.)))
    vertices = np.array([(0,0,0),(1+.49*ulp,1+.51*ulp,0),
        (2,2+.98*ulp,0),(0,3,0),(1,1,1)],dtype=np.float64)
    faces = np.array([(0,2,1),(0,3,2),(0,1,4),(1,2,4),(2,3,4),(3,0,4)],dtype=np.uint64)
    return vertices,faces


def test_actual_closed_positive_volume_cast_can_flip_one_geometric_face():
    vertices,faces = rounding_flip_pyramid()
    rounded = vertices.astype(np.float32).astype(np.float64)
    for mesh_vertices in (vertices,rounded):
        _,metrics = validate_mesh(mesh_vertices,faces)
        assert metrics['boundary_edges']==metrics['inconsistent_edges']==0
        assert metrics['zero_area_triangles']==0 and metrics['signed_volume']>0
    orientation = face_orientation_metrics(vertices,rounded,faces)
    assert not orientation['valid'] and orientation['flipped_faces']==1
    assert orientation['failing_face_indices']==[0]
    assert orientation['minimum_normal_dot']==pytest.approx(-1.)
    with pytest.raises(MeshEvaluationError,match='TARGET_PRECISION_FACE_ORIENTATION_INVALID'):
        require_face_orientation(vertices,rounded,faces)
    original=vertices.copy(),faces.copy()
    repaired,triangles,ids,row=repair_float32_mesh(vertices,faces,np.full(len(faces),42,dtype=np.uint64),
        displacement_budget=2e-6)
    assert row['status']=='APPLIED'
    assert row['metrics_before']['valid'] and row['metrics_before']['zero_area_triangles']==0
    assert row['defect_counts_before']['flipped_faces']==1
    assert row['bad_face_count_before']==1 and row['bad_face_count_after']==0
    assert row['metrics_after']['valid'] and row['face_orientation']['valid']
    assert row['face_orientation']['triangle_count']==len(triangles)
    assert row['surface_displacement_upper_bound']<=2e-6
    assert row['operations'] and all(op['bad_face_count_after']<op['bad_face_count_before']
                                   for op in row['operations'])
    assert any(op['operation']=='diagonal_flip' for op in row['operations'])
    assert set(ids)=={42}
    # The four healthy side faces share vertices with the repaired base.
    # Their triangles and cast coordinates remain unchanged, with no flip.
    for face in faces[2:]:
        assert any(np.array_equal(face,other) for other in triangles)
    np.testing.assert_array_equal(repaired,rounded)
    np.testing.assert_array_equal(vertices,original[0])
    np.testing.assert_array_equal(faces,original[1])
    json.dumps(row,allow_nan=False)


def test_repair_search_includes_flips_outside_zero_area_neighborhood():
    raw = sliver_cube().to_mesh64()
    pyramid,pyramid_faces = rounding_flip_pyramid()
    pyramid += (10,0,0)
    vertices = np.vstack((raw.vert_properties[:,:3],pyramid))
    faces = np.vstack((raw.tri_verts,pyramid_faces+len(raw.vert_properties)))
    original = vertices.copy(),faces.copy()
    _,source = validate_mesh(vertices,faces)
    assert source['valid'] and source['material_components']==2
    rounded,triangles,_,row=repair_float32_mesh(vertices,faces,np.zeros(len(faces),dtype=np.uint64),
        displacement_budget=2e-6,expected_components=2)
    assert row['operations'] and row['metrics_after']['zero_area_triangles']==0
    assert row['metrics_after_local_edit_float64']['valid']
    assert row['face_orientation']['triangle_count']==row['metrics_after']['triangle_count']
    assert row['metrics_before']['zero_area_triangles']>0
    assert row['defect_counts_before']['flipped_faces']==1
    assert row['bad_face_count_after']==0 and row['face_orientation']['valid']
    assert row['face_orientation']['flipped_faces']==0
    assert row['surface_displacement_upper_bound']<=2e-6
    measured,after=validate_mesh(rounded,triangles,expected_components=2)
    assert after['valid'] and len(measured.decompose())==2
    np.testing.assert_array_equal(vertices,original[0])
    np.testing.assert_array_equal(faces,original[1])
    json.dumps(row,allow_nan=False)


@pytest.mark.parametrize('protection',['budget','material'])
def test_flip_search_preserves_budget_and_material_guards_with_complete_evidence(protection):
    vertices,faces=rounding_flip_pyramid()
    ids=(np.arange(len(faces),dtype=np.uint64) if protection=='material'
         else np.full(len(faces),42,dtype=np.uint64))
    original=vertices.copy(),faces.copy()
    budget=2e-6 if protection=='material' else 1e-9
    with pytest.raises(MeshEvaluationError,match='LOCAL_PRECISION_REPAIR_REJECTED') as error:
        repair_float32_mesh(vertices,faces,ids,displacement_budget=budget,mm_per_unit=.25)
    row=error.value.diagnostic['repair']
    assert row['bad_face_count_before']==row['bad_face_count_after']==1
    assert row['defect_counts_before']['flipped_faces']==row['defect_counts_after']['flipped_faces']==1
    assert not row['operations']
    assert row['rejected_candidates']
    if protection=='budget':
        assert row['rejected_candidates'].get('flip_displacement_budget',0)>0
    else:
        assert any('material' in key and count>0 for key,count in row['rejected_candidates'].items())
    initial=row['precision_defects_before']
    assert initial['index_context']=='initial_input_mesh'
    assert initial['total_bad_faces']==initial['recorded_bad_faces']==1
    assert not initial['truncated']
    assert initial['faces'][0]['triangle_index']==0
    remaining=row['remaining_bad_faces']
    assert len(remaining)==1
    assert row['remaining_bad_faces_total']==row['remaining_bad_faces_recorded']==1
    assert not row['remaining_bad_faces_truncated']
    triangle=remaining[0]
    assert triangle['triangle_index']==0 and triangle['material_id']==int(ids[0])
    assert 'GEOMETRIC_NORMAL_FLIP' in triangle['defect_kinds']
    assert triangle['normal_dot']<0
    assert len(triangle['source_normal'])==len(triangle['target_normal'])==3
    for stage in ('source','target'):
        assert triangle['minimum_height_mm'][stage]==pytest.approx(
            triangle['minimum_height_scene_units'][stage]*.25)
    ulps=np.asarray(triangle['float32_ulp_scene_units'])
    assert ulps.shape==(3,3) and (ulps>0).all()
    np.testing.assert_array_equal(triangle['float32_ulp_mm'],ulps*.25)
    assert len(triangle['edited_vertices_local_float64'])==len(triangle['target_vertices_local'])==3
    ring=triangle['one_ring']
    assert set(ring['triangle_indices'])==set(range(len(faces)))
    assert len(ring['vertex_indices'])==len(ring['edited_vertices_local_float64'])==len(ring['target_vertices_local'])
    assert len(ring['faces'])==len(ring['triangle_indices'])
    for neighbor in ring['faces']:
        assert neighbor['material_id']==int(ids[neighbor['triangle_index']])
        assert len(neighbor['source_normal'])==len(neighbor['target_normal'])==3
        assert neighbor['normal_dot'] is not None
    json.dumps(error.value.diagnostic,allow_nan=False)
    np.testing.assert_array_equal(vertices,original[0])
    np.testing.assert_array_equal(faces,original[1])


def test_flip_failure_evidence_marks_truncation_and_keeps_complete_bad_face_counts():
    pyramid,base_faces=rounding_flip_pyramid()
    vertices=np.vstack([pyramid+(0,0,3*i) for i in range(10)])
    faces=np.vstack([base_faces+len(pyramid)*i for i in range(10)])
    with pytest.raises(MeshEvaluationError,match='LOCAL_PRECISION_REPAIR_REJECTED') as error:
        repair_float32_mesh(vertices,faces,np.full(len(faces),42,dtype=np.uint64),
            displacement_budget=1e-9,expected_components=10)
    row=error.value.diagnostic['repair']
    assert row['bad_face_count_before']==row['bad_face_count_after']==10
    for evidence in (row['precision_defects_before'],dict(
            total_bad_faces=row['remaining_bad_faces_total'],
            recorded_bad_faces=row['remaining_bad_faces_recorded'],
            truncated=row['remaining_bad_faces_truncated'],faces=row['remaining_bad_faces'])):
        assert evidence['total_bad_faces']==10 and evidence['recorded_bad_faces']==8
        assert evidence['truncated'] and len(evidence['faces'])==8
        assert all(len(face['one_ring']['faces'])==6 for face in evidence['faces'])
    json.dumps(error.value.diagnostic,allow_nan=False)


def test_orientation_normalization_avoids_area_squared_underflow():
    vertices = np.array([(0,0,0),(1e-170,0,0),(0,1e-170,0)],dtype=np.float64)
    faces = np.array([(0,1,2)])
    metrics = face_orientation_metrics(vertices,vertices,faces)
    assert metrics['valid'] and metrics['minimum_normal_dot']==1.
    assert metrics['source_zero_normals']==metrics['target_zero_normals']==0


def test_orientation_reports_collapse_and_nonfinite_as_uncomparable():
    vertices = np.array([(0,0,0),(1,0,0),(0,1,0)],dtype=np.float64)
    faces = np.array([(0,1,2)])
    target = vertices.copy();target[2]=target[1]
    collapsed = face_orientation_metrics(vertices,target,faces)
    assert collapsed['target_zero_normals']==collapsed['uncomparable_faces']==1
    assert not collapsed['valid']
    target[2,0]=np.nan
    nonfinite = face_orientation_metrics(vertices,target,faces)
    assert nonfinite['nonfinite_target_normals']==nonfinite['uncomparable_faces']==1
    assert not nonfinite['valid']
