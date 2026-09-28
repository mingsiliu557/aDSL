"""Graded static output contract; no model API or geometry execution."""
import json

import pytest
from agents import AgentOutputSchema
from pydantic import ValidationError

from adsl.agents.models import (
    ImageCriticDecision, CodeCriticDecision, GradedImageCriticDecision,
    GradedCodeCriticDecision, VisualIssue,
)
from adsl.agents.service import ObjectWorkflow


def issue(severity, target=None):
    return VisualIssue(severity=severity, target=target,
                       problem="Visible in the front view", suggested_fix="Restore the backrest")


@pytest.mark.parametrize("severities", [[], ["MED"], ["LOW"], ["MED", "LOW"], ["HIGH", "LOW"]])
def test_decision_high_is_the_only_visual_gate(severities):
    decision = GradedImageCriticDecision(approved=True, observations=[],
        required_changes=["model supplied incorrect gate"], issues=[issue(s) for s in severities])
    normalized = ObjectWorkflow._normalize_visual_decision(decision)
    assert normalized.approved == ("HIGH" not in severities)
    assert normalized.required_changes == (["Restore the backrest"] if "HIGH" in severities else [])
    assert normalized.issues == decision.issues


def test_decision_target_and_legacy_rejection():
    d = GradedImageCriticDecision(approved=True, observations=[], issues=[issue("HIGH", "Backrest")])
    assert ObjectWorkflow._normalize_visual_decision(d).required_changes == ["Backrest: Restore the backrest"]
    for approved, changes in [(True, ["fix"]), (False, [])]:
        old = ImageCriticDecision(approved=approved, observations=[], required_changes=changes)
        assert not ObjectWorkflow._normalize_visual_decision(old).approved


def test_source_grounded_gate_applied_after_visual_normalization():
    d = GradedCodeCriticDecision(approved=True, observations=[], issues=[])
    assert not ObjectWorkflow._normalize_code_critic_decision(d, source_grounded=False).approved


@pytest.mark.parametrize("kind", [GradedImageCriticDecision, GradedCodeCriticDecision])
def test_schema_strict_sdk_and_typed_serialization(kind):
    with pytest.raises(ValidationError):
        kind(approved=True, observations=[])
    with pytest.raises(ValidationError):
        kind(approved=True, observations=[], issues=[dict(severity="CRITICAL", target=None,
            problem="x", suggested_fix="y")])
    schema = AgentOutputSchema(kind)
    assert "issues" in schema.json_schema()["required"]
    original = kind(approved=True, observations=[], issues=[issue("LOW")])
    parsed = schema.validate_json(original.model_dump_json())
    assert ObjectWorkflow._typed_output(parsed, kind) == original
    assert json.loads(parsed.model_dump_json())["issues"][0]["severity"] == "LOW"
    for base in (ImageCriticDecision, CodeCriticDecision):
        assert "issues" not in base.model_json_schema()["properties"]


def test_resolved_visual_decision_uses_current_code_not_rejected_image():
    image = dict(required_changes=["old issue"], issues=[issue("HIGH").model_dump()])
    code = dict(required_changes=[], issues=[issue("LOW").model_dump()])
    resolved = ObjectWorkflow._resolved_visual_feedback(image, code)
    assert resolved["reviewed_by"] == "code_critic" and not resolved["required_changes"]
    assert ObjectWorkflow._resolved_visual_feedback(image, None)["required_changes"] == ["old issue"]


@pytest.mark.parametrize("graded", [False, True], ids=["legacy", "graded"])
def test_saved_reports_remain_readable_by_chat_and_cli(tmp_path, graded):
    from adsl.agents.cli import _parser, _resume_request
    from adsl.chat.workspace import scan_workspace

    image = dict(approved=True, observations=["Current appearance accepted"], required_changes=[])
    code = dict(image, image_critic_corrections=["Rear legs are visible in the bottom view"])
    if graded:
        image["issues"] = [issue("LOW").model_dump()]
        code["issues"] = [issue("LOW").model_dump()]
    request = dict(requirement="A four-legged chair", task_id="saved-review",
                   articulation=False, max_rounds=1, checker_specs=[], overhang_experiment={})
    (tmp_path / "runtime_config.json").write_text(json.dumps({"request": request}))
    (tmp_path / "run.json").write_text(json.dumps(dict(
        status="completed", approved=True, image_critic_history=[image], code_critic_history=[code])))
    (tmp_path / "source.py").write_text("# saved source\n")
    snapshot = scan_workspace(tmp_path)
    assert snapshot.latest_image_critique == image
    assert snapshot.latest_code_critique == code
    assert "Rear legs are visible" in snapshot.summary_text()
    args = _parser().parse_args(["resume", "--output", str(tmp_path), "--max-rounds", "1"])
    resumed = _resume_request(args)
    assert resumed.requirement == request["requirement"]
    assert resumed.max_rounds == 1 and resumed.checker_specs == ()
