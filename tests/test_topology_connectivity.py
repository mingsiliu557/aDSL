from __future__ import annotations

import importlib.util
from pathlib import Path
import sys

import pytest

pytest.importorskip("gmsh")

ANALYZER_PATH = (
    Path(__file__).resolve().parents[1]
    / "experiments"
    / "topology_connectivity"
    / "analyze.py"
)
SPEC = importlib.util.spec_from_file_location("test_topology_analyzer", ANALYZER_PATH)
assert SPEC is not None and SPEC.loader is not None
TOPOLOGY = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = TOPOLOGY
SPEC.loader.exec_module(TOPOLOGY)


def _cube(name: str, center: tuple[float, float, float]) -> dict[str, object]:
    x, y, z = center
    return {
        "semantic_path": f"Root/{name}",
        "name": name,
        "feature_id": f"feature:Root/{name}",
        "source_ids": [f"source:{name}"],
        "source_locations": ["L1-L1"],
        "bounds": [
            [x - 0.5, y - 0.5, z - 0.5],
            [x + 0.5, y + 0.5, z + 0.5],
        ],
        "attach_mode": "part",
        "boolean_mode": None,
        "primitives": [{
            "type": "cube",
            "params": {"scale": [1, 1, 1], "center": [0, 0, 0]},
            "xform": [
                [1, 0, 0, x],
                [0, 1, 0, y],
                [0, 0, 1, z],
                [0, 0, 0, 1],
            ],
        }],
        "children": [],
        "joints": [],
    }


def _manifest(*children: dict[str, object]) -> dict[str, object]:
    lowers = [child["bounds"][0] for child in children]
    uppers = [child["bounds"][1] for child in children]
    return {
        "version": 1,
        "source_sha256": "source",
        "geometry_sha256": "geometry",
        "root": {
            "semantic_path": "Root",
            "name": "Root",
            "feature_id": "feature:Root",
            "source_ids": ["source:root"],
            "source_locations": ["L1-L1"],
            "bounds": [
                [min(row[index] for row in lowers) for index in range(3)],
                [max(row[index] for row in uppers) for index in range(3)],
            ],
            "attach_mode": "root",
            "boolean_mode": None,
            "primitives": [],
            "children": list(children),
            "joints": [],
        },
    }


@pytest.mark.parametrize(
    ("center", "expected", "contact_kind"),
    [
        ((1.0, 0.0, 0.0), "PASS", "face_or_volume"),
        ((1.1, 0.0, 0.0), "FAIL", "gap"),
        ((1.0, 1.0, 1.0), "FAIL", "point_or_edge"),
    ],
)
def test_one_piece_contact_rule(
    center: tuple[float, float, float],
    expected: str,
    contact_kind: str,
) -> None:
    result = TOPOLOGY.analyze_manifest(
        _manifest(_cube("left", (0, 0, 0)), _cube("right", center)),
        {"mode": "one_piece", "numerical_tolerance_m": 1e-8},
    )
    assert result["status"] == expected
    assert result["contacts"][0]["contact_kind"] == contact_kind


def test_load_path_reports_paired_nearest_endpoints() -> None:
    result = TOPOLOGY.analyze_manifest(
        _manifest(
            _cube("support", (0, 0, 0)),
            _cube("seat", (0, 0, 1.1)),
        ),
        {
            "mode": "load_path",
            "loads": [{"name": "seat_load", "pattern": "seat"}],
            "numerical_tolerance_m": 1e-8,
        },
    )
    assert result["status"] == "FAIL"
    violation = next(
        row for row in result["violations"]
        if row["code"] == "LOAD_PATH_DISCONNECTED"
    )
    assert violation["relation"]["left_feature_id"]
    assert violation["relation"]["right_feature_id"]
    assert violation["relation"]["distance_m"] == pytest.approx(0.1)


def test_connected_load_path_builds_one_c3d10_mesh(tmp_path: Path) -> None:
    manifest = _manifest(
        _cube("support", (0, 0, 0)),
        _cube("seat", (0, 0, 1.0)),
    )
    result = TOPOLOGY.analyze_manifest(
        manifest,
        {
            "mode": "load_path",
            "loads": [{"name": "seat_load", "pattern": "seat"}],
        },
    )
    assert result["status"] == "PASS"
    mesh = TOPOLOGY.build_fused_mesh(
        manifest,
        result["active_part_paths"],
        scale=1.0,
        mesh_size=0.25,
        output=tmp_path / "mesh.inp",
    )
    assert Path(mesh["mesh_path"]).is_file()
    assert mesh["element_count"] > 0
    assert mesh["minimum_scaled_jacobian"] > 0


def test_load_path_ignores_unrelated_disconnected_decoration() -> None:
    decoration = _cube("decoration", (5, 0, 3))
    other_decoration = _cube("other_decoration", (7, 0, 3))
    decoration["primitives"] = [
        *decoration["primitives"],
        *other_decoration["primitives"],
    ]
    decoration["bounds"] = [[4.5, -0.5, 2.5], [7.5, 0.5, 3.5]]

    manifest = _manifest(
        _cube("support", (0, 0, 0)),
        _cube("seat", (0, 0, 1)),
        decoration,
    )
    load_path = TOPOLOGY.analyze_manifest(
        manifest,
        {
            "mode": "load_path",
            "loads": [{"name": "seat_load", "pattern": "seat"}],
        },
    )
    assert load_path["status"] == "PASS"
    assert "Root/decoration" not in load_path["active_part_paths"]
