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


def test_uniform_material_reset_allows_bounded_collapse_of_artificial_csg_boundaries():
    def box(offset):
        raw = mf.Manifold.cube((1,1,1)).to_mesh64()
        solid, _ = validate_mesh(raw.vert_properties[:,:3], raw.tri_verts,
            face_ids=np.full(len(raw.tri_verts),42,dtype=np.uint64))
        return solid.translate(offset)
    solid = box((0,0,0)) + box((.5+1e-9,1e-9,0))
    mesh, _, row = target_mesh(solid)
    assert row['uniform_material_ancestry_reset']
    assert row['simplify_tolerance_scene_units'] == np.spacing(np.float32(1.0))
    assert set(mesh.face_attributes['source_face_id']) == {42}
    assert row['surface_displacement_upper_bound_mm'] <= row['displacement_budget_mm']
    assert row['metrics']['valid']
