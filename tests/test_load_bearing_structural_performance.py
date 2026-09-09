import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest


PATH = Path(__file__).parents[1] / "experiments" / "load_bearing_structural_performance" / "analyze.py"
SPEC = importlib.util.spec_from_file_location("load_bearing_analysis", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_cantilever_formula():
    result = MODULE.cantilever_theory(3e9, 1.0, 0.1, 0.1, 100.0)
    assert result["tip_displacement_m"] == pytest.approx(0.001333333333333333)
    assert result["root_bending_stress_pa"] == pytest.approx(6e5)


def test_euler_formula():
    expected = np.pi ** 2 * 3e9 * (0.05 * 0.05 ** 3 / 12.0)
    assert MODULE.euler_buckling_load(3e9, 1.0, 0.05, 0.05) == pytest.approx(expected)


def test_mesh_convergence_uses_medium_to_fine_changes():
    def level(name, scale):
        analyses = {}
        for load in ("self_weight", "functional"):
            analyses[load] = {"max_displacement_m": 1.0 * scale,
                              "max_von_mises_pa": 2.0 * scale,
                              "first_positive_buckling_factor": 3.0 * scale}
        return {"mesh_level": name, "status": "SOLVED", "analyses": analyses}
    result = MODULE.mesh_convergence([level("medium", 0.95), level("fine", 1.0)])
    assert result["status"] == "PASSED"
    assert result["loads"]["functional"]["medium_to_fine_relative_change"]["displacement"] == pytest.approx(0.05)


def test_face_node_selection_is_semantic_and_surface_limited():
    nodes = {1: np.array([0.0, 0.0, 0.0]), 2: np.array([0.5, 0.5, 1.0]),
             3: np.array([0.5, 0.5, 0.8]), 4: np.array([2.0, 2.0, 1.0])}
    bounds = np.array([[0.0, 0.0, 0.0], [1.0, 1.0, 1.0]])
    assert MODULE.face_nodes(nodes, bounds, "top", 0.05) == [2]


def test_parse_gmsh_inp_ignores_surface_elements(tmp_path):
    path = tmp_path / "mesh.inp"
    path.write_text("*NODE\n1,0,0,0\n2,1,0,0\n3,0,1,0\n4,0,0,1\n5,.5,0,0\n6,.5,.5,0\n7,0,.5,0\n8,0,0,.5\n9,.5,0,.5\n10,0,.5,.5\n*ELEMENT, type=S6\n1,1,2,3,5,6,7\n*ELEMENT, type=C3D10, ELSET=Volume1\n2,1,2,3,4,5,6,7,8,9,10\n")
    nodes, elements = MODULE.parse_gmsh_inp(path)
    assert len(nodes) == 10
    assert elements == {2: [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]}


def test_exact_union_gate_rejects_disconnected_geometry():
    final = MODULE.FINAL
    a = final.CollisionGeometry("a", "box", __import__("trimesh").creation.box(), np.eye(4), {"size": [1, 1, 1]}, None)
    transform = np.eye(4); transform[0, 3] = 3.0
    b = final.CollisionGeometry("b", "box", __import__("trimesh").creation.box(), transform, {"size": [1, 1, 1]}, None)
    status, info, _ = MODULE.exact_union_gate([a, b])
    assert status == "INVALID_LOAD_PATH"
    assert info["union_component_count"] == 2
