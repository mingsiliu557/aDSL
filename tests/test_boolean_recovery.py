"""Native Boolean recovery and exported completeness, without model calls."""
import importlib
import json
import os
from pathlib import Path
import runpy

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL') != '1',
                                reason='explicit native Blender geometry validation')
g = importlib.import_module('adsl.core.export.export_glb')


@pytest.fixture(autouse=True)
def blender():
    bpy = pytest.importorskip('bpy')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    yield bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)


def corrupt_without_zero_area(base, other, operation):
    import bpy
    import bmesh
    bm = bmesh.new()
    bm.from_mesh(base.data)
    bm.faces.ensure_lookup_table()
    bmesh.ops.delete(bm, geom=[bm.faces[0]], context='FACES')
    bm.to_mesh(base.data)
    bm.free()
    bpy.data.objects.remove(other, do_unlink=True)


@pytest.mark.parametrize('operation,volume', [('UNION', 12), ('DIFFERENCE', 4), ('INTERSECT', 4)])
def test_failed_boolean_recomputes_same_operation_and_preserves_inputs(monkeypatch, operation, volume):
    from adsl.core import Cube
    a, = g._build_shape(Cube((2, 2, 2), center=(3, 4, 5), color=(1, 0, 0)))
    b, = g._build_shape(Cube((2, 2, 2), center=(4, 4, 5), color=(0, 0, 1)))
    original_b = b.data
    old_a = a.data
    monkeypatch.setattr(g, '_apply_boolean', corrupt_without_zero_area)
    g._apply_boolean_group(a, [b], operation)
    assert not g._mesh_defects(a.data)
    v, f, _ = g._mesh_triangles(a.data, a.matrix_world)
    import trimesh
    assert trimesh.Trimesh(v, f, process=False).volume == pytest.approx(volume)
    assert len(a.data.materials) == 2
    assert b.data is original_b
    assert a.data is not old_a
    report = json.loads(a['adsl_boolean_recovery'])
    assert report['operation'] == operation
    assert report['max_vertex_to_surface_sample_distance_scene_units'] <= report['displacement_bound_scene_units']


def test_real_missing_face_not_filled_and_failure_does_not_mutate_inputs(monkeypatch):
    from adsl.core import Cube
    a, = g._build_shape(Cube((2, 2, 2)))
    b, = g._build_shape(Cube((2, 2, 2), center=(1, 0, 0)))
    dummy = g._duplicate_object(b)
    corrupt_without_zero_area(a, dummy, 'UNION')
    before = a.data
    before_b = b.data
    v, f, _ = g._mesh_triangles(a.data, a.matrix_world)
    monkeypatch.setattr(g, '_apply_boolean', corrupt_without_zero_area)
    with pytest.raises(ValueError, match='INVALID_OPERAND'):
        g._apply_boolean_group(a, [b], 'UNION')
    assert a.data is before and b.data is before_b
    after_v, after_f, _ = g._mesh_triangles(a.data, a.matrix_world)
    np.testing.assert_array_equal(v, after_v)
    np.testing.assert_array_equal(f, after_f)


def test_valid_boolean_does_not_load_recovery(monkeypatch):
    from adsl.core import Cube, boolean_union
    def unexpected(*args):
        pytest.fail('valid Boolean must not invoke recovery')
    monkeypatch.setattr(g, '_recover_boolean', unexpected)
    obj, = g._build_shape(boolean_union(Cube((2, 2, 2)), Cube((2, 2, 2), center=(1, 0, 0))))
    assert not g._mesh_defects(obj.data)


def test_export_rejects_silently_omitted_mesh(tmp_path, monkeypatch):
    from adsl.core import Cube
    good = tmp_path/'good.glb'
    g.export_glb(Cube((1, 1, 1)), good)
    original = g._verify_glb_meshes
    def omitted(path, expected):
        original(path, {**expected, 'silently_omitted_trunk': 42})
    monkeypatch.setattr(g, '_verify_glb_meshes', omitted)
    with pytest.raises(ValueError, match='GLB_EXPORT_INCOMPLETE.*silently_omitted_trunk'):
        g.export_glb(Cube((1, 1, 1)), tmp_path/'bad.glb')


def test_original_elephant_trunk_local_precision_repair_and_manufacturing(tmp_path, blender):
    """Fixed original source survives actual GLB and independent ASCII STL."""
    import trimesh
    from adsl.core.assembly_topology import read_print_mesh, union_print_mesh
    from adsl.core.export.mesh64 import evaluate_shape
    from adsl.core.export.mesh_validity import mesh_metrics
    source = Path(__file__).parent/'fixtures/elephant_trunk.py'
    scene = runpy.run_path(str(source))['scene']
    result = evaluate_shape(scene)
    mesh = result.pieces[0].world_mesh()
    assert mesh_metrics(mesh.vertices, mesh.faces)['valid']
    path = tmp_path/'trunk.glb'
    g.export_glb(scene, path)
    assert path.is_file()
    saved = trimesh.load(path, force='scene', process=False)
    actual = trimesh.util.concatenate(tuple(saved.geometry.values()))
    assert mesh_metrics(actual.vertices, actual.faces)['valid']
    stl = tmp_path/'trunk_mesh64_diagnostic.stl'
    mesh.export(stl, file_type='stl_ascii')
    solid, components, _ = union_print_mesh(read_print_mesh(stl, np.eye(4)))
    assert len(components) == 1 and solid.volume() > 0
    # Manufacturing retains the original Mesh64 surface and has its own
    # independently verified display; repairs never become checker geometry.
    from adsl.core.export.export_assembly import evaluated
    canonical, manufactured, diagnostics = evaluated(scene, tmp_path/'assembly_input.glb', 1.)
    assert diagnostics['manufacturing_status'] == 'PASS'
    assert diagnostics['display_status'] == 'PASS'
    assert manufactured.volume() == pytest.approx(result.pieces[0].solid.volume(), abs=1e-12)
    assert mesh_metrics(canonical.vertices, canonical.faces)['valid']
