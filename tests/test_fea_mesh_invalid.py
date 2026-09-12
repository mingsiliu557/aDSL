"""Small mesh/state fixtures only; no Gmsh, CalculiX, rendering or API calls."""
import asyncio
import json
from unittest.mock import Mock

import numpy as np
import pytest

from adsl.agents.checkers import CheckerRun
from adsl.agents.models import CheckerResult, CheckerSpec, RepairPolicy
from adsl.agents.repair_policy import assess_candidate
from adsl.agents.service import _actionable_findings, _checker_evidence
from experiments.load_bearing_structural_performance import analyze as fea
from experiments.workflow_checkers.run import enrich_result, fea_result
from test_checker_fault_isolation import result, run, workflow_fixture


def tetra(height=1.0):
    corners = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.], [0., 0., height]])
    edges = [(0, 1), (1, 2), (0, 2), (0, 3), (1, 3), (2, 3)]
    points = [*corners, *((corners[a] + corners[b]) / 2 for a, b in edges)]
    return {i + 1: p for i, p in enumerate(points)}, {17: list(range(1, 11))}


def invalid_result(tmp_path):
    raw = {"status": "MESH_INVALID", "scale": {"factor_m_per_scene_unit": 0.5},
           "mesh_levels": [{"mesh_level": "fine", "status": "MESH_INVALID",
                            "mesh_invalid": fea.mesh_invalid_report(*tetra(0), {
                                "minimum_scaled_jacobian": 0,
                                "invalid_jacobian_element_ids": [17]})}]}
    return enrich_result(fea_result(raw, tmp_path / "raw/result.json"))


@pytest.mark.parametrize("height,quality,bad", [(1, 1, False), (1e-20, 1e-20, False),
                                               (0, 1, True), (-1, 1, True), (1, -0.1, True)])
def test_validity_is_not_a_low_quality_cutoff(height, quality, bad):
    mesh = {"minimum_scaled_jacobian": quality,
            "invalid_jacobian_element_ids": [17] if quality <= 0 else []}
    report = fea.mesh_invalid_report(*tetra(height), mesh)
    assert (report is not None) == bad
    if bad:
        assert report["invalid_element_count"] == 1
        assert report["invalid_element_ids"] == [17]
        assert report["count_is_complete"]
        assert report["bounds_m"] is not None


def test_mesh_gate_prevents_solver_and_load_setup(tmp_path, monkeypatch):
    monkeypatch.setattr(fa := fea, "all_bounds", lambda _: np.array([[0, 0, 0], [1, 1, 1]]))
    monkeypatch.setattr(fa, "build_occ_mesh", lambda *args: {"mesh_path": "unused", "minimum_scaled_jacobian": 0})
    monkeypatch.setattr(fa, "parse_gmsh_inp", lambda _: tetra(0))
    solver = Mock(side_effect=AssertionError("must not call solver"))
    monkeypatch.setattr(fa, "run_ccx", solver)
    row = fa.analyze_mesh_level("x", tmp_path, [], 1, "fine", .04, {}, {}, tmp_path / "ccx", tmp_path, 1)
    assert row["status"] == "MESH_INVALID"
    assert row["mesh_invalid"]["invalid_element_ids"] == [17]
    solver.assert_not_called()


def test_mesh_feedback_is_actionable_localized_and_bounded(tmp_path):
    r = invalid_result(tmp_path)
    check = CheckerRun(CheckerSpec(name="fea", command=["unused"]), r, tmp_path, ())
    f = r.findings[0]
    assert r.status == "INDETERMINATE"
    assert f.category == "geometry_failure" and f.repairability == "geometry"
    assert f.region.frame == "fea_m" and f.region.kind == "aabb"
    assert r.analysis_context.source_to_analysis[0][0] == .5
    assert _actionable_findings(check) == [f]
    f.domain["log"] = "FULL_LOG" * 10000
    f.domain["invalid_element_ids"] = list(range(10000))
    payload = _checker_evidence([check], workspace=tmp_path, finding_ids={f.finding_id})
    assert "FULL_LOG" not in json.dumps(payload)
    assert len(json.dumps(payload)) < 3500
    assert payload["checker_summary"][0]["geometry_repair_allowed"]
    assert payload["typed_findings"][0]["key_values"]["invalid_element_count"] == 1
    assert "unverified" in payload["typed_findings"][0]["message"]


@pytest.mark.parametrize("after_status,improve_topology,expected", [
    ("INDETERMINATE", False, False), ("INDETERMINATE", True, True),
    ("PASS", False, True), ("FAIL", False, False), ("FAIL", True, False),
])
def test_mesh_candidate_acceptance(tmp_path, after_status, improve_topology, expected):
    before_fea = invalid_result(tmp_path)
    after_fea = before_fea if after_status == "INDETERMINATE" else result("fea", after_status)
    before = [result("topology", "FAIL", 40), result("standing", "PASS"), before_fea]
    after = [result("topology", "PASS" if improve_topology else "FAIL", 20 if improve_topology else 40),
             result("standing", "PASS"), after_fea]
    decision = assess_candidate(before, after, target_finding_ids=["topology:tilt", before_fea.findings[0].finding_id],
                                appearance_approved=True, policy=RepairPolicy())
    assert decision.accepted == expected
    if after_status == "FAIL":
        assert any("newly evaluated physical failure" in r for r in decision.regressions)


def test_mesh_no_executable_proposal_saves_unverified_model(tmp_path, monkeypatch):
    mesh = CheckerRun(CheckerSpec(name="fea", command=["unused"]), invalid_result(tmp_path), tmp_path / "fea", ())
    rows = [run(tmp_path, "topology", "PASS"), run(tmp_path, "standing", "PASS"), mesh]
    workflow, kwargs, manifest, roles, _ = workflow_fixture(tmp_path, monkeypatch, rows)
    output = asyncio.run(workflow._iterate(**kwargs))
    assert output.glb_path.is_file() and not output.approved
    assert any(r.startswith("engineering-critic") for r in roles)
    assert manifest["status"] == "completed"
    assert manifest["unverified_checks"] == ["fea"]
    assert not manifest["required_checkers_passed"]
    saved = json.loads((tmp_path / "checker_results.json").read_text())
    assert saved["results"][-1]["violations"][0]["code"] == "MESH_INVALID"
