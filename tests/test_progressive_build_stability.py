from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import numpy as np
import trimesh

SCRIPT = Path(__file__).parents[1] / "experiments/progressive_build_stability/analyze.py"
SPEC = importlib.util.spec_from_file_location("progressive_build_stability", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
MODULE = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def geometry(name: str, mesh: trimesh.Trimesh):
    return MODULE.FINAL.CollisionGeometry(name, "mesh", mesh, np.eye(4), {}, None)


def test_half_height_box_is_capped_and_has_half_volume():
    mesh = trimesh.creation.box(extents=(2.0, 2.0, 2.0))
    mesh.apply_translation([0.0, 0.0, 1.0])
    result = MODULE.clip_below_height([geometry("box", mesh)], 1.0)
    assert result["failures"] == []
    assert result["geometries"][0].mesh.is_watertight
    assert np.isclose(result["geometries"][0].mesh.volume, 4.0)


def test_height_grid_includes_uniform_and_geometry_events():
    low = trimesh.creation.box(extents=(1, 1, 1))
    low.apply_translation([0, 0, 0.5])
    high = trimesh.creation.box(extents=(1, 1, 0.2))
    high.apply_translation([0, 0, 1.9])
    heights, events = MODULE.height_samples([geometry("low", low), geometry("high", high)], 0.25)
    assert len(heights) >= 6
    assert any(any(value.startswith("geometry_birth:high") for value in row) for row in events.values())
    assert any("completed_build" in row for row in events.values())


def test_top_heavy_geometry_needs_adhesion():
    base = trimesh.creation.box(extents=(1, 1, 0.2))
    base.apply_translation([0, 0, 0.1])
    top = trimesh.creation.box(extents=(1, 1, 1))
    top.apply_translation([1.2, 0, 1.0])
    items = [geometry("base", base), geometry("top", top)]
    support = MODULE.FINAL.support_region(items)
    com, info = MODULE.FINAL.weighted_com(items, "solid")
    models = {"uniform_solid": {**info, **MODULE.FINAL.margin_metrics(com, support)},
              "uniform_shell": {"available": False}}
    assert MODULE.geometry_assessment(support, models)["verdict"] == "UNSTABLE"
    assert MODULE.adhesion_demand(support, models)["normalized_gravity_overturning_demand"] > 0


def test_topple_threshold_is_strictly_greater_than_25_degrees():
    assert MODULE.FINAL.exceeds_topple_threshold(25.0) is False
    assert MODULE.FINAL.exceeds_topple_threshold(np.nextafter(25.0, np.inf)) is True
