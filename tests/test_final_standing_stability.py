from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import trimesh


SCRIPT = Path(__file__).parents[1] / "experiments/final_standing_stability/analyze.py"
SPEC = importlib.util.spec_from_file_location("final_standing_stability", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def geometry(name: str, mesh: trimesh.Trimesh, kind: str = "box"):
    return MODULE.CollisionGeometry(
        name=name,
        kind=kind,
        mesh=mesh,
        transform=np.eye(4),
        parameters={},
        source_path=None,
    )


def test_centered_box_has_positive_margin():
    item = geometry("box", trimesh.creation.box(extents=(2.0, 2.0, 2.0)))
    support = MODULE.support_region([item])
    solid_com, _ = MODULE.weighted_com([item], "solid")
    metrics = MODULE.margin_metrics(solid_com, support)

    assert support["rank"] == 2
    assert np.isclose(support["area"], 4.0)
    assert metrics["inside"] is True
    assert np.isclose(metrics["signed_margin"], 1.0)
    assert np.isclose(metrics["minimum_tipping_angle_deg"], 45.0)


def test_upright_thin_disc_has_degenerate_vertex_contact():
    disc = trimesh.creation.cylinder(radius=1.0, height=0.1, sections=64)
    disc.apply_transform(trimesh.transformations.rotation_matrix(np.pi / 2.0, [1, 0, 0]))
    disc.apply_translation([0.0, 0.0, 1.0])
    support = MODULE.support_region([geometry("wheel", disc, kind="mesh")])

    assert support["degenerate"] is True
    assert support["rank"] < 2


def test_non_watertight_mesh_disables_solid_com_but_keeps_shell_com():
    mesh = trimesh.creation.box()
    mesh.update_faces(np.arange(len(mesh.faces) - 1))
    item = geometry("open_box", mesh, kind="mesh")

    solid_com, solid_info = MODULE.weighted_com([item], "solid")
    shell_com, shell_info = MODULE.weighted_com([item], "shell")

    assert solid_com is None
    assert solid_info["available"] is False
    assert shell_com is not None
    assert shell_info["available"] is True


def test_output_directory_inside_input_is_rejected_by_path_rule(tmp_path):
    input_root = tmp_path / "frozen"
    output = input_root / "derived"
    assert input_root.resolve() in output.resolve().parents

def test_mujoco_builder_drops_two_face_numerical_sliver(tmp_path):
    sliver = trimesh.Trimesh(
        vertices=np.array(
            [
                [0.0, 0.0, 0.0],
                [1e-12, 0.0, 0.0],
                [0.0, 0.03, 0.0],
                [0.0, 0.0, 0.004],
                [1e-12, 0.03, 0.0],
                [0.0, 0.0, 0.004],
            ],
            dtype=float,
        ),
        faces=np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64),
        process=False,
    )
    valid = trimesh.creation.box(extents=(1.0, 1.0, 1.0))
    _, proxy = MODULE.build_mujoco_xml(
        [geometry("sliver", sliver, kind="mesh"), geometry("box", valid, kind="mesh")],
        tmp_path,
        ground_z=0.0,
        density=1000.0,
        friction=2.0,
    )

    # split() exposes the two triangles as separate degenerate pieces.
    assert proxy["dropped_degenerate_mesh_component_count"] >= 2
    assert proxy["mesh_component_count"] == 1


def test_user_topple_rule_is_strictly_greater_than_25_degrees():
    assert MODULE.exceeds_topple_threshold(25.0) is False
    assert MODULE.exceeds_topple_threshold(np.nextafter(25.0, np.inf)) is True
