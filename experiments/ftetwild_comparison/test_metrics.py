"""Tiny unit tests for standalone measurement, no meshing or production edits."""
import importlib.util
from pathlib import Path
import numpy as np

spec = importlib.util.spec_from_file_location('comparison', Path(__file__).with_name('run.py'))
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_signed_jacobian_and_volume():
    v = np.array([[0.,0,0], [1,0,0], [0,1,0], [0,0,1]])
    result, _, _ = module.tet_metrics(v, np.array([[0,1,2,3]]))
    assert result['valid'] and np.isclose(result['volume_m3'], 1/6)
    bad, _, _ = module.tet_metrics(v, np.array([[0,2,1,3]]))
    assert not bad['valid'] and bad['negative_jacobian'] == 1
    v[3] = [1,1,0]
    flat, _, _ = module.tet_metrics(v, np.array([[0,1,2,3]]))
    assert not flat['valid'] and flat['zero_jacobian'] == 1


def test_solid_connectivity_is_shared_face_not_vertex():
    v = np.array([[0.,0,0], [1,0,0], [0,1,0], [0,0,1], [0,0,-1],
                  [-1,0,0], [0,-1,0], [0,0,-2]])
    joined, _, _ = module.tet_metrics(v, np.array([[0,1,2,3], [0,2,1,4]]))
    assert joined['face_connected_solid_components'] == 1
    point_only, _, _ = module.tet_metrics(v, np.array([[0,1,2,3], [0,5,7,6]]))
    assert point_only['face_connected_solid_components'] == 2


def test_c3d10_shared_midpoints_and_constant_jacobian():
    from sf03_fea import quadratic_mesh
    v = np.array([[0.,0,0], [1,0,0], [0,1,0], [0,0,1], [0,0,-1]])
    t = np.array([[0,1,2,3], [0,2,1,4]])
    points, cells, determinants = quadratic_mesh(v, t)
    assert len(points) == 14  # five corners + nine unique shared edges
    assert cells[0,4] == cells[1,6]  # same global edge 0--1
    assert np.allclose(determinants, 1.)
