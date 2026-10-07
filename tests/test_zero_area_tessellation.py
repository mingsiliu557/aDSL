"""Exact-zero tessellation normalization; native Blender, no model API or renderer."""
import importlib
import os
import json
from pathlib import Path

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


def zero_length_fixture():
    import bpy
    raw = json.loads((Path(__file__).parent/'fixtures/sf16_zero_length_edge.json').read_text())
    data = bpy.data.meshes.new('sf16_boolean_duplicate')
    data.from_pydata(raw['vertices'], [], raw['polygons'])
    data.update()
    obj = bpy.data.objects.new('sf16_back', data)
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
@pytest.mark.parametrize('fixture', ['planar', 'zero_length'])
def test_normalized_mesh_is_shared_by_stl_glb_and_topology(tmp_path, monkeypatch, mode, fixture):
    from adsl.core import Cube, FixedAssembly
    from adsl.core.assembly_topology import read_print_mesh, part_measurement
    from adsl.core.export.export_assembly import export_assembly, _verify_written_exports
    from adsl.core.export import mesh64
    from adsl.core.export.mesh_validity import normalize_blender_input
    factory = pyramid if fixture == 'planar' else zero_length_fixture
    bounds = mesh_of(factory()).bounds
    def tessellate(*args, **kwargs):
        obj = factory()
        normalization = normalize_blender_input(obj, node_path='fixture')
        vertices, faces, _ = exporter._mesh_triangles(obj.data, obj.matrix_world)
        return vertices, faces, normalization['normalizations']
    # The legacy polygon defect enters through the leaf tessellator. Every
    # subsequent CSG/export/checker step uses the real canonical evaluator.
    monkeypatch.setattr(mesh64, '_unit_mesh', tessellate)
    assembly = FixedAssembly(root_id='part', mm_per_unit=1)
    assembly.add_part('part', Cube(1), components=('fixture',))
    report = export_assembly(assembly, tmp_path, source_sha256='test', expected=dict(
        mm_per_unit=1, fit_offset_mm=.2, final_size_mm=(bounds[1]-bounds[0]).tolist(), validation_mode=mode))
    if mode == 'visual_only':
        assert report['export_status'] == 'PASS', report['failures']
    assert report['status'] == ('PASS' if mode == 'geometry' else 'NOT_EVALUATED')
    part = report['parts'][0]
    assert part['mesh_normalizations'][0]['zero_area_triangles_before'] == (1 if fixture == 'planar' else 2)
    if fixture == 'zero_length':
        assert part['mesh_normalizations'][0]['method'] == 'exact_zero_length_edge_cleanup'
        assert part['mesh_normalizations'][0]['maximum_displacement_mm'] == 0
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


def test_unsuccessful_normalization_reports_an_evaluation_failure(tmp_path, monkeypatch):
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
    assert failure['failure_kind'] == 'candidate_evaluation'
    assert failure['reason'].startswith('EVALUATED_MESH_DEGENERATE:')


def test_exact_zero_length_edge_preserves_positive_geometry_and_materials():
    obj = zero_length_fixture()
    before = mesh_of(obj)
    assert np.count_nonzero(before.area_faces == 0) == 2
    area_by_material = [sum(t.area for t in obj.data.loop_triangles
        if obj.data.polygons[t.polygon_index].material_index == i) for i in range(2)]
    exporter._normalize_zero_area_tessellation(obj)
    after = mesh_of(obj)
    assert len(after.vertices) == len(before.vertices)-1
    assert np.all(after.area_faces > 0) and after.is_volume and after.is_winding_consistent
    assert len(after.split(only_watertight=False, repair=False)) == 1
    np.testing.assert_array_equal(np.unique(before.vertices, axis=0), np.unique(after.vertices, axis=0))
    assert after.area == pytest.approx(before.area, rel=1e-13)
    assert after.volume == pytest.approx(before.volume, rel=1e-13)
    for i in range(2):
        assert sum(t.area for t in obj.data.loop_triangles
            if obj.data.polygons[t.polygon_index].material_index == i) == pytest.approx(area_by_material[i], rel=1e-13)
    info = json.loads(obj['adsl_zero_length_normalization'])
    assert info['merged_vertices'] == 1 and info['maximum_displacement_mm'] == 0
    assert info['positive_triangle_surfaces_unchanged'] and info['materials_preserved']
    data = obj.data
    exporter._normalize_zero_area_tessellation(obj)
    assert obj.data is data


@pytest.mark.parametrize('defect', ['open', 'native_failure', 'moved_vertex', 'changed_material', 'changed_diagonals'])
def test_exact_zero_length_cleanup_rejects_unsafe_copy(monkeypatch, defect):
    import bpy
    import bmesh
    obj = zero_length_fixture()
    if defect == 'open':
        bm = bmesh.new(); bm.from_mesh(obj.data); bm.faces.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.faces[0]], context='FACES_ONLY')
        bm.to_mesh(obj.data); bm.free()
    if defect in ('native_failure', 'moved_vertex', 'changed_material'):
        actual = bmesh.ops.weld_verts
        def weld(bm, **kwargs):
            if defect == 'native_failure':
                raise RuntimeError('injected weld failure')
            result = actual(bm, **kwargs)
            if defect == 'moved_vertex':
                max(bm.verts, key=lambda v:v.co.z).co.z += .1
            else:
                face = next(f for f in bm.faces if f.calc_area() > 1)
                face.material_index = 1-face.material_index
            return result
        monkeypatch.setattr(bmesh.ops, 'weld_verts', weld)
    if defect == 'changed_diagonals':
        actual = bmesh.ops.triangulate
        def triangulate(bm, **kwargs):
            return actual(bm, **{**kwargs, 'ngon_method':'BEAUTY'})
        monkeypatch.setattr(bmesh.ops, 'triangulate', triangulate)
    data, triangles = obj.data, mesh_of(obj).triangles.copy()
    count = len(bpy.data.meshes)
    with pytest.raises(ValueError, match='EVALUATED_MESH_DEGENERATE:'):
        exporter._normalize_zero_area_tessellation(obj)
    assert obj.data is data and len(bpy.data.meshes) == count
    np.testing.assert_array_equal(mesh_of(obj).triangles, triangles)
    assert not obj.get('adsl_zero_length_normalization')


def test_nearby_vertices_are_not_exact_zero_length_candidates():
    obj = zero_length_fixture()
    value = np.float32(obj.data.vertices[23].co.z)
    obj.data.vertices[23].co.z = float(np.nextafter(value, np.float32(np.inf)))
    before = np.array([tuple(v.co) for v in obj.data.vertices])
    try:
        exporter._normalize_zero_area_tessellation(obj)
    except ValueError:
        pass  # Unsupported geometry stays unverified, never approximately welded.
    assert not obj.get('adsl_zero_length_normalization')
    np.testing.assert_array_equal(before, np.array([tuple(v.co) for v in obj.data.vertices]))


def test_exact_cleanup_preserves_small_positive_faces_in_other_component():
    import bmesh
    obj = zero_length_fixture()
    bm = bmesh.new(); bm.from_mesh(obj.data)
    verts = [bm.verts.new(v) for v in [(1000,0,0),(1001,0,0),(1000,1e-12,0),(1000,0,1)]]
    for ids in [(0,2,1),(0,1,3),(0,3,2),(1,2,3)]:
        bm.faces.new([verts[i] for i in ids])
    bm.to_mesh(obj.data); bm.free()
    before = mesh_of(obj)
    tiny = before.triangles[(before.area_faces > 0) & (before.area_faces < 1e-10)]
    assert len(tiny) == 2
    exporter._normalize_zero_area_tessellation(obj)
    after = mesh_of(obj)
    assert len(after.split(only_watertight=False, repair=False)) == 2
    for triangle in tiny:
        assert any(np.array_equal(triangle, other) for other in after.triangles)
