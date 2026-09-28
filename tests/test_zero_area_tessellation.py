"""Exact-zero tessellation normalization; native Blender, no model API or renderer."""
import importlib
import os

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL') != '1',
                                reason='explicit native Blender geometry validation')
exporter = importlib.import_module('adsl.core.export.export_glb')


def pyramid(*, reverse=False, open_mesh=False):
    import bpy
    # Three boundary corners of a planar quad are collinear. Its valid area is
    # a triangle; keeping the intermediate vertex is necessary for its neighbors.
    vertices = [(0, 3, 0), (0, 4, 1), (0, -1, 0), (0, 1, 0), (-1, 0, .5)]
    faces = [(0, 1, 2, 3), (1, 0, 4), (2, 1, 4), (3, 2, 4), (0, 3, 4)]
    if reverse:
        faces = [tuple(reversed(f)) for f in faces]
        faces[0] = faces[0][1:] + faces[0][:1]
    if open_mesh:
        faces.pop()
    data = bpy.data.meshes.new('collinear_boundary')
    data.from_pydata(vertices, [], faces)
    data.update()
    obj = bpy.data.objects.new('side_fixture', data)
    bpy.context.collection.objects.link(obj)
    for i in range(2):
        material = bpy.data.materials.new(f'material_{i}')
        material.use_nodes = True
        data.materials.append(material)
    for i, polygon in enumerate(data.polygons):
        polygon.material_index = i % 2
    return obj


def mesh_of(obj):
    import trimesh
    v, f, _ = exporter._mesh_triangles(obj.data, obj.matrix_world)
    return trimesh.Trimesh(v, f, process=False)


@pytest.fixture(autouse=True)
def fresh_blender():
    bpy = pytest.importorskip('bpy')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    yield
    bpy.ops.wm.read_factory_settings(use_empty=True)


@pytest.mark.parametrize('reverse', [False, True])
def test_collinear_polygon_retriangulation_preserves_shape_and_materials(reverse):
    obj = pyramid(reverse=reverse)
    before = mesh_of(obj)
    assert np.count_nonzero(before.area_faces == 0) == 1
    material_area = [sum(t.area for t in obj.data.loop_triangles
                         if obj.data.polygons[t.polygon_index].material_index == i) for i in range(2)]
    exporter._normalize_zero_area_tessellation(obj)
    after = mesh_of(obj)
    assert np.all(after.area_faces > 0)
    assert after.is_watertight and after.is_winding_consistent
    assert len(after.split(only_watertight=False, repair=False)) == 1
    np.testing.assert_array_equal(after.vertices, before.vertices)
    assert after.volume == pytest.approx(before.volume, rel=1e-13)
    assert after.area == pytest.approx(before.area, rel=1e-13)
    assert obj['adsl_retriangulated_polygons'] == 1
    assert obj['adsl_mesh_shell_components'] == 1
    for i in range(2):
        assert sum(t.area for t in obj.data.loop_triangles
                   if obj.data.polygons[t.polygon_index].material_index == i) == pytest.approx(material_area[i])
    # Idempotent; a valid mesh must not be rebuilt a second time.
    data = obj.data
    exporter._normalize_zero_area_tessellation(obj)
    assert obj.data is data


def test_small_positive_faces_are_not_filtered():
    import bpy
    data = bpy.data.meshes.new('thin_tetrahedron')
    data.from_pydata([(0,0,0),(1,0,0),(0,1e-12,0),(0,0,1)], [],
                     [(0,2,1),(0,1,3),(0,3,2),(1,2,3)])
    obj = bpy.data.objects.new('valid_small_detail', data)
    bpy.context.collection.objects.link(obj)
    before = mesh_of(obj)
    assert before.is_volume and 0 < before.area_faces.min() < 1e-10
    exporter._normalize_zero_area_tessellation(obj)
    assert obj.data is data
    np.testing.assert_array_equal(mesh_of(obj).triangles, before.triangles)
    assert not obj.get('adsl_retriangulated_polygons')


@pytest.mark.parametrize('defect', ['open', 'triangle', 'winding', 'native_failure'])
def test_unsafe_normalization_is_an_evaluation_error_without_mutation(monkeypatch, defect):
    import bpy
    import bmesh
    obj = pyramid(open_mesh=defect == 'open')
    if defect == 'triangle':
        obj.data.clear_geometry()
        obj.data.from_pydata([(0,0,0),(1,0,0),(2,0,0)], [], [(0,1,2)])
    if defect == 'winding':
        bm = bmesh.new(); bm.from_mesh(obj.data); bm.faces.ensure_lookup_table()
        bm.faces[1].normal_flip(); bm.to_mesh(obj.data); bm.free()
    if defect == 'native_failure':
        def fail(*args, **kwargs):
            raise RuntimeError('native triangulation unavailable')
        monkeypatch.setattr(bmesh.ops, 'triangulate', fail)
    original = obj.data
    before = mesh_of(obj).triangles.copy()
    mesh_count = len(bpy.data.meshes)
    with pytest.raises(ValueError, match='EVALUATED_MESH_DEGENERATE:.*side_fixture'):
        exporter._normalize_zero_area_tessellation(obj)
    assert obj.data is original and len(bpy.data.meshes) == mesh_count
    np.testing.assert_array_equal(mesh_of(obj).triangles, before)
    assert not obj.get('adsl_retriangulated_polygons')


def test_connected_components_are_preserved_not_fused():
    import bmesh
    obj = pyramid()
    bm = bmesh.new(); bm.from_mesh(obj.data)
    second = bmesh.ops.create_cube(bm, size=1)
    for v in second['verts']:
        v.co.x += 10
    bm.to_mesh(obj.data); bm.free()
    before = mesh_of(obj)
    exporter._normalize_zero_area_tessellation(obj)
    after = mesh_of(obj)
    assert after.is_watertight and after.is_winding_consistent
    assert len(after.split(only_watertight=False, repair=False)) == 2
    assert obj['adsl_mesh_shell_components'] == 2
    assert after.volume == pytest.approx(before.volume)
    np.testing.assert_array_equal(after.vertices, before.vertices)


@pytest.mark.parametrize('mode', ['visual_only', 'geometry'])
def test_normalized_mesh_is_shared_by_stl_glb_and_topology(tmp_path, monkeypatch, mode):
    from adsl.core import Cube, FixedAssembly
    from adsl.core.assembly_topology import read_print_mesh, part_measurement
    from adsl.core.export.export_assembly import export_assembly, _verify_written_exports
    def build(*args, **kwargs):
        return [pyramid()]
    monkeypatch.setattr(exporter, '_build_shape', build)
    assembly = FixedAssembly(root_id='part', mm_per_unit=1)
    assembly.add_part('part', Cube((1,5,1)), components=('fixture',))
    report = export_assembly(assembly, tmp_path, source_sha256='test', expected=dict(
        mm_per_unit=1, fit_offset_mm=.2, final_size_mm=[1,5,1], validation_mode=mode))
    if mode == 'visual_only':
        assert report['export_status'] == 'PASS', report['failures']
    assert report['status'] == ('PASS' if mode == 'geometry' else 'NOT_EVALUATED')
    part = report['parts'][0]
    assert part['mesh_normalizations'][0]['zero_area_triangles_before'] == 1
    assert part['mesh_normalizations'][0]['zero_area_triangles_after'] == 0
    stl = read_print_mesh(tmp_path/part['stl'], part['print_transform_mm'])
    measured, _ = part_measurement(stl, 'part', part.get('mesh_face_groups'))
    assert measured['status'] == 'PASS'
    _verify_written_exports(tmp_path, report, {}, surface_meshes={'part':stl},
                            compare_triangles=True, compare_placement=True)
    assert not report['failures'], report['failures']


def test_tiny_positive_faces_survive_retriangulation_elsewhere():
    import bmesh
    obj = pyramid()
    bm = bmesh.new(); bm.from_mesh(obj.data)
    vertices = [bm.verts.new(p) for p in [(10,0,0),(11,0,0),(10,1e-12,0),(10,0,1)]]
    for face in [(0,2,1),(0,1,3),(0,3,2),(1,2,3)]:
        bm.faces.new([vertices[i] for i in face])
    bm.to_mesh(obj.data); bm.free()
    before = mesh_of(obj)
    tiny = before.triangles[(before.area_faces > 0) & (before.area_faces < 1e-10)]
    assert len(tiny) == 2
    exporter._normalize_zero_area_tessellation(obj)
    after = mesh_of(obj)
    assert np.all(after.area_faces > 0)
    assert len(after.split(only_watertight=False, repair=False)) == 2
    np.testing.assert_array_equal(after.vertices, before.vertices)
    for triangle in tiny:
        assert any(np.array_equal(triangle, other) for other in after.triangles)


def test_unsuccessful_normalization_does_not_claim_a_design_defect(tmp_path, monkeypatch):
    from adsl.core import Cube, FixedAssembly
    from adsl.core.export import export_assembly as assembly_exporter
    assembly = FixedAssembly(root_id='part', mm_per_unit=1)
    assembly.add_part('part', Cube(1), components=('fixture',))
    def fail(*args, **kwargs):
        raise ValueError('EVALUATED_MESH_DEGENERATE: object=part zero_area_triangles=1 reason=unresolved')
    monkeypatch.setattr(assembly_exporter, 'evaluated', fail)
    report = assembly_exporter.export_assembly(assembly, tmp_path, source_sha256='test',
        expected=dict(mm_per_unit=1, fit_offset_mm=.2, validation_mode='visual_only'))
    failure = report['failures'][0]
    assert report['export_status'] == 'FAIL'
    assert failure['failure_kind'] == 'unknown'
    assert failure['reason'].startswith('EVALUATED_MESH_DEGENERATE:')
