"""Recursive solid evaluation and directed-shell semantics, without model calls."""
import importlib
import json
from pathlib import Path
import runpy

import numpy as np
import pytest

from adsl.core import (Asset, Cube, Cylinder, Sphere, boolean_difference,
                       boolean_intersection, boolean_union, hull, translate_shape, scale_shape)
from adsl.core.export.mesh64 import evaluate_shape


@pytest.fixture(autouse=True)
def native_leaves():
    bpy = pytest.importorskip('bpy')
    pytest.importorskip('manifold3d')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    yield bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)


@pytest.mark.parametrize('operation,expected', [
    (boolean_union, 12.0), (boolean_difference, 4.0), (boolean_intersection, 4.0)])
def test_mesh64_boolean_volumes_materials_and_local_frame(operation, expected):
    result = evaluate_shape(operation(Cube((2, 2, 2), center=(3, 4, 5), color=(1, 0, 0)),
                                      Cube((2, 2, 2), center=(4, 4, 5), color=(0, 0, 1))))
    assert len(result.pieces) == 1
    piece, = result.pieces
    assert piece.solid.volume() == pytest.approx(expected)
    mesh = piece.world_mesh()
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(expected)
    assert set(mesh.face_attributes['material']) == {0, 1}
    assert result.materials[0]['base_color'][:3] == [1, 0, 0]
    assert result.materials[1]['base_color'][:3] == [0, 0, 1]
    assert all(r['output_precision'] == 'float64' for r in result.records)
    json.dumps(result.records)


def test_nested_csg_never_builds_blender_boolean_intermediates(monkeypatch, native_leaves):
    shape = boolean_intersection(
        boolean_difference(boolean_union(Cube(2), Cube(2, center=(1, 0, 0))),
                           Cube((1, 4, 4), center=(-.5, 0, 0))),
        Cube((2, 4, 4), center=(1, 0, 0)))
    result = evaluate_shape(shape)
    assert result.pieces[0].solid.volume() == pytest.approx(8)
    operations = [r['operation'] for r in result.records if r.get('method') == 'recursive_manifold_mesh64']
    assert operations == ['UNION', 'DIFFERENCE', 'INTERSECT']
    assert not [o for o in native_leaves.context.scene.objects if o.type == 'MESH']


def test_multi_object_base_uses_same_cutters_without_implicitly_unioning():
    base = Asset(label='two_bases')
    base.attach_part('a', Cube(2, center=(-2, 0, 0)))
    base.attach_part('b', Cube(2, center=(2, 0, 0)))
    result = evaluate_shape(boolean_difference(base, Cube((10, 1, 4))))
    assert len(result.pieces) == 2
    assert [p.solid.volume() for p in result.pieces] == pytest.approx([4, 4])
    assert sum(p.world_mesh().volume for p in result.pieces) == pytest.approx(8)


def test_directed_cavity_shell_stays_hollow_in_recursive_and_mesh_input():
    cavity = evaluate_shape(boolean_difference(Cube(4), Cube(2)))
    piece, = cavity.pieces
    assert piece.solid.volume() == pytest.approx(56)
    raw = piece.world_mesh()
    assert len(raw.split(only_watertight=False)) == 2
    assert sorted(s.volume for s in raw.split(only_watertight=False)) == pytest.approx([-8, 64])
    asset = Asset(label='directed_cavity')
    asset.add_primitive(dict(type='mesh', params=dict(vertices=raw.vertices, triangles=raw.faces),
        xform=np.eye(4), color=(1, 1, 1), alpha=None))
    reread = evaluate_shape(boolean_union(asset, Cube(1, center=(8, 0, 0))))
    assert reread.pieces[0].solid.volume() == pytest.approx(57)
    assert reread.pieces[0].world_mesh().volume == pytest.approx(57)


def test_empty_intersection_is_not_skipped_by_outer_csg():
    empty = boolean_intersection(Cube(1), Cube(1, center=(5, 0, 0)))
    result = evaluate_shape(boolean_intersection(empty, Cube(10)))
    assert len(result.pieces) == 1 and result.pieces[0].solid.is_empty()
    union = evaluate_shape(boolean_union(empty, Cube(1)))
    assert union.pieces[0].solid.volume() == pytest.approx(1)


@pytest.mark.parametrize('empty_first', [False, True])
@pytest.mark.parametrize('empty_kind', ['asset', 'nested_intersection', 'nested_difference'])
def test_intersection_preserves_empty_operand_in_both_orders(empty_first, empty_kind):
    if empty_kind == 'asset':
        empty = Asset('empty_operand')
    elif empty_kind == 'nested_intersection':
        empty = boolean_intersection(Cube(1), Cube(1, center=(5,0,0)))
    else:
        empty = boolean_difference(Asset('empty_base'), Cube(1))
    operands = [empty, Cube(10)] if empty_first else [Cube(10), empty]
    result = evaluate_shape(boolean_intersection(*operands))
    assert len(result.pieces)==1 and result.pieces[0].solid.is_empty()
    final = result.records[-1]
    assert final['operation']=='INTERSECT' and final['input_count']==2 and final['empty']


@pytest.mark.parametrize('contains_empty', [False, True])
def test_intersection_unions_pieces_within_each_operand(contains_empty):
    group = Asset('multi_piece_operand')
    group.attach_part('left', Cube(2,center=(-2,0,0)))
    group.attach_part('right', Cube(2,center=(2,0,0)))
    if contains_empty:
        group.attach_part('empty', boolean_intersection(Cube(1),Cube(1,center=(5,0,0))))
    result = evaluate_shape(boolean_intersection(group,Cube((10,2,2))))
    piece, = result.pieces
    assert piece.solid.volume()==pytest.approx(16)
    assert sum(component.volume()>0 for component in piece.solid.decompose())==2
    assert result.records[-1]['operation']=='INTERSECT'
    assert result.records[-1]['input_count']==2


@pytest.mark.parametrize('operation', [boolean_union, boolean_difference, hull])
def test_empty_operand_remains_neutral_for_union_cutters_and_hull(operation):
    result = evaluate_shape(operation(Cube(2),Asset('empty_operand')))
    assert len(result.pieces)==1 and result.pieces[0].solid.volume()==pytest.approx(8)


def test_independent_parts_joint_nodes_and_scene_units_remain_distinct():
    scene = Asset(label='root')
    scene.attach_part('fixed_a', Cube(1, center=(0, 0, 0)))
    scene.attach_part('fixed_b', Cube(1, center=(3, 0, 0)))
    scene.attach_part('moving', Cube(1, center=(0, 0, 0)))
    scene.prismatic('moving', axis=(1, 0, 0), origin=(0, 0, 0), initial=2, limit=(0, 3))
    result = evaluate_shape(scene, mm_per_unit=4)
    assert len(result.pieces) == 3
    nodes = {node['path']: node for node in result.nodes}
    assert nodes['root/moving']['attach_mode'] == 'joint'
    assert nodes['root/moving']['joint']['joint_type'] == 'prismatic'
    moving = next(p for p in result.pieces if p.node_path == 'root/moving')
    np.testing.assert_allclose(moving.world_mesh(mm_per_unit=4).bounds, [[6, -2, -2], [10, 2, 2]])
    assert len(evaluate_shape(scene, include_joint_children=False).pieces) == 2


@pytest.mark.parametrize('translation', [(0, 0, 0), (10, -20, 30), (1e7, -2e7, 3e7)])
def test_common_large_translation_does_not_change_boolean_precision(translation):
    shape = translate_shape(boolean_union(Cube(2), Cube(2, center=(1, 0, 0))), translation)
    piece, = evaluate_shape(shape).pieces
    assert piece.solid.volume() == pytest.approx(12)
    assert np.abs(np.asarray(piece.solid.to_mesh64().vert_properties)[:, :3]).max() <= 3
    bounds = piece.world_mesh().bounds - np.asarray(translation)
    np.testing.assert_allclose(bounds, [[-1, -1, -1], [2, 1, 1]], rtol=0, atol=1e-9)


def test_leaf_cylinder_dimensions_are_float64_and_hull_uses_canonical_inputs():
    shift = (1e7, 0, 0)
    cylinder = Cylinder(.025, p0=(1e7, 0, 0), p1=(1e7+.05, 0, 0), color=(.2, .3, .4))
    piece, = evaluate_shape(cylinder).pieces
    np.testing.assert_allclose(piece.world_mesh().bounds - shift,
                               [[0, -.025, -.025], [.05, .025, .025]], atol=1e-9)
    result = evaluate_shape(hull(Cube(1), Cube(1, center=(2, 0, 0)), color=(0, 1, 0)))
    assert result.pieces[0].solid.volume() == pytest.approx(3)
    assert set(result.pieces[0].world_mesh().face_attributes['material']) == {1}
    assert result.materials[1]['base_color'][:3] == [0, 1, 0]


def test_equal_appearance_shares_material_without_merging_distinct_colors():
    same = evaluate_shape(boolean_union(Cube(2, color=(.2, .3, .4)),
        Sphere(1, center=(1, 0, 0), color=(.2, .3, .4))))
    assert len(same.materials) == 1
    assert set(same.pieces[0].world_mesh().face_attributes['material']) == {0}
    different = evaluate_shape(boolean_union(Cube(2, color=(.2, .3, .4)),
        Sphere(1, center=(1, 0, 0), color=(.2, .3, .5))))
    assert len(different.materials) == 2
    assert set(different.pieces[0].world_mesh().face_attributes['material']) == {0, 1}


@pytest.mark.parametrize('part', ['arm', 'nested_upper'])
def test_original_complex_csg_keeps_common_translation_outside_internal_geometry(part):
    source = Path(__file__).resolve().parents[1]/'reports/benchmark_six_boolean_failure_20261007/source.py'
    original = runpy.run_path(str(source))
    shape = (original['upper'].curved_upper_arm if part == 'arm'
             else original['assembly'].parts['upper_structure'])
    baseline, = evaluate_shape(shape).pieces
    displacement = np.array([1e7, 2e7, -3e7])
    moved, = evaluate_shape(translate_shape(shape, displacement)).pieces
    assert moved.solid.volume() == pytest.approx(baseline.solid.volume(), rel=1e-10)
    np.testing.assert_allclose(moved.transform[:3, 3]-baseline.transform[:3, 3], displacement, rtol=0, atol=0)
    from adsl.core.export.mesh_validity import mesh_metrics
    raw = moved.solid.to_mesh64()
    metrics = mesh_metrics(np.asarray(raw.vert_properties)[:, :3], raw.tri_verts)
    assert metrics['valid']
    assert metrics['zero_area_triangles'] == metrics['nonmanifold_edges'] == metrics['boundary_edges'] == 0
    # The original local geometry remains near its original 150-unit extent,
    # rather than entering the Boolean operands at ten-million-unit coordinates.
    assert np.abs(np.asarray(raw.vert_properties)[:, :3]).max() < 200


def test_world_mesh_reflection_preserves_outer_and_cavity_shell_directions():
    cavity, = evaluate_shape(boolean_difference(Cube(4), Cube(2))).pieces
    raw = cavity.world_mesh()
    shape = Asset('directed_cavity')
    shape.add_primitive(dict(type='mesh', params=dict(vertices=raw.vertices, triangles=raw.faces),
                             xform=np.eye(4), color=(1, 1, 1), alpha=None))
    piece, = evaluate_shape(scale_shape(shape, (-1, 2, 3), center=(0, 0, 0))).pieces
    assert np.linalg.det(piece.transform[:3, :3]) < 0
    reflected = piece.world_mesh()
    assert reflected.is_volume
    assert reflected.volume == pytest.approx(56*6)
    assert reflected.volume == pytest.approx(piece.world_solid().volume())
    assert sorted(s.volume for s in reflected.split(only_watertight=False)) == pytest.approx([-8*6, 64*6])
    np.testing.assert_allclose(reflected.bounds, [[-2, -4, -6], [2, 4, 6]])
