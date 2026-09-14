"""Lightweight opt-in controller tests; no API, FEA or generation."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import os
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

from adsl.agents.models import CheckerResult, RepairPolicy, CheckerSpec
from adsl.agents.overhang_edit import compare_measurements, budget_remaining, reserve_attempt, opportunities
from adsl.agents.repair_policy import assess_candidate
from experiments.workflow_checkers.run import overhang_result
from experiments.support_requirement_critical_surfaces import analyze


def measured(area=100, error=0.01):
    return CheckerResult(checker="overhang", status="PASS", summary="measurement only", metrics={
        "overhang_area_mm2": area, "area_uncertainty_mm2": error,
        "nominal_contact_area_mm2": 10, "support_required": True,
        "measurement": {"scale": 10}, "print_extent_mm": [10, 10, 10],
        "overhang_regions": [{"area_mm2": area, "representative_point_mm": [0.5, 0.5, 1],
                              "bounds_mm": [[0, 0, 1], [1, 1, 1]]}]})


@pytest.mark.parametrize("status", ["SLICER_TIMEOUT", "EXTERIOR_TIMEOUT", "ANALYSIS_FAILED"])
def test_unavailable_never_becomes_zero(tmp_path, status):
    result = overhang_result({"status": status}, tmp_path / "raw.json", {})
    assert result.status in {"ERROR", "INDETERMINATE"}
    assert result.metrics["overhang_area_mm2"] is None
    assert result.metrics["support_required"] is None
    assert compare_measurements(measured(), result)["conclusion"] == "unevaluated"


@pytest.mark.parametrize("area,appearance,protection,accepted", [
    (99.9, True, "PASS", True),  # Much less than 1% is allowed beyond numerical error.
    (99.999, True, "PASS", False), (101, True, "PASS", False),
    (90, False, "PASS", False), (90, True, "FAIL", False),
    (90, True, "UNCONFIRMED", False),
])
def test_protected_area_selection(area, appearance, protection, accepted):
    decision = assess_candidate([measured()], [measured(area)], target_finding_ids=[],
        appearance_approved=appearance, policy=RepairPolicy(), overhang_optimization=True,
        protection={"status": protection})
    assert decision.accepted is accepted


def test_conditions_and_uncertainty_required():
    after = measured(80)
    after.metrics["measurement"] = {"scale": 11}
    assert compare_measurements(measured(), after)["conclusion"] == "unevaluated"
    after = measured(80, None)
    assert compare_measurements(measured(), after)["conclusion"] == "unevaluated"


def test_budget_counts_every_attempt_and_survives_resume(tmp_path):
    options = {"max_candidates": 2}
    assert reserve_attempt(tmp_path, options, "initial_patch")
    assert reserve_attempt(tmp_path, options, "debugger_patch")
    assert not reserve_attempt(tmp_path, dict(options), "visual_patch")
    assert budget_remaining(tmp_path, options) == 0


def test_opt_in_success_measurement_reaches_engineering_without_fail(tmp_path, monkeypatch):
    from test_overhang_candidate_isolation import fixture, run_case
    f = fixture(tmp_path, monkeypatch, actions=(90,), budget=1)
    run_case(f)
    assert any(role.startswith("engineering-critic") for role, _ in f.calls)
    assert f.manifest["required_checkers_passed"] is False


def test_default_does_not_optimize_success(tmp_path, monkeypatch):
    from test_checker_fault_isolation import workflow_fixture
    from adsl.agents.checkers import CheckerRun
    row = CheckerRun(CheckerSpec(name="overhang", command=["unused"], required=False), measured(),
                     tmp_path / "checkers/overhang", ())
    workflow, kwargs, _, roles, _ = workflow_fixture(tmp_path, monkeypatch, [row])
    asyncio.run(workflow._iterate(**kwargs))
    assert not any(r.startswith("engineering-critic") for r in roles)


def test_control_cannot_read_offline_measurement(tmp_path):
    from adsl.agents.tools import AgentToolContext
    control = tmp_path / "control"
    control.mkdir()
    context = AgentToolContext(workspace=control, source_path=control / "source.py")
    (tmp_path / "baseline.json").write_text('{"area": 123}')
    with pytest.raises(ValueError, match="escapes workspace"):
        context.resolve("../baseline.json")


def test_optional_optimization_continues_after_acceptance(tmp_path, monkeypatch):
    from test_overhang_candidate_isolation import fixture, run_case
    f = fixture(tmp_path, monkeypatch, actions=(90, 80), budget=2)
    _, book = run_case(f)
    assert len(book["attempts"]) == 2
    assert all(a["accepted"] for a in book["attempts"].values())
    assert budget_remaining(f.workspace, f.options) == 0
    assert f.manifest["required_checkers_passed"] is False


def test_opportunity_feedback_has_no_face_lists():
    value = measured()
    value.metrics["overhang_regions"][0]["global_face_ids"] = list(range(10000))
    feedback = opportunities(value)[0]
    assert not feedback.required
    assert feedback.category == "optimization_opportunity"
    assert "global_face_ids" not in feedback.model_dump_json()


def test_frozen_scale_not_renormalized(monkeypatch, tmp_path):
    mesh = trimesh.creation.box([2, 3, 4])
    monkeypatch.setattr(analyze.FINAL, "parse_urdf", lambda p: SimpleNamespace(robot_name="box"))
    monkeypatch.setattr(analyze.FINAL, "state_values", lambda p: {"initial": {}})
    monkeypatch.setattr(analyze.FINAL, "collision_geometries", lambda *args: [SimpleNamespace(name="box", transformed_mesh=lambda: mesh)])
    case = {"semantic_scale": {"measure": "height", "target_m": 0.1},
            "frozen_measurement": {"scale_mm_per_source_unit": 10}}
    _, scale = analyze.load_print_meshes(tmp_path, case, {"max_print_extent_mm": 180, "bed_margin_mm": 10})
    assert scale["print_extent_mm"] == [20, 30, 40]


def test_inclined_face_and_horizontal_cantilever():
    vertices = [[0, 0, 10], [10, 0, 10], [0, 10, 15]]
    mesh = trimesh.Trimesh(vertices=vertices, faces=[[0, 2, 1]], process=False)
    assert analyze.overhang_mask(mesh, 45, 0.2).all()
    assert analyze.area_uncertainty(mesh, {"overhang_threshold_from_horizontal_deg": 45, "layer_height_mm": .2})["bound_mm2"] > 0


def test_exterior_timeout_is_not_valid_fallback(tmp_path, monkeypatch):
    import subprocess
    box = trimesh.creation.box()
    monkeypatch.setattr(analyze, "load_print_meshes", lambda *args: ([analyze.TaggedMesh("box", box, 0, 12)], {}))
    def timeout(*args):
        raise subprocess.TimeoutExpired("union", 1)
    monkeypatch.setattr(analyze, "detector_exterior", timeout)
    result = analyze.analyze_case("x", tmp_path, tmp_path / "out", {
        "cases": {"x": {"exterior_method": "blender_exact_union"}}, "profile": {}}, Path("unused"), Path("unused"), 1)
    assert result["status"] == "EXTERIOR_TIMEOUT"
    assert "overhang" not in result


@pytest.mark.skipif(os.environ.get("ADSL_OVERHANG_SMOKE") != "1", reason="explicit local Blender smoke only")
def test_real_blender_exterior_excludes_internal_overlap(tmp_path):
    a = trimesh.creation.box([10, 10, 10])
    b = a.copy()
    b.apply_translation([5, 0, 0])
    rows, version = analyze.detector_exterior([
        analyze.TaggedMesh("a", a, 0, 12), analyze.TaggedMesh("b", b, 12, 24)], tmp_path, 30)
    exterior = analyze.combined_mesh(rows)
    assert exterior.is_volume
    assert exterior.volume == pytest.approx(1500)
    assert exterior.area == pytest.approx(800)
    assert exterior.area < a.area + b.area
    assert version


@pytest.mark.skipif(os.environ.get("ADSL_OVERHANG_SMOKE") != "1", reason="explicit local slicer smoke only")
def test_real_exterior_and_slicer_cantilever(tmp_path):
    from experiments.overhang_feedback.run_pilot import PROFILE, DEFAULT_PROFILE, DEFAULT_SLICER
    # A single, explicitly triangulated L-prism (no touching-shell union).
    polygon = [(10, 0), (20, 0), (20, 20), (40, 20), (40, 24), (10, 24)]
    vertices = [(x, y, z) for y in (10, 20) for x, z in polygon]
    front = [[0, 1, 2], [0, 2, 5], [2, 3, 4], [2, 4, 5]]
    faces = front + [[c+6, b+6, a+6] for a, b, c in front]
    for i in range(6):
        j = (i+1) % 6
        faces.extend([[i, i+6, j+6], [i, j+6, j]])
    fixture = trimesh.Trimesh(vertices=vertices, faces=faces)
    if fixture.volume < 0:
        fixture.invert()
    rows, _ = analyze.detector_exterior([analyze.TaggedMesh("fixture", fixture, 0, len(fixture.faces))], tmp_path, 30)
    mesh = analyze.combined_mesh(rows)
    stl, gcode = tmp_path / "fixture.stl", tmp_path / "fixture.gcode"
    mesh.export(stl)
    result = analyze.run_slicer(DEFAULT_SLICER, DEFAULT_PROFILE, stl, gcode, 30)
    assert result["returncode"] == 0
    analyzed = analyze.analyze_mesh(mesh, rows, {}, PROFILE, analyze.parse_gcode(gcode))
    assert analyzed["overhang"]["area_mm2"] > 0
    assert analyzed["slicer_support"]["required"]


@pytest.mark.skipif(os.environ.get("ADSL_OVERHANG_SMOKE") != "1", reason="explicit local Blender smoke only")
def test_real_touching_boolean_triangulation(tmp_path):
    fixture = analyze.fixture_mesh("cantilever")
    rows, _ = analyze.detector_exterior([analyze.TaggedMesh("touching", fixture, 0, len(fixture.faces))], tmp_path, 30)
    result = analyze.combined_mesh(rows)
    assert result.is_volume
    assert np.all(result.area_faces > 0)
    assert result.volume == pytest.approx(fixture.volume)


def test_missing_preparation_reports_reason_without_starting_edit(tmp_path, monkeypatch):
    from experiments.overhang_feedback.run_pilot import paired_phase
    case_root = tmp_path / "SF05"
    case_root.mkdir()
    (case_root / "prepare_status.json").write_text(json.dumps({"reason": "protected part unavailable"}))
    args = SimpleNamespace(output_root=tmp_path, phase="edit")
    assert paired_phase(args, [{"case_id": "SF05"}]) == 1
    report = json.loads((tmp_path / "edit_results.json").read_text())["cases"][0]
    assert report["status"] == "NOT_READY"
    assert report["reason"] == "protected part unavailable"
    assert "FileNotFoundError" not in report["reason"]


def test_source_index_protection_of_merged_mesh(tmp_path, monkeypatch):
    import hashlib
    from adsl.agents.overhang_edit import protection_check
    a = tmp_path / "original"
    b = tmp_path / "candidate"
    a.mkdir(); b.mkdir()
    (a / "source.py").write_text("scene = None\n")
    mesh = trimesh.creation.box()
    (a / "source_index.json").write_text(json.dumps({
        "source_sha256": hashlib.sha256((a / "source.py").read_bytes()).hexdigest(),
        "features": [{"semantic_path": "stool/seat/slab", "bounds": mesh.bounds.tolist(),
                      "frame": "authored_scene", "resolution": "complete"}]}))
    candidate = mesh.copy()
    monkeypatch.setattr(analyze.FINAL, "parse_urdf", lambda p: p)
    monkeypatch.setattr(analyze.FINAL, "state_values", lambda p: {"initial": {}})
    monkeypatch.setattr(analyze.FINAL, "collision_geometries", lambda p, _: [
        SimpleNamespace(name="merged", transformed_mesh=lambda: mesh if p.parent == a else candidate)])
    options = {"protection": {"surfaces": [{"label": "seat", "source_index_regex": "/seat/", "face": "all"}]}}
    assert protection_check(a / "scene.urdf", b / "scene.urdf", options)["status"] == "PASS"
    candidate.apply_translation([0, 0, .1])
    assert protection_check(a / "scene.urdf", b / "scene.urdf", options)["status"] == "FAIL"
    (a / "source.py").write_text("changed source")
    assert protection_check(a / "scene.urdf", b / "scene.urdf", options)["status"] == "UNCONFIRMED"
