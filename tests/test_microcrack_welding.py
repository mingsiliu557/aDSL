"""Bounded local welding on native Blender copies, without models or rendering."""
import importlib
import os

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL') != '1',
                                reason='explicit native Blender geometry validation')
exporter = importlib.import_module('adsl.core.export.mesh_repair')


def cracked_cube(*, gap=2**-17, origin=100., extra_hole=False):
    import bpy
    vertices = np.array([(0,0,0),(1,0,0),(1,1,0),(0,1,0),
                         (0,0,1),(1,0,1),(1,1,1),(0,1,1),(gap,gap,0)]) + origin
    # One polygon takes a detour around a nearly duplicate boundary corner.
    # Its neighboring polygon retains the original edge, leaving a thin 3-edge loop.
    faces = [(0,8,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    if extra_hole:
        faces.pop(1)
    data = bpy.data.meshes.new('roundoff_crack')
    data.from_pydata(vertices, [], faces)
    data.update()
    obj = bpy.data.objects.new('part', data)
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
    vertices, faces, _ = exporter._mesh_triangles(obj.data, obj.matrix_world)
    return trimesh.Trimesh(vertices, faces, process=False)


@pytest.fixture(autouse=True)
def fresh_blender():
    bpy = pytest.importorskip('bpy')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    yield
    bpy.ops.wm.read_factory_settings(use_empty=True)


@pytest.mark.parametrize('scale', [.1, 1., 2.])
def test_local_weld_closes_crack_preserves_faces_materials_and_units(scale):
    obj = cracked_cube()
    before = mesh_of(obj)
    face_materials = [p.material_index for p in obj.data.polygons]
    report = exporter._normalize_numeric_microcracks(obj, scale)
    assert report['status'] == 'APPLIED', report
    assert report['boundary_edges_before'] == 3 and report['boundary_edges_after'] == 0
    assert report['maximum_displacement_mm'] == pytest.approx(np.sqrt(2)*2**-17*scale)
    after = mesh_of(obj)
    assert after.is_volume and after.is_winding_consistent
    assert np.all(after.area_faces > 0)
    assert after.volume == pytest.approx(before.volume, abs=3e-5)
    assert [p.material_index for p in obj.data.polygons] == face_materials
    assert report['shell_components'] == 1
    for vertex in after.vertices:
        assert any(np.array_equal(vertex, old) for old in before.vertices)
    data = obj.data
    assert exporter._normalize_numeric_microcracks(obj, scale) is None
    assert obj.data is data


@pytest.mark.parametrize('kwargs,scale', [({'gap': .001},1.), ({'origin': 0.},1.), ({},100.)])
def test_real_gaps_or_excessive_physical_displacement_are_not_welded(kwargs, scale):
    obj = cracked_cube(**kwargs)
    data, triangles = obj.data, mesh_of(obj).triangles.copy()
    report = exporter._normalize_numeric_microcracks(obj, scale)
    assert report['status'] == 'SKIPPED', report
    assert report['maximum_displacement_mm'] == 0
    assert obj.data is data
    np.testing.assert_array_equal(mesh_of(obj).triangles, triangles)


@pytest.mark.parametrize('defect', ['other_hole', 'winding', 'native_failure', 'unrelated_vertex_move'])
def test_rejected_weld_rolls_back_geometry_and_datablocks(monkeypatch, defect):
    import bpy
    import bmesh
    obj = cracked_cube(extra_hole=defect == 'other_hole')
    if defect == 'winding':
        bm = bmesh.new(); bm.from_mesh(obj.data); bm.faces.ensure_lookup_table()
        bm.faces[1].normal_flip(); bm.to_mesh(obj.data); bm.free()
    real_weld = bmesh.ops.weld_verts
    if defect == 'native_failure':
        def weld(*args, **kwargs):
            raise RuntimeError('injected native failure')
        monkeypatch.setattr(bmesh.ops, 'weld_verts', weld)
    if defect == 'unrelated_vertex_move':
        def weld(bm, **kwargs):
            real_weld(bm, **kwargs)
            max(bm.verts, key=lambda v:v.co.z).co.z += .1
        monkeypatch.setattr(bmesh.ops, 'weld_verts', weld)
    data, triangles = obj.data, mesh_of(obj).triangles.copy()
    count = len(bpy.data.meshes)
    report = exporter._normalize_numeric_microcracks(obj, 1.)
    assert report['status'] == 'REJECTED', report
    assert report['boundary_edges_after'] == report['boundary_edges_before']
    assert report['maximum_displacement_mm'] == 0
    assert obj.data is data and len(bpy.data.meshes) == count
    np.testing.assert_array_equal(mesh_of(obj).triangles, triangles)


def test_separate_nearby_shells_and_objects_are_never_fused():
    import bmesh
    obj = cracked_cube()
    other = cracked_cube(origin=100.00003)
    other_data = other.data
    other_vertices = mesh_of(other).vertices.copy()
    # Separate shells in one object; search is restricted to each boundary loop.
    bm = bmesh.new(); bm.from_mesh(obj.data); bm.from_mesh(other.data)
    bm.to_mesh(obj.data); bm.free()
    report = exporter._normalize_numeric_microcracks(obj, 1.)
    assert report['status'] == 'APPLIED', report
    assert report['shell_components'] == 2
    assert len(mesh_of(obj).split(only_watertight=False, repair=False)) == 2
    assert other.data is other_data
    np.testing.assert_array_equal(mesh_of(other).vertices, other_vertices)


@pytest.mark.parametrize('mode', ['visual_only', 'geometry'])
def test_welded_mesh_is_shared_by_stl_glb_and_topology(tmp_path, monkeypatch, mode):
    from adsl.core import Cube, FixedAssembly
    from adsl.core.assembly_topology import read_print_mesh, part_measurement
    from adsl.core.export.export_assembly import export_assembly, _verify_written_exports
    from adsl.core.export import mesh64
    from adsl.core.export.mesh_validity import normalize_blender_input
    def tessellate(*args, **kwargs):
        # Gap is inside the unit object's local precision budget. Translating
        # an object may not increase its permitted repair displacement.
        obj = cracked_cube(origin=.5, gap=2**-24)
        normalization = normalize_blender_input(obj, node_path='fixture')
        vertices, faces, _ = exporter._mesh_triangles(obj.data, obj.matrix_world)
        return vertices, faces, normalization['normalizations']
    monkeypatch.setattr(mesh64, '_unit_mesh', tessellate)
    assembly = FixedAssembly(root_id='part', mm_per_unit=1)
    assembly.add_part('part', Cube(1), components=('fixture',))
    report = export_assembly(assembly, tmp_path, source_sha256='test', expected=dict(
        mm_per_unit=1, fit_offset_mm=.2, final_size_mm=[1,1,1], validation_mode=mode))
    assert report['status'] == ('PASS' if mode == 'geometry' else 'NOT_EVALUATED'), report
    part = report['parts'][0]
    normalization = part['mesh_normalizations'][0]
    assert normalization['status'] == 'APPLIED'
    assert normalization['boundary_edges_after'] == 0
    stl = read_print_mesh(tmp_path/part['stl'], part['print_transform_mm'])
    measured, _ = part_measurement(stl, 'part', part.get('mesh_face_groups'))
    assert measured['status'] == 'PASS'
    _verify_written_exports(tmp_path, report, {}, surface_meshes={'part': stl},
                            compare_triangles=True, compare_placement=True)
    assert not report['failures'], report['failures']
