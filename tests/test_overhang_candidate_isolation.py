"""Mock assets only. SF03 numbers are a regression fixture, not a rerun."""
import asyncio
import json
from dataclasses import replace
from types import SimpleNamespace

import pytest

from adsl.agents import service
from adsl.agents.checkers import CheckerRun
from adsl.agents.models import (ObjectRequest, ObjectPlan, CheckerSpec, EngineeringCriticDecision,
                                ImageCriticDecision, CodeCriticDecision, RepairProposal, RepairTarget)
from adsl.agents.overhang_edit import (budget_remaining, reserve_attempt, version_record, assert_version,
                                      edit_outcome, file_hash)
from adsl.agents.source_index import SourceIndex
from adsl.agents.utils.execution import ExecutionResult
from test_overhang_local_edit import measured


def fixture(tmp_path, monkeypatch, *, arm="feedback", actions=(90,), initial=False,
            original_area=100, original_appearance=True, budget=2, candidate_appearance=True):
    workspace = tmp_path / "work"
    workspace.mkdir()
    source_text = "class Frame:\n    value = 0\n\nscene = Frame()\n"
    source = workspace / "source.py"
    source.write_text(source_text)
    (workspace / "original_source.py").write_text(source_text)

    def assets(root, value):
        root.mkdir(parents=True, exist_ok=True)
        (root / "scene.glb").write_bytes(f"GLB:{value}".encode())
        (root / "scene.urdf").write_text(f"URDF:{value}")
        (root / "meshes").mkdir(exist_ok=True)
        (root / "meshes/m.stl").write_text(f"mesh:{value}")
        (root / "render").mkdir(exist_ok=True)
        image = root / "render/front.png"
        image.write_bytes(f"render:{value}".encode())
        index = root / "source_index.json"
        index.write_text(SourceIndex(source_path=str(source), source_sha256="mock",
                                     index_sha256="mock", root_feature_id="root").model_dump_json())
        return ExecutionResult(root, root / "scene.glb", root / "scene.urdf", (image,), "", "", index)

    original = assets(tmp_path / "original", 0)
    options = {"arm": arm, "original_urdf": str(original.urdf_path), "max_candidates": budget,
               "protection": {"allowed_classes": ["Frame"], "surfaces": ["frame"]}}
    (workspace / "runtime_config.json").write_text(json.dumps({"request": {"overhang_experiment": options}}))
    spec = CheckerSpec(name="overhang", required=False, command=["mock"])
    request = ObjectRequest(requirement="keep the frame and reduce overhang", workspace=workspace,
        task_id="mock", max_rounds=5, check_first=not initial,
        checker_specs=(spec,) if arm == "feedback" else (), overhang_experiment=options)
    plan = ObjectPlan(object_name="frame", components=[], relations=[], critic_checklist=[])
    calls, manifest, measures = [], {}, []
    actions = iter(actions)
    values = {0: original_area}

    def execute(src, root, **kwargs):
        value = int(src.read_text().split("value = ")[1].splitlines()[0])
        return assets(root, value)

    def check(specs, *, execution, source_path, round_root):
        assert arm == "feedback", "control must never evaluate overhang online"
        value = int(execution.glb_path.read_text().split(":")[1])
        measures.append(value)
        return [CheckerRun(spec, measured(values[value]), round_root / "checkers/overhang", ())]

    async def run(**kw):
        role = kw["role"]
        calls.append((role, kw["input"]))
        if role.startswith("coder"):
            action = next(actions)
            ctx = kw["context"]
            if isinstance(action, Exception):
                raise action
            if isinstance(action, str):
                return SimpleNamespace(final_output=action)
            value = len(values)
            values[value] = action
            ctx.source_path.write_text(source_text.replace("value = 0", f"value = {value}"))
            ctx.record("apply_patch", ctx.source_path)
            out = "edited"
        elif role.startswith("engineering"):
            kw["context"].record("read_file", kw["context"].source_path)
            out = EngineeringCriticDecision(approved=False, observations=[], repair_proposals=[RepairProposal(
                proposal_id=f"engineering_{len(calls)}", finding_ids=["overhang:optimization:0"], hypothesis="local change",
                target=RepairTarget(allowed_scopes=["Frame"]), action="reshape")])
        elif role.startswith("code"):
            kw["context"].record("read_file", kw["context"].source_path)
            out = CodeCriticDecision(approved=False, observations=["frame appearance needs repair"], required_changes=["fix frame"])
        else:
            original_review = kw["stage"].endswith(":0")
            out = ImageCriticDecision(approved=original_appearance if original_review else candidate_appearance,
                                      observations=["mock visual review"])
        return SimpleNamespace(final_output=out)

    runtime = SimpleNamespace(agent=lambda **kw: kw["name"], run=run,
        usage=SimpleNamespace(update_manifest=lambda **kw: manifest.update(kw), totals=lambda: None))
    monkeypatch.setattr(service, "execute_asset_source", execute)
    monkeypatch.setattr(service, "run_checkers", check)
    monkeypatch.setattr(service, "user_input", lambda text, images: text)
    monkeypatch.setattr(service, "protection_check", lambda *args: {"status": "PASS", "mock": True})
    # Mock source localization only; real scope validation and candidate acceptance remain enabled.
    monkeypatch.setattr(service, "localize_findings", lambda findings, *args, **kw:
                        (findings, SimpleNamespace(model_dump=lambda: {})))
    monkeypatch.setattr(service.RepairController, "normalize_proposal", lambda self, p: (p, []))
    workflow = service.ObjectWorkflow()
    kwargs = dict(runtime=runtime, request=request, workspace=workspace, source_path=source, mode="edit", plan=plan)
    return SimpleNamespace(workflow=workflow, kwargs=kwargs, workspace=workspace, calls=calls,
        manifest=manifest, measures=measures, original=original, options=options)


def run_case(f):
    result = asyncio.run(f.workflow._iterate(**f.kwargs))
    book = json.loads((f.workspace / "overhang_versions.json").read_text())
    assert "error" not in book, book.get("error")
    retained = book["versions"][book["retained"]]
    assert_version(retained)
    assert file_hash(result.source_path) == retained["files"][retained["source"]]
    for name in ("checker_results.json", "appearance_protection.json"):
        published = json.loads((f.workspace / name).read_text())
        assert published["version_id"] == book["retained"]
        assert published["record_hash"] == retained["record_hash"]
    return result, book


def test_sf03_gate_regression_keeps_original(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, original_area=6119.80, actions=(6645.29,),
                original_appearance=False, budget=1)
    result, book = run_case(f)
    attempt = book["attempts"]["attempt_0001"]
    assert attempt["origin"] == "gate_patch" and not attempt["accepted"]
    assert attempt["overhang_comparison"]["conclusion"] == "worsened"
    assert book["retained"] == "original"
    assert result.glb_path.read_text() == "GLB:0"
    assert f.manifest["checker_results"][0]["metrics"]["overhang_area_mm2"] == 6119.80
    assert not result.approved  # fallback is not a fictitious appearance approval
    assert book["versions"]["attempt_0001"]["reviews"]["appearance_approved"]


def test_improve_then_worsen_publishes_matching_best(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, actions=(90, 95))
    result, book = run_case(f)
    assert book["retained"] == "attempt_0001"
    assert result.glb_path.read_text() == "GLB:1"
    assert result.render_paths[0].read_text() == "render:1"
    assert f.manifest["checker_results"][0]["metrics"]["overhang_area_mm2"] == 90
    assert f.measures == [0, 1, 2]  # no remeasurement for final publication
    assert not book["attempts"]["attempt_0002"]["accepted"]


@pytest.mark.parametrize("action,status", [
    ('{"edit_action":"NO_CHANGE","reason":"protect the frame"}', "NO_CHANGE"),
    ("finished", "NO_PATCH_UNEXPLAINED"), (ValueError("apply_patch failed"), "TOOL_ERROR")])
def test_control_noop_and_errors_keep_assets(tmp_path, monkeypatch, action, status):
    f = fixture(tmp_path, monkeypatch, arm="control", initial=True, actions=(action,))
    result, book = run_case(f)
    assert book["attempts"]["attempt_0001"]["status"] == status
    assert book["retained"] == "original" and result.glb_path.exists()
    assert result.approved is False and f.measures == []
    assert budget_remaining(f.workspace, f.options) == 1
    prompts = json.dumps(f.calls)
    assert "overhang_area_mm2" not in prompts and "checker_evidence" not in prompts
    assert not any(role.startswith("engineering") for role, _ in f.calls)


def test_initial_and_gate_use_shared_budget(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, initial=True, original_appearance=False, actions=(110, 90), budget=2)
    _, book = run_case(f)
    assert [a["origin"] for a in book["attempts"].values()] == ["initial_edit", "gate_patch"]
    assert budget_remaining(f.workspace, f.options) == 0
    assert len([r for r, _ in f.calls if r.startswith("coder")]) == 2
    assert book["retained"] == "attempt_0002"


def test_initial_and_engineering_use_shared_budget(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, initial=True, actions=(90, 80), budget=2)
    _, book = run_case(f)
    assert [a["origin"] for a in book["attempts"].values()] == ["initial_edit", "engineering"]
    assert len((f.workspace / "edit_attempts.jsonl").read_text().splitlines()) == 2


def test_exhausted_initial_does_not_call_model(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, initial=True, budget=1)
    assert reserve_attempt(f.workspace, f.options, "initial_edit", attempt_id="existing")
    result, book = run_case(f)
    assert not f.calls and book["retained"] == "original" and result.glb_path.exists()
    assert not reserve_attempt(f.workspace, f.options, "gate_patch", attempt_id="existing")


def test_resume_never_repeats_reserved_attempt(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    candidate = f.workspace / "interrupted/source.py"
    candidate.parent.mkdir()
    candidate.write_text("partial = 1\n")
    reserve_attempt(f.workspace, f.options, "engineering", attempt_id="attempt_0001", parent="original")
    book = {"original": "original", "retained": "original", "candidate": "attempt_0001",
        "versions": {"original": version_record("original", f.workspace / "original_source.py", f.original)},
        "attempts": {"attempt_0001": {"status": "MODEL_STARTED", "candidate": "interrupted/source.py",
                                     "origin": "engineering", "parent_version": "original"}}}
    (f.workspace / "overhang_versions.json").write_text(json.dumps(book))
    _, book = run_case(f)
    assert not f.calls and not f.measures
    assert book["attempts"]["attempt_0001"]["status"] == "INTERRUPTED"
    assert len((f.workspace / "edit_attempts.jsonl").read_text().splitlines()) == 1
    f.kwargs["resume_state"] = {"stage": "completed"}
    run_case(f)
    assert not f.calls and budget_remaining(f.workspace, f.options) == 1


def test_visual_regression_rejects_area_improvement(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, actions=(80,), budget=1, candidate_appearance=False)
    _, book = run_case(f)
    assert book["retained"] == "original"
    assert not book["attempts"]["attempt_0001"]["accepted"]


def test_no_effect_and_error_have_priority():
    from adsl.agents.tools.context import ToolEvent
    events = [ToolEvent("apply_patch", "candidate/source.py")]
    assert edit_outcome("done", events, "a", "a")["status"] == "NO_EFFECT"
    assert edit_outcome("done", events, "a", "b", error=ValueError("tool"))["status"] == "TOOL_ERROR"


def test_normal_repair_still_requires_patch(tmp_path):
    source = tmp_path / "source.py"
    source.write_text("scene = None\n")
    async def noop(**kw):
        return SimpleNamespace(final_output='{"edit_action":"NO_CHANGE","reason":"none"}')
    with pytest.raises(RuntimeError, match="apply_patch"):
        asyncio.run(service.ObjectWorkflow()._repair(runtime=SimpleNamespace(run=noop), repairer=None,
            workspace=tmp_path, source_path=source, role="coder", stage="normal", payload={}))


def test_control_acceptance_never_reads_area(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, arm="control", initial=True, actions=(999999,))
    monkeypatch.setattr(service, "assess_candidate", lambda *a, **kw: pytest.fail("control area acceptance"))
    result, book = run_case(f)
    assert result.glb_path.read_text() == "GLB:1" and book["retained"] == "attempt_0001"
    assert f.measures == [] and f.manifest["checker_results"] == []
    assert "overhang_area_mm2" not in json.dumps(f.calls)


def test_initial_gate_engineering_one_ledger(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, initial=True, original_appearance=False, actions=(110, 90, 80), budget=3)
    _, book = run_case(f)
    assert [a["origin"] for a in book["attempts"].values()] == ["initial_edit", "gate_patch", "engineering"]
    assert [a["parent_version"] for a in book["attempts"].values()] == ["original", "original", "attempt_0002"]
    assert len((f.workspace / "edit_attempts.jsonl").read_text().splitlines()) == 3


def test_checker_error_keeps_matching_original_result(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, actions=(80,), budget=1)
    real_mock = service.run_checkers
    def fail_candidate(specs, **kw):
        runs = real_mock(specs, **kw)
        if "candidates" in str(kw["round_root"]):
            runs[0].result.status = "ERROR"
            runs[0].result.metrics = {}
            runs[0].result.summary = "mock timeout"
        return runs
    monkeypatch.setattr(service, "run_checkers", fail_candidate)
    _, book = run_case(f)
    assert book["retained"] == "original"
    assert f.manifest["checker_results"][0]["status"] == "PASS"
    assert book["versions"]["attempt_0001"]["checkers"][0]["result"]["status"] == "ERROR"


def test_public_edit_and_completed_resume_do_not_bypass_isolation(tmp_path, monkeypatch):
    from adsl.agents.models import EditPlan
    from unittest.mock import AsyncMock
    f = fixture(tmp_path, monkeypatch, arm="control", initial=True,
                actions=('NO_CHANGE: preserve the shape',), budget=1)
    source = tmp_path / "input.py"
    source.write_text((f.workspace / "original_source.py").read_text())
    monkeypatch.setattr(f.workflow, "_prepare_workspace", lambda path: f.workspace)
    monkeypatch.setattr(f.workflow, "_runtime", lambda *a, **kw: f.kwargs["runtime"])
    monkeypatch.setattr(f.workflow, "_plan_edit", AsyncMock(return_value=EditPlan(
        summary="local", preserved_features=["frame"], changes=[], patch_scope=["Frame"])))
    result = asyncio.run(f.workflow.edit(f.kwargs["request"], source=source))
    assert result.glb_path.read_text() == "GLB:0"
    calls = len(f.calls)
    # A stale published file is repaired from retained without a new model/checker call.
    result.source_path.write_text("stale rejected candidate\n")
    resumed = asyncio.run(f.workflow.resume(f.kwargs["request"]))
    assert resumed.source_path.read_text() == source.read_text()
    assert len(f.calls) == calls
    assert budget_remaining(f.workspace, f.options) == 0


def test_successful_noop_patch_event_is_experiment_only(tmp_path):
    from agents.tool_context import ToolContext
    from adsl.agents.tools.context import AgentToolContext
    from adsl.agents.tools.files import apply_patch
    source = tmp_path / "source.py"
    source.write_text("scene = None\n")
    for enabled in (False, True):
        ctx = AgentToolContext(tmp_path, source, record_noop_patch=enabled)
        wire = json.dumps({"path": "source.py", "old_text": "scene = None", "new_text": "scene = None"})
        wrapper = ToolContext(ctx, tool_name="apply_patch", tool_call_id="mock", tool_arguments=wire)
        asyncio.run(apply_patch.on_invoke_tool(wrapper, wire))
        assert bool(ctx.events) is enabled
