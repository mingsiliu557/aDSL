"""Real Mesh64 validity and output-precision checks, without Blender or models."""
import json

import manifold3d as mf
import numpy as np
import pytest
import trimesh

from adsl.core.export.mesh_validity import (
    MeshEvaluationError, checked_solid, mesh_metrics, target_mesh,
    validate_mesh, world_vertices,
)


def arrays(solid):
    raw = solid.to_mesh64()
    return np.asarray(raw.vert_properties[:, :3]), np.asarray(raw.tri_verts)


def test_closed_cube_metrics_and_explicit_self_intersection_scope():
    v, f = arrays(mf.Manifold.cube((2, 2, 2)))
    solid, row = validate_mesh(v, f)
    assert solid.volume() == pytest.approx(8)
    assert row['valid'] and row['material_components'] == 1
    for key in ('zero_area_triangles', 'duplicate_faces', 'boundary_edges',
                'nonmanifold_edges', 'nonmanifold_vertices', 'inconsistent_edges'):
        assert row[key] == 0
    assert row['self_intersection'] == 'NOT_EVALUATED'
    json.dumps(row)


def test_large_translation_keeps_analytic_volume_exact_world_triangles_and_materials():
    import hashlib
    base = mf.Manifold.cube((2,2,2),center=True)
    source = ((base+base.translate((1+1e-5,0,0)))-
        mf.Manifold.cube((1,4,4),center=True).translate((-.5,0,0))) ^ (
        mf.Manifold.cube((4,1,4),center=True).translate((.5,0,0)))
    v,f = arrays(source)
    face_ids=np.full(len(f),42,dtype=np.uint64)
    def geometry_hash(vertices,faces):
        triangles=[]
        for triangle in np.asarray(vertices)[faces]:
            triangle=triangle[np.lexsort((triangle[:,2],triangle[:,1],triangle[:,0]))]
            triangles.append(triangle.reshape(-1))
        values=np.asarray(triangles,dtype=np.float64)
        values=values[np.lexsort(tuple(values[:,i] for i in reversed(range(9))))]
        return hashlib.sha256(np.ascontiguousarray(values).tobytes()).hexdigest()
    measured=[]
    for offset in [(0.,0,0),(1e7,2e7,-3e7)]:
        world=v+offset
        solid,metrics=validate_mesh(world,f,face_ids=face_ids)
        assert solid.volume()==pytest.approx(4.00002,abs=2e-8,rel=0)
        before=solid.volume()
        raw=solid.to_mesh64()
        assert solid.volume()==before
        assert geometry_hash(raw.vert_properties[:,:3],raw.tri_verts)==geometry_hash(world,f)
        assert set(raw.face_id)=={42}
        assert metrics['solid_construction_frame']=='bbox_center_local_float64'
        assert np.allclose(metrics['solid_construction_origin'],(world.min(axis=0)+world.max(axis=0))/2,
                           rtol=0,atol=1e-9)
        measured.append(solid.volume())
    assert measured[1]==pytest.approx(measured[0],abs=2e-8,rel=0)


@pytest.mark.parametrize('defect', ['missing_face', 'duplicate_face', 'winding', 'zero_area', 'nan', 'bad_index'])
def test_invalid_geometry_is_rejected_without_mutating_input(defect):
    v, f = arrays(mf.Manifold.cube((2, 2, 2)))
    v, f = v.copy(), f.copy()
    if defect == 'missing_face':
        f = f[1:]
    elif defect == 'duplicate_face':
        f = np.vstack((f, f[:1]))
    elif defect == 'winding':
        f[0] = f[0, ::-1]
    elif defect == 'zero_area':
        f[0, 1] = f[0, 0]
    elif defect == 'nan':
        v[0, 0] = np.nan
    else:
        f[0, 0] = len(v)
    before_v, before_f = v.copy(), f.copy()
    with pytest.raises(MeshEvaluationError, match='INPUT_GEOMETRY_INVALID') as error:
        validate_mesh(v, f, node_path='root/operand0')
    assert error.value.diagnostic['node_path'] == 'root/operand0'
    assert error.value.diagnostic['failure_kind'] == 'input_geometry'
    np.testing.assert_array_equal(v, before_v)
    np.testing.assert_array_equal(f, before_f)


def test_pinched_vertex_is_not_accepted_by_edge_incidence_alone():
    one = trimesh.creation.icosphere(subdivisions=0)
    # Share a single coordinate while the two triangle links remain disconnected.
    two = one.copy()
    two.vertices = -two.vertices + 2*one.vertices[0]
    mesh = trimesh.util.concatenate((one, two))
    row = mesh_metrics(mesh.vertices, mesh.faces)
    assert row['boundary_edges'] == row['nonmanifold_edges'] == 0
    assert row['nonmanifold_vertices'] >= 1 and not row['valid']


def test_signed_inner_cavity_is_preserved_without_shell_union():
    hollow = mf.Manifold.cube((4,4,4), center=True) - mf.Manifold.cube((2,2,2), center=True)
    v, f = arrays(hollow)
    solid, row = validate_mesh(v, f)
    assert solid.volume() == pytest.approx(56)
    assert row['shell_components'] == 2 and row['material_components'] == 1
    mesh, frame, record = target_mesh(solid)
    rebuilt, _ = validate_mesh(mesh.vertices, mesh.faces)
    assert rebuilt.volume() == pytest.approx(56)
    assert record['metrics']['shell_components'] == 2
    assert frame.shape == (4, 4)


def test_empty_boolean_can_continue_but_required_output_rejects():
    empty = mf.Manifold()
    assert checked_solid(empty, allow_empty=True) is empty
    with pytest.raises(MeshEvaluationError, match='EMPTY_REQUIRED_GEOMETRY'):
        checked_solid(empty)
    solid, row = validate_mesh(np.empty((0,3)), np.empty((0,3), dtype=np.uint64), allow_empty=True)
    assert solid.is_empty() and row['valid']


@pytest.mark.parametrize('offset', [(0,0,0), (100, 350, 700), (1e8, -2e8, 3e8)])
def test_conversion_uses_local_coordinates_and_translation_independent_budget(offset):
    cube = mf.Manifold.cube((2,4,6)).translate(offset)
    mesh, frame, row = target_mesh(cube, mm_per_unit=2)
    np.testing.assert_allclose(mesh.extents, (2,4,6))
    np.testing.assert_allclose(world_vertices(mesh.vertices, frame).min(axis=0), offset)
    assert row['displacement_budget_mm'] == pytest.approx(16*np.finfo(np.float32).eps*3*2)
    assert row['metrics']['valid'] and row['file_validation'] == 'NOT_EVALUATED'
    assert np.max(np.abs(mesh.vertices)) == 3


@pytest.mark.parametrize('dimensions_mm', [(0.4,0.6,0.8), (2.,4.,6.)])
def test_precision_budget_has_same_millimeter_floor_for_different_scene_units(dimensions_mm):
    records = []
    for mm_per_unit in (0.001,1.,1000.):
        dimensions = np.asarray(dimensions_mm)/mm_per_unit
        source = mf.Manifold.cube(dimensions,center=True)
        mesh,_,row = target_mesh(source,mm_per_unit=mm_per_unit)
        radius_mm = max(dimensions_mm)/2
        expected = 16*np.finfo(np.float32).eps*max(1.,radius_mm)
        assert row['absolute_scale_floor_mm']==1.
        assert row['local_scale_mm']==pytest.approx(radius_mm)
        assert row['displacement_budget_mm']==pytest.approx(expected,rel=1e-14)
        assert row['face_orientation']['valid']
        assert row['face_orientation']['triangle_count']==len(mesh.faces)
        np.testing.assert_allclose(mesh.extents*mm_per_unit,dimensions_mm,rtol=2e-7)
        records.append(row)
    assert records[0]['displacement_budget_mm']==pytest.approx(records[-1]['displacement_budget_mm'],rel=1e-14)
    assert records[0]['volume_budget_mm3']==pytest.approx(records[-1]['volume_budget_mm3'],rel=1e-14)


def test_target_conversion_rejects_direct_cast_geometric_flip_before_bounded_retry():
    ulp = float(np.spacing(np.float32(1.)))
    vertices = np.array([(0,0,2),(1+.49*ulp,1+.51*ulp,2),
        (2,2+.98*ulp,2),(0,3,2),(1,1,3)],dtype=np.float64)
    faces = np.array([(0,2,1),(0,3,2),(0,1,4),(1,2,4),(2,3,4),(3,0,4)],dtype=np.uint64)
    # Two separate mirrored pieces keep the aggregate bbox center at zero,
    # so the real target_mesh local cast retains this rounding counterexample.
    vertices = np.vstack((vertices,-vertices))
    faces = np.vstack((faces,faces[:,::-1]+5))
    solid,source = validate_mesh(vertices,faces,face_ids=np.arange(len(faces),dtype=np.uint64))
    _,cast = validate_mesh(vertices.astype(np.float32).astype(np.float64),faces)
    assert source['valid'] and cast['valid']
    mesh,_,row = target_mesh(solid)
    rejected = row['attempts'][0]['diagnostic']
    assert rejected['code']=='TARGET_PRECISION_FACE_ORIENTATION_INVALID'
    assert rejected['face_orientation']['flipped_faces']==2
    assert row['simplify_tolerance_scene_units']>0
    assert row['face_orientation']['valid']
    assert row['face_orientation']['triangle_count']==len(mesh.faces)


def test_real_narrow_gap_does_not_get_welded_or_component_selected():
    a = mf.Manifold.cube((1,1,1))
    b = mf.Manifold.cube((1,1,1)).translate((1.0002,0,0))
    solid = a+b
    mesh, _, row = target_mesh(solid, mm_per_unit=1000)
    measured, _ = validate_mesh(mesh.vertices, mesh.faces)
    assert len(measured.decompose()) == 2
    assert row['material_components'] == 2
    assert row['simplify_tolerance_scene_units'] == 0
    components = sorted(measured.decompose(), key=lambda s: s.bounding_box()[0])
    gap_mm = (components[1].bounding_box()[0]-components[0].bounding_box()[3])*1000
    assert gap_mm == pytest.approx(.2, abs=2e-4)


def test_sub_float32_gap_is_not_silently_filled():
    solid = mf.Manifold.cube((1,1,1)) + mf.Manifold.cube((2,1,1)).translate((1+1e-9,0,0))
    checked_solid(solid)
    with pytest.raises(MeshEvaluationError, match='TARGET_PRECISION_UNREPRESENTABLE') as error:
        target_mesh(solid, node_path='gap')
    row = error.value.diagnostic
    assert row['stage'] == 'target_precision' and len(row['attempts']) == 3
    assert row['internal_metrics']['valid']
    json.dumps(row)


def test_triangle_material_provenance_survives_precision_boundary():
    v, f = arrays(mf.Manifold.cube((2,2,2)))
    ids = np.arange(len(f), dtype=np.uint64)
    solid, _ = validate_mesh(v, f, face_ids=ids)
    mesh, _, row = target_mesh(solid)
    assert len(mesh.face_attributes['source_face_id']) == len(mesh.faces)
    assert set(mesh.face_attributes['source_face_id']) == set(ids)
    assert row['simplify_tolerance_scene_units'] == 0


def test_noninteger_indices_are_rejected_before_casting():
    v, f = arrays(mf.Manifold.cube((1,1,1)))
    f = f.astype(float)
    f[0,0] += .25
    with pytest.raises(MeshEvaluationError, match='INPUT_GEOMETRY_INVALID'):
        validate_mesh(v, f)


def test_mesh64_input_and_float64_world_transform_are_supported():
    raw = mf.Manifold.cube((1,2,3)).to_mesh64()
    mesh, frame, _ = target_mesh(raw)
    transform = np.eye(4, dtype=np.float64)
    transform[:3,3] = (1e8+.12345678, -2e8+.98765432, 3e8+.12345678)
    result = world_vertices(mesh.vertices, transform @ frame)
    np.testing.assert_array_equal(result.min(axis=0), transform[:3,3])


@pytest.mark.parametrize('vertices,faces', [(1, []), ([[]], []), ('bad', [])])
def test_malformed_arrays_have_structured_diagnostics(vertices, faces):
    with pytest.raises(MeshEvaluationError, match='INPUT_GEOMETRY_INVALID') as error:
        validate_mesh(vertices, faces)
    json.dumps(error.value.diagnostic, allow_nan=False)


def test_actual_print_readback_is_separate_and_missing_file_is_not_geometry(tmp_path):
    from adsl.core.export.mesh_validity import validate_written_mesh
    mesh, _, _ = target_mesh(mf.Manifold.cube((1,2,3)))
    path = tmp_path/'cube.stl'
    mesh.export(path)
    read, solid, row = validate_written_mesh(path)
    assert row['stage'] == 'file_readback' and solid.volume() == pytest.approx(6)
    assert len(read.faces) == len(mesh.faces)
    with pytest.raises(MeshEvaluationError, match='MESH_FILE_READ_FAILED') as error:
        validate_written_mesh(tmp_path/'missing.stl')
    assert error.value.diagnostic['failure_kind'] == 'file'
    bad = mesh.copy()
    bad.faces = bad.faces[1:]
    bad.export(tmp_path/'open.stl')
    with pytest.raises(MeshEvaluationError, match='WRITTEN_MESH_INVALID'):
        validate_written_mesh(tmp_path/'open.stl')


def test_real_connector_clearance_survives_target_conversion():
    from adsl.core.assembly import TabSlot
    from adsl.core.assembly_topology import query_solids
    nominal_tab, nominal_slot = query_solids(TabSlot(12,6,6,7,.2))
    tab, _, _ = target_mesh(nominal_tab)
    slot, _, _ = target_mesh(nominal_slot)
    # Re-centering frames are restored before comparing the actual dimensions.
    np.testing.assert_allclose(slot.extents[:2]-tab.extents[:2], (.4,.4), atol=1e-6)
    assert not np.array_equal(slot.extents, tab.extents)


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_FIXED_REAL') != '1', reason='explicit native Blender input repair validation')
@pytest.mark.parametrize('kind', ['planar', 'microcrack', 'missing_face', 'real_gap'])
def test_native_restricted_input_repairs_use_shared_validator_and_rollback(kind):
    import importlib
    bpy = pytest.importorskip('bpy')
    from adsl.core.export.mesh_validity import normalize_blender_input
    zero = importlib.import_module('test_zero_area_tessellation')
    cracks = importlib.import_module('test_microcrack_welding')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = zero.pyramid(open_mesh=kind == 'missing_face') if kind in ('planar','missing_face') else cracks.cracked_cube(
        origin=.5, gap=.001 if kind == 'real_gap' else 2**-24)
    original = obj.data
    positions = np.asarray([tuple(v.co) for v in original.vertices])
    mesh_count = len(bpy.data.meshes)
    if kind in ('missing_face','real_gap'):
        with pytest.raises(MeshEvaluationError, match='INPUT_GEOMETRY_INVALID'):
            normalize_blender_input(obj, node_path='input')
        assert obj.data is original and len(bpy.data.meshes) == mesh_count
        np.testing.assert_array_equal(positions, [tuple(v.co) for v in original.vertices])
    else:
        row = normalize_blender_input(obj, node_path='input')
        assert row['status'] == 'APPLIED' and row['metrics_after']['valid']
        assert row['maximum_displacement_mm'] <= row['displacement_budget_mm']
        if kind == 'planar':
            assert row['metrics_before']['zero_area_triangles'] == 1
            np.testing.assert_array_equal(positions, [tuple(v.co) for v in obj.data.vertices])
        else:
            assert row['metrics_before']['boundary_edges'] == 3
        assert len(bpy.data.meshes) == mesh_count
    bpy.ops.wm.read_factory_settings(use_empty=True)


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_FIXED_REAL') != '1', reason='explicit native Blender input repair validation')
@pytest.mark.parametrize('mm_per_unit', [0.001,1.,1000.])
def test_native_input_repair_budget_floor_is_millimeters(mm_per_unit):
    import importlib
    bpy = pytest.importorskip('bpy')
    from adsl.core.export.mesh_validity import normalize_blender_input
    bpy.ops.wm.read_factory_settings(use_empty=True)
    obj = importlib.import_module('test_zero_area_tessellation').pyramid()
    matrix = np.eye(4)
    matrix[:3,:3] *= 0.2/mm_per_unit
    matrix[:3,3] = (1e6,-2e6,3e6)
    obj.matrix_world = matrix.tolist()
    row = normalize_blender_input(obj,mm_per_unit=mm_per_unit)
    assert row['status']=='APPLIED' and row['metrics_after']['valid']
    assert row['displacement_budget_mm']==pytest.approx(16*np.finfo(np.float32).eps,rel=1e-14)
    bpy.ops.wm.read_factory_settings(use_empty=True)


def test_uniform_material_reset_allows_bounded_collapse_of_artificial_csg_boundaries():
    def box(offset):
        raw = mf.Manifold.cube((1,1,1)).to_mesh64()
        solid, _ = validate_mesh(raw.vert_properties[:,:3], raw.tri_verts,
            face_ids=np.full(len(raw.tri_verts),42,dtype=np.uint64))
        return solid.translate(offset)
    solid = box((0,0,0)) + box((.5+1e-9,1e-9,0))
    mesh, _, row = target_mesh(solid)
    assert row['local_precision_repair'] or row['uniform_material_ancestry_reset']
    assert row['simplify_tolerance_scene_units'] in (0., np.spacing(np.float32(1.0)))
    assert set(mesh.face_attributes['source_face_id']) == {42}
    assert row['surface_displacement_upper_bound_mm'] <= row['displacement_budget_mm']
    assert row['metrics']['valid']
