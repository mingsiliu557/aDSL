from pathlib import Path

from experiments.standing_fea_30.profiles import definition, prompt_is_eligible
from experiments.standing_fea_30.run_batch import classify_pause, generation_command, terminal_generation
from experiments.standing_fea_30.summarize import exact_mcnemar, summarize


def test_chair_requires_seat_and_back() -> None:
    category = definition("chair_stool")
    common = dict(category=category, dataset="shapenet", filtered_name="chair", synset_id="03001627")
    assert prompt_is_eligible(prompt="a tall chair with a seat and high back", **common)
    assert not prompt_is_eligible(prompt="a tall chair with four seat legs", **common)


def test_only_ours_enables_checkers(tmp_path: Path) -> None:
    case = {"case_id": "SF01", "prompt": "chair", "fea_config": "fea_chair_stool.json"}
    kwargs = dict(case=case, workspace=tmp_path / "w", adsl_run=Path("/bin/true"), model_config=Path(__file__), max_rounds=4)
    baseline = generation_command(arm="adsl", **kwargs)
    ours = generation_command(arm="ours", **kwargs)
    assert "--checker-config" not in baseline
    assert ours.count("--checker-config") == 2


def test_gate_exhaustion_is_terminal(tmp_path: Path) -> None:
    (tmp_path / "source.py").write_text("scene = None\n")
    (tmp_path / "run.json").write_text('{"error_type":"WorkflowGateError"}')
    terminal, outcome, _ = terminal_generation(workspace=tmp_path, process={"return_code": 1})
    assert terminal and outcome == "completed_unpublished_gate_failure"


def test_pause_classification(tmp_path: Path) -> None:
    log = tmp_path / "logs/adsl"; log.mkdir(parents=True)
    target = log / "SF01.stderr.log"
    target.write_text("HTTP 429 insufficient_quota")
    assert classify_pause(output_root=tmp_path, case_id="SF01", arm="adsl", row={}) == "PAUSED_QUOTA"
    target.write_text("GpuRenderWorkerUnavailable: heartbeat lost")
    assert classify_pause(output_root=tmp_path, case_id="SF01", arm="adsl", row={}) == "PAUSED_GPU"


def test_indeterminate_is_not_joint_pass() -> None:
    rows = [
        {"case_id": "SF01", "arm": "adsl", "run_status": "COMPLETED", "joint_pass": False, "standing_status": "PASS", "fea_status": "INDETERMINATE", "fea_reason": "NOT_MESHABLE"},
        {"case_id": "SF01", "arm": "ours", "run_status": "COMPLETED", "joint_pass": True, "standing_status": "PASS", "fea_status": "PASS", "fea_reason": None},
    ]
    result = summarize(rows, 1)
    assert result["paired_joint_pass"]["difference"] == 1.0
    assert result["fea_indeterminate_causes"]["adsl"] == {"NOT_MESHABLE": 1}
    assert exact_mcnemar([False], [True])["p_value_two_sided"] == 1.0
