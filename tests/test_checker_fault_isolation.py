"""Controller-only regressions: fake processes/results, no geometry or solver."""
import asyncio
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from adsl.agents.checkers import CheckerRun, run_checkers
from adsl.agents.models import (
    AnalysisContext, CheckerFinding, CheckerResult, CheckerSpec,
    EngineeringCriticDecision, ImageCriticDecision, MetricEvidence,
    ObjectPlan, ObjectRequest, RepairPolicy,
)
from adsl.agents.repair_policy import assess_candidate
from adsl.agents.service import ObjectWorkflow, _checker_evidence, _CandidateOutcome
from adsl.agents.utils.execution import ExecutionResult


def result(name, status, tilt=None):
    findings = [] if tilt is None else [CheckerFinding(
        finding_id=f"{name}:tilt", rule_id="TILT", category="physical_violation",
        repairability="geometry",
        metric=MetricEvidence(name="tilt", value=tilt, threshold=25, comparator="le"),
    )]
    return CheckerResult(checker=name, status=status, summary=status, findings=findings)


def run(root, name, status, tilt=None):
    return CheckerRun(CheckerSpec(name=name, command=["unused"]),
                      result(name, status, tilt), root / "checkers" / name, ())


@pytest.mark.parametrize("mode,stage", [
    ("crash", "execution"), ("malformed", "result_parsing"),
    ("missing", "result_loading"), ("startup", "setup_or_execution"),
    ("config", "setup_or_execution"),
])
def test_checker_fault_does_not_abort_independent_checks(tmp_path, mode, stage):
    script = tmp_path / "fake.py"
    script.write_text('''import json, pathlib, sys
out = pathlib.Path(sys.argv[1]); mode = sys.argv[2]
if mode == 'crash': raise RuntimeError('simulated crash')
if mode == 'missing': raise SystemExit(0)
if mode == 'malformed':
    (out/'result.json').write_text('not JSON ' + 'RAW_LOG'*10000)
else:
    (out/'result.json').write_text(json.dumps(dict(checker='standing',status='PASS',summary='ok')))
''')
    command = [sys.executable, str(script), "{output_dir}", mode]
    if mode == "startup":
        command = [str(tmp_path / "nonexistent-executable")]
    broken = CheckerSpec(name="broken", command=command,
                         prepend_environment={"PATH": ["{unknown}"]} if mode == "config" else {})
    standing = CheckerSpec(name="standing", command=[sys.executable, str(script), "{output_dir}", "pass"])
    execution = ExecutionResult(tmp_path, tmp_path / "scene.glb", None, (), "", "")
    runs = run_checkers([broken, standing], execution=execution,
                       source_path=tmp_path / "source.py", round_root=tmp_path / "round")
    assert [row.result.status for row in runs] == ["ERROR", "PASS"]
    assert runs[0].result.violations[0]["stage"] == stage
    assert runs[0].result.findings[0].category == "infrastructure_error"
    assert len(runs[0].result.summary) < 250
    assert (runs[0].output_dir / "result.json").is_file()


@pytest.mark.parametrize("status", ["PASS", "FAIL", "ERROR", "INDETERMINATE"])
def test_topology_dependency_is_order_independent(tmp_path, monkeypatch, status):
    import adsl.agents.checkers as checkers
    calls = []

    def fake(spec, **kwargs):
        calls.append(spec.name)
        return run(kwargs["round_root"], spec.name, status if spec.name == "topology" else "PASS")

    monkeypatch.setattr(checkers, "run_checker", fake)
    specs = [CheckerSpec(name=name, command=["unused"]) for name in ("fea", "standing", "topology")]
    rows = run_checkers(specs, execution=None, source_path=tmp_path / "source.py", round_root=tmp_path)
    assert [row.spec.name for row in rows] == ["fea", "standing", "topology"]
    assert rows[1].result.status == "PASS"
    assert calls[0] == "topology"
    if status == "PASS":
        assert "fea" in calls
        assert rows[0].result.status == "PASS"
    else:
        assert "fea" not in calls
        assert rows[0].result.status == "INDETERMINATE"
        assert rows[0].result.violations[0]["dependency_status"] == status
        assert rows[0].result.artifacts["dependency_report"].endswith("topology/result.json")


@pytest.mark.parametrize("before,after", [("ERROR", "ERROR"), ("INDETERMINATE", "INDETERMINATE"),
                                         ("ERROR", "INDETERMINATE"), ("INDETERMINATE", "ERROR")])
def test_partial_improvement_can_be_accepted_with_unavailable_check(before, after):
    decision = assess_candidate(
        [result("standing", "FAIL", 40), result("fea", before)],
        [result("standing", "FAIL", 30), result("fea", after)],
        target_finding_ids=["standing:tilt"], appearance_approved=True, policy=RepairPolicy())
    assert decision.accepted
    assert decision.unavailable_checks == ["fea"]
    assert decision.target_improvements


@pytest.mark.parametrize("status", ["FAIL", "ERROR", "INDETERMINATE"])
def test_pass_cannot_regress_even_when_other_target_improves(status):
    decision = assess_candidate(
        [result("standing", "FAIL", 40), result("fea", "PASS")],
        [result("standing", "PASS", 20), result("fea", status)],
        target_finding_ids=["standing:tilt"], appearance_approved=True, policy=RepairPolicy())
    assert not decision.accepted
    assert f"fea: PASS -> {status}" in decision.regressions


@pytest.mark.parametrize("mode", ["unchanged", "skip_target", "recover_only", "missing", "visual_regression"])
def test_unavailability_is_not_an_improvement(mode):
    before = [result("standing", "FAIL", 40), result("fea", "ERROR", 100)]
    after = [result("standing", "FAIL", 40), result("fea", "ERROR", 0)]
    if mode == "skip_target":
        after[0] = result("standing", "INDETERMINATE", 0)
    elif mode == "recover_only":
        after[1] = result("fea", "PASS", 0)
    elif mode == "missing":
        after = after[:1]
    elif mode == "visual_regression":
        after[0] = result("standing", "PASS", 20)
    decision = assess_candidate(before, after, target_finding_ids=["standing:tilt", "fea:tilt"],
                                appearance_approved=mode != "visual_regression", policy=RepairPolicy())
    assert not decision.accepted


def test_unavailable_feedback_is_short_and_not_a_geometry_target(tmp_path):
    bad = run(tmp_path, "fea", "ERROR", 100)
    bad.result.summary = "Solver crashed\n" + "RAW_LOG" * 10000
    bad.result.violations = [{"stage": "solver", "code": "SOLVER_FAILED", "message": "RAW_LOG" * 10000}]
    payload = _checker_evidence([bad, run(tmp_path, "standing", "FAIL", 40)], workspace=tmp_path)
    assert "RAW_LOG" not in json.dumps(payload)
    assert len(json.dumps(payload)) < 2500
    assert [row["finding_id"] for row in payload["typed_findings"]] == ["standing:tilt"]
    assert payload["checker_summary"][0]["stage"] == "solver"
    assert payload["checker_summary"][0]["geometry_repair_allowed"] is False
    assert payload["checker_summary"][0]["result_ref"] == "checkers/fea/result.json"


def workflow_fixture(tmp_path, monkeypatch, rows, *, appearance=True, proposals=None):
    import adsl.agents.service as service
    source = tmp_path / "source.py"
    source.write_text("scene = None\n")
    asset = tmp_path / "mock_execution"
    asset.mkdir()
    glb = asset / "scene.glb"
    glb.write_bytes(b"mock model")
    execution = ExecutionResult(asset, glb, None, (), "", "")
    monkeypatch.setattr(service, "execute_asset_source", lambda *args, **kwargs: execution)
    monkeypatch.setattr(service, "run_checkers", lambda *args, **kwargs: rows)
    monkeypatch.setattr(ObjectWorkflow, "_require_tool_event", lambda *args: None)
    manifest = {}
    roles = []

    async def fake_run(**kwargs):
        roles.append(kwargs["role"])
        if kwargs["role"].startswith("engineering-critic"):
            output = EngineeringCriticDecision(approved=False, observations=[], repair_proposals=proposals or [])
        elif kwargs["role"].startswith("code-critic"):
            from adsl.agents.models import CodeCriticDecision
            output = CodeCriticDecision(approved=appearance, observations=[], required_changes=["fix appearance"])
        else:
            output = ImageCriticDecision(approved=appearance, observations=[])
        return SimpleNamespace(final_output=output)

    runtime = SimpleNamespace(agent=lambda **kwargs: None, run=AsyncMock(side_effect=fake_run),
                              usage=SimpleNamespace(update_manifest=lambda **kw: manifest.update(kw), totals=lambda: None))
    workflow = ObjectWorkflow()
    workflow._repair = AsyncMock()
    request = ObjectRequest(requirement="chair", workspace=tmp_path, task_id="mock",
                            max_rounds=3, checker_specs=tuple(row.spec for row in rows))
    plan = ObjectPlan(object_name="chair", components=[], relations=[], critic_checklist=[])
    return workflow, dict(runtime=runtime, request=request, workspace=tmp_path,
                          source_path=source, mode="text", plan=plan), manifest, roles, execution


@pytest.mark.parametrize("status", ["ERROR", "INDETERMINATE", "FAIL", "PASS"])
def test_no_actionable_feedback_saves_model_and_verification(tmp_path, monkeypatch, status):
    rows = [run(tmp_path, "topology", status), run(tmp_path, "standing", "PASS")]
    workflow, kwargs, manifest, roles, _ = workflow_fixture(tmp_path, monkeypatch, rows)
    output = asyncio.run(workflow._iterate(**kwargs))
    assert output.glb_path.read_bytes() == b"mock model"
    assert output.approved == (status == "PASS")
    assert output.selected_round == 1
    assert roles == ["image-critic:round:1"]
    workflow._repair.assert_not_awaited()
    assert manifest["status"] == "completed"
    assert manifest["required_checkers_passed"] == (status == "PASS")
    saved = json.loads((tmp_path / "checker_results.json").read_text())
    assert saved["checker_statuses"] == {"topology": status, "standing": "PASS"}
    assert saved["unverified_checks"] == (["topology"] if status in {"ERROR", "INDETERMINATE"} else [])
    assert json.loads((tmp_path / "checkpoint.json").read_text())["stage"] == "completed"


def test_visual_loop_continues_when_checker_unavailable_and_budget_saves(tmp_path, monkeypatch):
    workflow, kwargs, manifest, roles, _ = workflow_fixture(
        tmp_path, monkeypatch, [run(tmp_path, "standing", "ERROR")], appearance=False)
    output = asyncio.run(workflow._iterate(**kwargs))
    assert not output.approved
    assert output.selected_round == 3
    assert workflow._repair.await_count == 2
    assert len([role for role in roles if role.startswith("image-critic")]) == 3
    assert not any(role.startswith("engineering-critic") for role in roles)
    assert manifest["finalization_reason"] == "round_budget_exhausted_with_unmet_gates"
    assert manifest["unverified_checks"] == ["standing"]


@pytest.mark.parametrize("outcome", ["improved", "budget", "no_proposal", "rejected"])
def test_engineering_loop_keeps_partial_result_and_stops_normally(tmp_path, monkeypatch, outcome):
    rows = [run(tmp_path, "standing", "FAIL", 40), run(tmp_path, "fea", "ERROR")]
    workflow, kwargs, manifest, roles, execution = workflow_fixture(tmp_path, monkeypatch, rows)
    after = [run(tmp_path, "standing", "PASS", 20), run(tmp_path, "fea", "ERROR")]
    attempts = ()
    if outcome == "budget":
        attempts = ({"stage": "repair_budget", "reason": "repair time budget reached"},)
    if outcome == "rejected":
        attempts = ({"candidate": "source.py", "accepted": False, "reason": "regression"},)
    workflow._attempt_engineering_candidates = AsyncMock(return_value=_CandidateOutcome(
        execution if outcome == "improved" else None, tuple(after), True, attempts))
    output = asyncio.run(workflow._iterate(**kwargs))
    assert not output.approved
    assert output.glb_path.exists()
    assert manifest["status"] == "completed"
    assert manifest["required_checkers_passed"] is False
    assert manifest["unverified_checks"] == ["fea"]
    assert any(role.startswith("engineering-critic") for role in roles)
    assert output.selected_round == (3 if outcome == "rejected" else 1)
    if outcome == "improved":
        assert manifest["checker_statuses"]["standing"] == "PASS"
    workflow._repair.assert_not_awaited()


@pytest.mark.parametrize("before,after,budget,accepted", [
    ("ERROR", "ERROR", False, True),
    ("INDETERMINATE", "INDETERMINATE", False, True),
    ("PASS", "ERROR", False, False),
    ("PASS", "FAIL", False, False),
    ("PASS", "INDETERMINATE", False, False),
    ("ERROR", "ERROR", True, False),
])
def test_actual_candidate_path_only_copies_an_accepted_source(tmp_path, monkeypatch, before, after, budget, accepted):
    import adsl.agents.service as service
    from adsl.agents.repair_policy import ScopeValidation
    root = tmp_path / "round"
    candidate = root / "candidates/01"
    candidate.mkdir(parents=True)
    source = tmp_path / "source.py"
    source.write_text("baseline source")
    candidate_source = candidate / "source.py"
    candidate_source.write_text("candidate source")
    glb = candidate / "scene.glb"
    glb.write_bytes(b"candidate model")
    execution = ExecutionResult(candidate, glb, None, (), "", "")
    proposal = SimpleNamespace(proposal_id="repair-standing", finding_ids=["standing:tilt"],
                               model_dump=lambda: {"proposal_id": "repair-standing"})
    captured_findings = []
    controller = SimpleNamespace(
        budget_error=lambda: "maximum total candidate budget reached" if budget else None,
        normalize_proposal=lambda p: (p, []),
        prepare_candidate=lambda *args: (candidate, candidate_source, "fingerprint"),
        record=Mock(),
    )

    def make_controller(**kwargs):
        captured_findings.extend(kwargs["findings"])
        return controller

    monkeypatch.setattr(service, "RepairController", make_controller)
    monkeypatch.setattr(service, "validate_patch_scope", lambda *args, **kwargs: ScopeValidation(valid=True))
    monkeypatch.setattr(service, "execute_asset_source", lambda *args, **kwargs: execution)
    monkeypatch.setattr(service, "run_checkers", lambda *args, **kwargs: [
        run(candidate, "standing", "FAIL", 30), run(candidate, "fea", after)])
    context = AnalysisContext(source_sha256="a", geometry_sha256="b", checker_specs_sha256="c")
    monkeypatch.setattr(service, "build_analysis_context", lambda **kwargs: context)
    runtime = SimpleNamespace(run=AsyncMock(return_value=SimpleNamespace(
        final_output=ImageCriticDecision(approved=True, observations=[]))))
    workflow = ObjectWorkflow()
    workflow._repair = AsyncMock()
    output = asyncio.run(workflow._attempt_engineering_candidates(
        runtime=runtime, request=SimpleNamespace(requirement="chair", repair_policy=RepairPolicy(), checker_specs=(), image_paths=()),
        workspace=tmp_path, source_path=source, round_root=root, round_number=1,
        plan=SimpleNamespace(model_dump=lambda: {}), repairer=None, image_critic=None, code_critic=None,
        baseline_execution=execution, baseline_runs=[run(root, "standing", "FAIL", 40), run(root, "fea", before, 100)],
        baseline_analysis_context=context, source_index=object(),
        engineering_decision=SimpleNamespace(repair_proposals=[proposal]), code_decision=None))
    assert (output.execution is not None) == accepted
    assert source.read_text() == ("candidate source" if accepted else "baseline source")
    assert [finding.finding_id for finding in captured_findings] == ["standing:tilt"]
    if budget:
        assert output.attempts[0]["stage"] == "repair_budget"
        workflow._repair.assert_not_awaited()
        runtime.run.assert_not_awaited()
    else:
        runtime.run.assert_awaited_once()  # Visual critic is still mandatory.
        saved = json.loads((candidate / "decision.json").read_text())
        assert saved["accepted"] == accepted
        if after in {"ERROR", "INDETERMINATE"}:
            assert saved["unavailable_checks"] == ["fea"]


def test_final_verification_belongs_to_accepted_candidate(tmp_path, monkeypatch):
    rows = [run(tmp_path, "standing", "FAIL", 40), run(tmp_path, "fea", "ERROR")]
    workflow, kwargs, manifest, _, execution = workflow_fixture(tmp_path, monkeypatch, rows)
    after = [run(tmp_path, "standing", "PASS", 20), run(tmp_path, "fea", "PASS")]
    workflow._attempt_engineering_candidates = AsyncMock(return_value=_CandidateOutcome(
        execution, tuple(after), True, ()))
    output = asyncio.run(workflow._iterate(**kwargs))
    assert output.approved
    assert manifest["required_checkers_passed"] is True
    assert manifest["checker_statuses"] == {"standing": "PASS", "fea": "PASS"}
    assert manifest["unverified_checks"] == []
