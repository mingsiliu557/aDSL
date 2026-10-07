"""Parallel-section loft contracts and opt-in native export/render smoke."""
import builtins
import json
import math
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from adsl.core import (
    Cube, Polygon, align_anchors, loft, rotation_matrix, scaling_matrix,
    shape_aabb, shape_support, transform_shape, translation_matrix,
)
from adsl.core.constructive import _loft_cap, _pchip_evaluate, _pchip_slopes, _sample_loft_rings


def mesh_of(asset):
    params = asset.local_primitives()[0]['params']
    return trimesh.Trimesh(params['vertices'], params['triangles'], process=False)


def rectangle(width=2., height=2., center=(0., 0.)):
    u, v = center
    return Polygon([(u-width/2, v-height/2), (u+width/2, v-height/2),
                    (u+width/2, v+height/2), (u-width/2, v+height/2)])


def assert_solid(mesh):
    assert mesh.is_watertight and mesh.is_winding_consistent and mesh.volume > 0
    assert np.all(mesh.area_faces > 0)
    assert len(mesh.split(only_watertight=False)) == 1


@pytest.mark.parametrize('axis,expected', [
    ('z', [[-1, -2, -2], [3, 1, 3]]),
    ('x', [[-2, -1, -2], [3, 3, 1]]),
    ('y', [[-2, -2, -1], [1, 3, 3]]),
])
def test_axis_mapping_prism_volume_and_winding(axis, expected):
    profile = Polygon([(-1, -2), (3, -2), (3, 1), (-1, 1)])
    mesh = mesh_of(loft([profile, profile], [-2, 3], axis=axis))
    assert_solid(mesh)
    assert mesh.volume == pytest.approx(60.)
    np.testing.assert_allclose(mesh.bounds, expected)


@pytest.mark.parametrize('points,area', [
    ([(0, 0), (4, 0), (4, 1), (1, 1), (1, 3), (0, 3)], 6),
    ([(0, 0), (2, 0), (4, 0), (4, 1), (1, 1), (1, 2), (1, 3), (0, 3)], 6),
])
def test_concave_caps_with_collinear_boundary_segments(points, area):
    profile = Polygon(points)
    mesh = mesh_of(loft([profile, profile], [0, 5], interpolation='linear', samples_per_span=3))
    assert_solid(mesh)
    assert mesh.volume == pytest.approx(area * 5)


def test_cap_reconstructs_boundary_segment_omitted_by_triangulation():
    from collections import Counter
    ring = np.array([(0, 0), (1, 0), (2, 0), (2, 1), (0, 1)], dtype=float)
    triangulator = SimpleNamespace(triangulate=lambda *args, **kwargs: [(0, 2, 3), (0, 3, 4)])
    faces = _loft_cap(ring, 'first cap', triangulator)
    edges = Counter(tuple(sorted((a, b))) for triangle in faces
                    for a, b in zip(triangle, (*triangle[1:], triangle[0])))
    assert {edge for edge, count in edges.items() if count == 1} == {
        tuple(sorted((i, (i+1) % len(ring)))) for i in range(len(ring))}
    triangles = ring[np.array(faces)]
    ab, ac = triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0]
    areas2 = ab[:, 0] * ac[:, 1] - ab[:, 1] * ac[:, 0]
    assert np.all(areas2 > 0) and areas2.sum() / 2 == pytest.approx(2)


@pytest.mark.parametrize('interpolation', ['linear', 'smooth'])
def test_true_mesh_sections_retain_local_waist(interpolation):
    mesh = mesh_of(loft([rectangle(4, 6), rectangle(1, 2), rectangle(4, 6)],
                        [0, 2, 5], interpolation=interpolation))
    assert_solid(mesh)
    # Actual plane/triangle intersection, not a filter on stored control vertices.
    for position, dimensions in ((1e-6, (4, 6)), (2, (1, 2)), (5-1e-6, (4, 6))):
        section = mesh.section(plane_origin=[0, 0, position], plane_normal=[0, 0, 1])
        assert section is not None
        np.testing.assert_allclose(np.ptp(section.vertices, axis=0)[:2], dimensions, atol=5e-6)


def test_pchip_nonuniform_slopes_endpoint_limits_and_turning_points():
    positions = np.array([0., 1., 3.])
    values = np.array([[0., 4., 1.], [1., 1., 1.], [4., 3., 1.]])
    slopes = _pchip_slopes(positions, values)
    np.testing.assert_allclose(slopes[:, 0], [5/6, 27/23, 11/6])
    assert slopes[1, 1] == 0  # Sign change, without a cubic overshoot.
    np.testing.assert_array_equal(slopes[:, 2], 0)
    np.testing.assert_allclose(_pchip_evaluate(positions, values, slopes, positions), values)
    for i in range(2):
        samples = np.linspace(positions[i], positions[i+1], 101)
        observed = _pchip_evaluate(positions, values, slopes, samples)
        assert np.all(observed >= np.minimum(values[i], values[i+1]) - 1e-12)
        assert np.all(observed <= np.maximum(values[i], values[i+1]) + 1e-12)
    eps = 1e-6
    knot = values[1]
    left = (knot - _pchip_evaluate(positions, values, slopes, [1-eps])[0]) / eps
    right = (_pchip_evaluate(positions, values, slopes, [1+eps])[0] - knot) / eps
    np.testing.assert_allclose(left, slopes[1], atol=7e-6)
    np.testing.assert_allclose(right, slopes[1], atol=7e-6)
    # Steep final secant with a flat initial trend: endpoint derivative clips to 0.
    clipped = _pchip_slopes(np.array([0., 1., 2.]), np.array([0., 1., 10.]))
    assert clipped[0] == 0


def test_sampling_controls_are_unique_and_two_sections_are_linear():
    points = np.array([rectangle(2, 3).points, rectangle(4, 5, (1, 2)).points])
    positions = np.array([-.5, 2.5])
    linear_x, linear = _sample_loft_rings(points, positions, 'linear', 4)
    smooth_x, smooth = _sample_loft_rings(points, positions, 'smooth', 4)
    np.testing.assert_allclose(linear_x, [-.5, .25, 1, 1.75, 2.5])
    np.testing.assert_allclose(smooth_x, linear_x)
    np.testing.assert_allclose(smooth, linear)
    np.testing.assert_allclose(linear[2], (points[0] + points[1]) / 2)
    points = np.array([rectangle(2, 3).points, rectangle(4, 5).points, rectangle(1, 2).points])
    positions = np.array([0., 1., 3.])
    sample_x, sampled = _sample_loft_rings(points, positions, 'smooth', 5)
    assert sampled.shape == (11, 4, 2)
    assert np.count_nonzero(sample_x == 1) == 1
    np.testing.assert_allclose(sampled[[0, 5, 10]], points)
    assert np.all(np.diff(sample_x) > 0)


def test_subdivision_changes_curved_geometry_not_only_metadata():
    profiles = [rectangle(2, 2), rectangle(4, 3, (1, .5)), rectangle(1, 2)]
    coarse = mesh_of(loft(profiles, [0, 1, 3], samples_per_span=1))
    fine = mesh_of(loft(profiles, [0, 1, 3], samples_per_span=8))
    coarse_section = coarse.section(plane_origin=[0, 0, .5], plane_normal=[0, 0, 1])
    fine_section = fine.section(plane_origin=[0, 0, .5], plane_normal=[0, 0, 1])
    assert not np.allclose(coarse_section.bounds, fine_section.bounds, atol=1e-4)
    assert_solid(coarse)
    assert_solid(fine)


def test_winding_normalization_preserves_first_vertex_and_correspondence():
    points = [(3, 2), (3, 4), (0, 4), (0, 2)]
    a = Polygon(points)
    b = Polygon([points[0], *reversed(points[1:]), points[0]])
    assert a.points == b.points and a.points[0] == tuple(points[0])
    mesh = mesh_of(loft([a, b], [0, 2]))
    assert_solid(mesh)
    assert mesh.volume == pytest.approx(12)


@pytest.mark.parametrize('profiles,positions,kwargs,reason', [
    ([rectangle()], [0], {}, 'at least two'),
    ([rectangle(), rectangle()], [0], {}, 'positions'),
    ([rectangle(), Polygon([(0, 0), (1, 0), (0, 1)])], [0, 1], {}, 'profile 1.*3.*4'),
    ([rectangle(), 'not a profile'], [0, 1], {}, 'profile 1.*Polygon'),
    ([rectangle(), Polygon([(-2, -2), (2, -2), (2, 2), (-2, 2)],
                           holes=[[(-1, -1), (1, -1), (1, 1), (-1, 1)]])], [0, 1], {}, 'profile 1.*hole'),
    ([rectangle(), rectangle()], [0, 0], {}, 'strictly increasing'),
    ([rectangle(), rectangle()], [1, 0], {}, 'strictly increasing'),
    ([rectangle(), rectangle()], [0, float('inf')], {}, 'finite'),
    ([rectangle(), rectangle()], [float('nan'), 1], {}, 'finite'),
    ([rectangle(), rectangle()], [0, 1], {'axis': 'q'}, 'axis'),
    ([rectangle(), rectangle()], [0, 1], {'interpolation': 'cubic'}, 'interpolation'),
    ([rectangle(), rectangle()], [0, 1], {'samples_per_span': 0}, 'samples_per_span'),
    ([rectangle(), rectangle()], [0, 1], {'samples_per_span': -2}, 'samples_per_span'),
    ([rectangle(), rectangle()], [0, 1], {'samples_per_span': 1.5}, 'samples_per_span'),
    ([rectangle(), rectangle()], [0, 1], {'samples_per_span': True}, 'samples_per_span'),
])
def test_rejects_invalid_loft_inputs(profiles, positions, kwargs, reason):
    with pytest.raises((TypeError, ValueError), match=reason):
        loft(profiles, positions, **kwargs)


def test_mismatched_correspondence_collapse_identifies_span():
    ring = rectangle().points
    shifted = Polygon(ring[2:] + ring[:2])
    with pytest.raises(ValueError, match='span 0'):
        loft([Polygon(ring), shifted], [0, 1], samples_per_span=2)


def test_asset_serialization_copy_bounds_support_and_alignment():
    from adsl.agents.analysis_geometry import _jsonable
    profiles = [rectangle(2, 3), rectangle(3, 2, (.25, .5)), rectangle(1, 1)]
    asset = loft(profiles, [0, 1, 3], axis='x', samples_per_span=3,
                 color=(.2, .4, .6), alpha=.7)
    # Existing Asset dictionaries keep ndarray xforms; the artifact exporter
    # applies _jsonable. Loft control data itself needs no custom encoder.
    raw = asset.to_dict()
    json.dumps(raw['primitives'][0]['params']['construction'])
    data = json.loads(json.dumps(_jsonable(raw)))
    construction = data['primitives'][0]['params']['construction']
    assert construction == dict(op='loft', profiles=[json.loads(json.dumps(p.to_dict())) for p in profiles],
        positions=[0, 1, 3], axis='x', interpolation='smooth', samples_per_span=3)
    assert data['primitives'][0]['color'] == [.2, .4, .6]
    assert data['primitives'][0]['alpha'] == .7
    vertices = mesh_of(asset).vertices.copy()
    matrix = translation_matrix((4, -2, 7)) @ rotation_matrix((1, 2, 3), 29) @ scaling_matrix((2, .5, 3))
    changed = transform_shape(asset.copy(), matrix)
    expected = vertices @ matrix[:3, :3].T + matrix[:3, 3]
    np.testing.assert_allclose(shape_aabb(changed), [expected.min(0), expected.max(0)])
    direction = np.array([2., -3., 1.])
    assert np.dot(shape_support(changed, direction), direction) == pytest.approx(np.max(expected @ direction))
    np.testing.assert_array_equal(mesh_of(asset).vertices, vertices)
    target = Cube((2, 2, 2), center=(10, 0, 0))
    aligned = align_anchors(changed, target, anchor='left', target_anchor='right', offset=(.5, 0, 0))
    assert shape_aabb(aligned)[0][0] == pytest.approx(shape_aabb(target)[1][0] + .5)
    np.testing.assert_array_equal(mesh_of(asset).vertices, vertices)


def test_missing_geometry_extra_is_lazy_and_explicit(monkeypatch):
    original = builtins.__import__
    def unavailable(name, *args, **kwargs):
        if name == 'manifold3d':
            raise ImportError('missing test dependency')
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', unavailable)
    from adsl.core import Polygon as Profile, loft as operation
    profile = Profile([(0, 0), (2, 0), (0, 1)])
    with pytest.raises(ImportError, match=r'adsl-core\[geometry\]'):
        operation([profile, profile], [0, 1])


def test_clean_public_import_does_not_load_native_geometry():
    result = subprocess.run([sys.executable, '-c',
        'import sys; from adsl.core import Polygon, loft; '
        'assert "manifold3d" not in sys.modules; Polygon([(0,0),(1,0),(0,1)])'],
        capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(os.environ.get('ADSL_TEST_GEOMETRY_REAL') != '1', reason='native Blender opt-in')
@pytest.mark.parametrize('operation', ['union', 'difference'])
def test_real_loft_boolean_compatibility(operation):
    bpy = pytest.importorskip('bpy')
    from adsl.core import Cylinder, boolean_difference, boolean_union
    from adsl.core.export.mesh64 import evaluate_shape
    bpy.ops.object.select_all(action='SELECT')
    bpy.ops.object.delete(use_global=False)
    body = loft([rectangle(), rectangle()], [0, 4])
    if operation == 'union':
        scene = boolean_union(body, Cube((2, 2, 2), center=(1.5, 0, 2)))
        expected = 22
    else:
        scene = boolean_difference(body, Cylinder(.25, p0=(0, 0, -1), p1=(0, 0, 5)))
        expected = 16 - 4 * .5 * 32 * .25**2 * math.sin(2*math.pi/32)
    result = evaluate_shape(scene)
    mesh = result.pieces[0].world_mesh()
    assert_solid(mesh)
    assert mesh.volume == pytest.approx(expected, rel=1e-5)
    assert not [o for o in bpy.data.objects if o.type == 'MESH']
    assert mesh.face_attributes['material'].shape == (len(mesh.faces),)
    assert all(result.materials[int(i)]['base_color'][:3] == [1., 1., 1.]
               for i in set(mesh.face_attributes['material']))


@pytest.mark.skipif(os.environ.get('ADSL_TEST_GEOMETRY_REAL') != '1', reason='native Blender opt-in')
def test_real_loft_executor_glb_records_and_eight_views(tmp_path):
    from adsl.agents.utils.execution import execute_asset_source
    source = tmp_path / 'source.py'
    source.write_text('''from adsl.core import *
import math
def ellipse(w, h, c=0):
    return Polygon([(w*math.cos(2*math.pi*j/32), c+h*math.sin(2*math.pi*j/32)) for j in range(32)])
scene = loft(
    [ellipse(.5,.65), ellipse(.85,.95,.1), ellipse(.55,.7), ellipse(.7,.85,.08), ellipse(.25,.35)],
    positions=[0,.8,1.6,2.4,3.0], axis="x", interpolation="smooth", samples_per_span=8,
)
''')
    started = time.monotonic()
    execution = execute_asset_source(source, tmp_path / 'exec', render=False, export_urdf=False,
                                     fixed_assembly=None)
    execution_seconds = time.monotonic() - started
    started = time.monotonic()
    loaded = trimesh.load(execution.glb_path, force='scene')
    assert loaded.geometry and all(len(mesh.faces) > 0 for mesh in loaded.geometry.values())
    # Blender glTF axis conversion swaps Y/Z; sorted dimensions remain invariant.
    np.testing.assert_allclose(sorted(loaded.extents), sorted([3., 1.7, 1.9]), atol=2e-6)
    source_index = json.loads(execution.source_index_path.read_text())
    calls = [node for node in source_index['source_nodes'] if node['name'] == 'loft']
    assert len(calls) == 1 and calls[0]['span']['start_line'] == 5 and calls[0]['span']['end_line'] == 8
    analysis = json.loads(execution.analysis_geometry_path.read_text())
    serialized = json.dumps(analysis)
    assert '"op": "loft"' in serialized and '"vertices"' in serialized and '"triangles"' in serialized
    readback_seconds = time.monotonic() - started
    command = [sys.executable, '-m', 'adsl.tools.render', '--glb-path', str(execution.glb_path),
        '--output-dir', str(tmp_path / 'views'), '--num-camera-per-layer', '8', '--view-layout', 'review_eight',
        '--material-mode', 'neutral', '--width', '512', '--height', '512', '--render-samples', '32',
        '--render-threads', '4', '--background', 'gray']
    started = time.monotonic()
    result = subprocess.run(command, env=dict(os.environ, ADSL_RENDER_ENGINE='CYCLES',
        CUDA_VISIBLE_DEVICES='', LIBGL_ALWAYS_SOFTWARE='1'), capture_output=True, text=True, timeout=300)
    render_seconds = time.monotonic() - started
    (tmp_path / 'render.log').write_text(result.stdout + '\n' + result.stderr)
    assert result.returncode == 0, result.stderr
    images = sorted((tmp_path / 'views').glob('*.png'))
    assert len(images) == 8
    (tmp_path / 'native_smoke.json').write_text(json.dumps(dict(
        execution='PASS', glb_readback='PASS', rendering='PASS',
        execution_seconds=execution_seconds, readback_seconds=readback_seconds, render_seconds=render_seconds,
        glb=str(execution.glb_path), images=[str(path) for path in images], render_command=command,
        control_sections=5, ring_vertices=32, samples_per_span=8,
        triangles=sum(len(mesh.faces) for mesh in loaded.geometry.values()),
        checkers=[], export_urdf=False, device='CPU'), indent=2))
