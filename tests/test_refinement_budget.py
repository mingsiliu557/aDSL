import json
from pathlib import Path

from adsl.agents.cli import _parser
from adsl.agents.models import ObjectRequest, RepairPolicy
from adsl.agents.service import _checker_evidence
from test_checker_fault_isolation import run


def test_default_round_and_candidate_budgets_match_profile(tmp_path):
    request = ObjectRequest(requirement="chair", workspace=tmp_path, task_id="test")
    assert request.max_rounds == 10
    policy = RepairPolicy()
    assert (policy.max_candidates_per_round, policy.max_total_candidates) == (1, 10)
    path = Path(__file__).resolve().parents[1] / "experiments/workflow_checkers/repair_policy.json"
    assert RepairPolicy.model_validate_json(path.read_text()) == policy
    assert policy.time_budget_seconds == 7200


def test_cli_new_run_defaults_and_explicit_diagnostic_override():
    parser = _parser()
    for args in (["create", "chair", "--output", "/tmp/test"],
                 ["edit", "chair", "--output", "/tmp/test", "--source", "/tmp/source.py"]):
        assert parser.parse_args(args).max_rounds == 10
        assert parser.parse_args([*args, "--max-rounds", "1"]).max_rounds == 1


def test_all_independent_checker_feedback_is_in_one_payload(tmp_path):
    rows = [run(tmp_path, "topology", "PASS"),
            run(tmp_path, "standing", "FAIL", 40), run(tmp_path, "fea", "FAIL", 35)]
    payload = _checker_evidence(rows, workspace=tmp_path)
    assert [r["checker"] for r in payload["checker_summary"]] == ["topology", "standing", "fea"]
    assert {r["finding_id"] for r in payload["typed_findings"]} == {"standing:tilt", "fea:tilt"}
    rows[0] = run(tmp_path, "topology", "FAIL", 5)
    rows[2] = run(tmp_path, "fea", "INDETERMINATE")
    payload = _checker_evidence(rows, workspace=tmp_path)
    assert {r["finding_id"] for r in payload["typed_findings"]} == {"topology:tilt", "standing:tilt"}
    assert payload["checker_summary"][2]["geometry_repair_allowed"] is False


def test_new_batch_manifest_and_render_budget(tmp_path):
    from experiments.standing_fea_30.run_batch import runtime_environment
    path = Path(__file__).resolve().parents[1] / "experiments/standing_fea_30/case_manifest.json"
    assert json.loads(path.read_text())["protocol"]["max_rounds_per_arm"] == 10
    env = runtime_environment(tmp_path, None)
    assert env["ADSL_RENDER_WIDTH"] == env["ADSL_RENDER_HEIGHT"] == "1024"
