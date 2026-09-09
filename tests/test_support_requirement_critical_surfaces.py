from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import pytest
import trimesh


SCRIPT = Path(__file__).parents[1] / "experiments" / "support_requirement_critical_surfaces" / "analyze.py"
SPEC = importlib.util.spec_from_file_location("support_requirement_critical_surfaces", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def test_overhang_threshold_uses_angle_from_horizontal():
    mesh = trimesh.creation.box([10, 10, 10])
    mesh.apply_translation([0, 0, 10])
    mask = MODULE.overhang_mask(mesh, 45.0, 0.2)
    downward = mesh.face_normals[:, 2] < -0.99
    assert np.array_equal(mask, downward)


def test_faces_on_build_plate_are_not_overhangs():
    mesh = trimesh.creation.box([10, 10, 10])
    mesh.apply_translation([0, 0, 5])
    assert not MODULE.overhang_mask(mesh, 45.0, 0.2).any()


def test_parse_gcode_distinguishes_support_interface_and_bridge(tmp_path):
    path = tmp_path / "sample.gcode"
    path.write_text(
        "M82\n;Z:1.0\nG1 X0 Y0 E0\n;TYPE:Support material\n"
        "G1 X10 Y0 E1\n;TYPE:Support material interface\nG1 X20 Y0 E2\n"
        ";TYPE:Bridge infill\nG1 X30 Y0 E3\n",
        encoding="utf-8",
    )
    parsed = MODULE.parse_gcode(path)
    assert sum(row["length_mm"] for row in MODULE.role_segments(parsed, "support material", exclude="interface")) == pytest.approx(10)
    assert sum(row["length_mm"] for row in MODULE.role_segments(parsed, "support material interface")) == pytest.approx(10)
    assert sum(row["length_mm"] for row in MODULE.role_segments(parsed, "bridge infill")) == pytest.approx(10)


def test_nominal_contact_maps_interface_below_downward_face():
    mesh = trimesh.creation.box([10, 10, 2])
    mesh.apply_translation([5, 5, 2])
    candidate = mesh.face_normals[:, 2] < -0.99
    layers = {0.6: MODULE.LineString([(0, 5), (10, 5)]).buffer(0.225, cap_style=2)}
    contact, areas, info = MODULE.map_nominal_contact(mesh, candidate, layers, 0.2, 0.2)
    assert contact.any()
    assert areas.sum() > 0
    assert info["z_window_mm"] == pytest.approx(0.5 + 1e-6)


def test_nominal_bottom_contact_maps_support_above_upward_face():
    mesh = trimesh.creation.box([10, 10, 2])
    mesh.apply_translation([5, 5, 1])
    layers = {2.4: MODULE.LineString([(0, 5), (10, 5)]).buffer(0.225, cap_style=2)}
    contact, areas, info = MODULE.map_nominal_bottom_contact(mesh, layers, 0.2, 0.2)
    assert contact.any()
    assert areas.sum() > 0
    assert info["support_layer_count"] == 1


def test_critical_mask_requires_collision_and_face_selector():
    mesh = trimesh.creation.box([2, 2, 2])
    tagged = [MODULE.TaggedMesh("chair/seat/slab", mesh, 0, len(mesh.faces))]
    mask, evidence = MODULE.critical_mask(
        tagged, [{"label": "seat", "collision_regex": "/seat/", "face": "top"}]
    )
    assert mask.sum() == 2
    assert evidence[0]["face_count"] == 2


def test_semantic_scale_and_print_cap_are_separate():
    bounds = np.array([[0.0, 0.0, 0.0], [1.0, 2.0, 3.0]])
    assert MODULE.scalar_for_measure(bounds, {"measure": "height", "target_m": 0.9}) == pytest.approx(300)
    assert MODULE.scalar_for_measure(bounds, {"measure": "length", "target_m": 2.0}) == pytest.approx(1000)

def test_export_overlay_replaces_texture_visuals_with_face_colors(tmp_path):
    mesh = trimesh.creation.box([2, 2, 2])
    mesh.visual = trimesh.visual.TextureVisuals(
        uv=np.zeros((len(mesh.vertices), 2), dtype=float)
    )
    overhang = np.zeros(len(mesh.faces), dtype=bool)
    critical = np.zeros(len(mesh.faces), dtype=bool)
    contact = np.zeros(len(mesh.faces), dtype=bool)
    overhang[0] = True
    contact[1] = True

    output = tmp_path / "surface_classes.ply"
    MODULE.export_overlay(mesh, output, overhang, critical, contact)
    loaded = trimesh.load(output, force="mesh", process=False)
    colors = np.asarray(loaded.visual.face_colors)[:, :3]

    assert [244, 159, 54] in colors.tolist()
    assert [220, 45, 45] in colors.tolist()
    assert [155, 155, 155] in colors.tolist()
