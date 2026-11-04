"""Native Blender parity for batched shading; no rendering or simulation."""
import pytest

bpy = pytest.importorskip('bpy')
from adsl.tools.render import _mesh_objects, _smooth_scene


def test_batch_smoothing_matches_per_object_operator():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    for i in range(4):
        bpy.ops.mesh.primitive_cube_add(location=(i * 3, 0, 0))
    # Include shared mesh data and a non-mesh object.
    shared = bpy.data.objects.new('shared', _mesh_objects()[0].data)
    bpy.context.collection.objects.link(shared)
    bpy.ops.object.camera_add()
    meshes = _mesh_objects()
    def snapshot():
        return [(list(o.matrix_world), [tuple(v.co) for v in o.data.vertices],
                 [p.use_smooth for p in o.data.polygons],
                 [tuple(n.vector) for n in o.data.corner_normals]) for o in meshes]
    for obj in meshes:
        bpy.ops.object.select_all(action='DESELECT')
        obj.select_set(True)
        bpy.context.view_layer.objects.active = obj
        bpy.ops.object.shade_smooth()
    bpy.ops.object.select_all(action='DESELECT')
    expected = snapshot()
    for obj in meshes:
        for poly in obj.data.polygons:
            poly.use_smooth = False
    _smooth_scene()
    assert snapshot() == expected
    assert not bpy.context.selected_objects
    assert bpy.context.view_layer.objects.active == meshes[-1]


def test_empty_scene_smoothing():
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    _smooth_scene()
    assert not _mesh_objects()
