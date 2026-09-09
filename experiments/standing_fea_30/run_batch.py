#!/usr/bin/env python3
"""Run independent vanilla-aDSL and checker-unified prompt-to-3D arms."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[2]
EXPERIMENT = Path(__file__).resolve().parent
DEFAULT_PYTHON = Path("/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python")
DEFAULT_ADSL_RUN = Path("/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/adsl-run")
DEFAULT_MODEL_CONFIG = EXPERIMENT / "configs/stepcode-temperature-zero.yaml"
DEFAULT_CHECKER_RUN = REPO / "experiments/workflow_checkers/run.py"
DEFAULT_ASSET_EXECUTOR = REPO / "adsl-agents/utils/asset_executor.py"
DEFAULT_STANDING_SPEC = REPO / "experiments/workflow_checkers/specs/standing.json"
DEFAULT_REPAIR_POLICY = REPO / "experiments/workflow_checkers/repair_policy.json"
DEFAULT_GPU_QUEUE = Path("/jiigan-hp/lms/aDSL/experiment/gpu_render_queue")
MUJOCO_PYTHONPATH = Path("/jiigan-hp/lms/aDSL/experiment/runtime/mujoco-py310")
GMSH_PYTHONPATH = Path("/vepfs_default/chanxueyan/lhp/lms/fea_runtime/gmsh-4.15.2/lib")
FEA_LIBRARY_PATH = Path(
    "/vepfs_default/chanxueyan/lhp/lms/fea_runtime/sysroot/usr/lib/x86_64-linux-gnu"
)
VALID_ARMS = ("adsl", "ours")
QUOTA_MARKERS = (
    "insufficient_quota", "quota exceeded", "quota_exceeded",
    "usage limit", "rate limit", "rate_limit", "status code: 429", "http 429",
)
GPU_MARKERS = (
    "gpurenderworkerunavailable", "gpurenderjobfailed", "gpurenderqueueerror",
    "gpumemorynotquiescent", "no allocated nvidia gpu", "heartbeat lost",
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def append_jsonl(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        handle.flush()
        os.fsync(handle.fileno())


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def tail_text(paths: Sequence[Path], limit_bytes: int = 131072) -> str:
    chunks: list[str] = []
    for path in paths:
        if not path.is_file():
            continue
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            size = handle.tell()
            handle.seek(max(0, size - limit_bytes))
            chunks.append(handle.read().decode("utf-8", errors="replace"))
    return "\n".join(chunks).lower()


def classify_pause(*, output_root: Path, case_id: str, arm: str, row: dict[str, Any]) -> str:
    recorded = row.get("generation_logs", {})
    paths = [
        output_root / str(recorded.get("stdout", "")),
        output_root / str(recorded.get("stderr", "")),
    ] if recorded else [
        output_root / "logs" / arm / f"{case_id}.stdout.log",
        output_root / "logs" / arm / f"{case_id}.stderr.log",
    ]
    text = tail_text(paths)
    if any(marker in text for marker in QUOTA_MARKERS):
        return "PAUSED_QUOTA"
    if any(marker in text for marker in GPU_MARKERS):
        return "PAUSED_GPU"
    evaluation_status = row.get("evaluation", {}).get("status")
    if evaluation_status == "CHECKER_ERROR":
        return "PAUSED_CHECKER_ERROR"
    return "PAUSED_ERROR"


def case_by_id(manifest: dict[str, Any], selected: set[str]) -> list[dict[str, Any]]:
    cases = [case for case in manifest["cases"] if not selected or case["case_id"] in selected]
    found = {case["case_id"] for case in cases}
    if selected and found != selected:
        raise ValueError(f"unknown cases: {sorted(selected - found)}")
    return cases


def fea_spec_path(case: dict[str, Any]) -> Path:
    return EXPERIMENT / "specs" / case["fea_config"]


def generation_command(
    *,
    case: dict[str, Any],
    arm: str,
    workspace: Path,
    adsl_run: Path,
    model_config: Path,
    max_rounds: int,
) -> list[str]:
    if arm not in VALID_ARMS:
        raise ValueError(arm)
    command = [
        str(adsl_run.resolve()),
        "--model-config", str(model_config.resolve()),
        "create", case["prompt"],
        "--output", str(workspace.resolve()),
        "--task-id", f"standing-fea-30-{case['case_id']}-{arm}",
        "--max-rounds", str(max_rounds),
    ]
    if arm == "ours":
        command.extend([
            "--checker-config", str(DEFAULT_STANDING_SPEC.resolve()),
            "--checker-config", str(fea_spec_path(case).resolve()),
            "--repair-policy-config", str(DEFAULT_REPAIR_POLICY.resolve()),
        ])
    return command


def remaining_resume_rounds(workspace: Path, maximum: int) -> int:
    seen = []
    for path in (workspace / "rounds").glob("round_[0-9][0-9]"):
        try:
            seen.append(int(path.name.rsplit("_", 1)[1]))
        except ValueError:
            continue
    return max(1, maximum - max(seen, default=0))


def resume_command(
    *,
    case: dict[str, Any],
    arm: str,
    workspace: Path,
    adsl_run: Path,
    model_config: Path,
    max_rounds: int,
) -> list[str]:
    command = [
        str(adsl_run.resolve()),
        "--model-config", str(model_config.resolve()),
        "resume", "--output", str(workspace.resolve()),
        "--task-id", f"standing-fea-30-{case['case_id']}-{arm}",
        "--max-rounds", str(remaining_resume_rounds(workspace, max_rounds)),
    ]
    if arm == "ours":
        command.extend([
            "--checker-config", str(DEFAULT_STANDING_SPEC.resolve()),
            "--checker-config", str(fea_spec_path(case).resolve()),
            "--repair-policy-config", str(DEFAULT_REPAIR_POLICY.resolve()),
        ])
    return command


def runtime_environment(output_root: Path, gpu_queue: Path) -> dict[str, str]:
    environment = dict(os.environ)
    scratch = output_root / "scratch"
    scratch.mkdir(parents=True, exist_ok=True)
    environment.update({
        "ADSL_GPU_RENDER_QUEUE": str(gpu_queue.resolve()),
        "ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS": "1800",
        "ADSL_GPU_RENDER_WAIT_TIMEOUT_SECONDS": "3600",
        "ADSL_RENDER_ENGINE": "BLENDER_EEVEE",
        "ADSL_RENDER_WIDTH": "512",
        "ADSL_RENDER_HEIGHT": "512",
        "ADSL_RENDER_SAMPLES": "64",
        "PYTHONHASHSEED": "20260909",
        "TMPDIR": str(scratch.resolve()),
    })
    return environment


def checker_environment(environment: dict[str, str]) -> dict[str, str]:
    value = dict(environment)
    python_paths = [str(GMSH_PYTHONPATH), str(MUJOCO_PYTHONPATH)]
    current_python = value.get("PYTHONPATH", "")
    if current_python:
        python_paths.append(current_python)
    value["PYTHONPATH"] = os.pathsep.join(python_paths)
    libraries = [str(FEA_LIBRARY_PATH)]
    current_libraries = value.get("LD_LIBRARY_PATH", "")
    if current_libraries:
        libraries.append(current_libraries)
    value["LD_LIBRARY_PATH"] = os.pathsep.join(libraries)
    return value


def run_logged(
    command: Sequence[str],
    *,
    environment: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        process = subprocess.Popen(
            list(command), cwd=REPO, env=environment,
            stdout=stdout, stderr=stderr, text=True, start_new_session=True,
        )
        timed_out = False
        try:
            return_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            os.killpg(process.pid, signal.SIGTERM)
            try:
                return_code = process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                return_code = process.wait()
    return {
        "pid": process.pid,
        "return_code": 124 if timed_out else return_code,
        "timed_out": timed_out,
        "elapsed_seconds": round(time.monotonic() - started, 3),
    }


def run_manifest(workspace: Path) -> dict[str, Any]:
    path = workspace / "run.json"
    return read_json(path) if path.is_file() else {}


def terminal_generation(
    *, workspace: Path, process: dict[str, Any]
) -> tuple[bool, str, dict[str, Any]]:
    manifest = run_manifest(workspace)
    if process["return_code"] == 0 and (workspace / "source.py").is_file():
        return True, "completed", manifest
    if (
        manifest.get("error_type") == "WorkflowGateError"
        and (workspace / "source.py").is_file()
    ):
        return True, "completed_unpublished_gate_failure", manifest
    return False, "infrastructure_or_generation_error", manifest


def next_evaluation_attempt(root: Path) -> Path:
    index = 1
    while True:
        value = root / f"attempt_{index:02d}"
        if not value.exists():
            return value
        index += 1


def next_generation_logs(root: Path) -> tuple[Path, Path]:
    index = 1
    while (root / f"attempt_{index:02d}.stdout.log").exists() or (
        root / f"attempt_{index:02d}.stderr.log"
    ).exists():
        index += 1
    return (
        root / f"attempt_{index:02d}.stdout.log",
        root / f"attempt_{index:02d}.stderr.log",
    )


def evaluate_final_source(
    *,
    case: dict[str, Any],
    arm: str,
    workspace: Path,
    output_root: Path,
    python: Path,
    checker_run: Path,
    asset_executor: Path,
    environment: dict[str, str],
    timeout_seconds: float,
) -> dict[str, Any]:
    attempt = next_evaluation_attempt(output_root / "evaluation" / arm / case["case_id"])
    asset_dir = attempt / "asset"
    source = workspace / "source.py"
    export_command = [
        str(python.resolve()), str(asset_executor.resolve()),
        "--source", str(source.resolve()), "--output", str(asset_dir.resolve()), "--urdf",
    ]
    export = run_logged(
        export_command,
        environment=environment,
        stdout_path=attempt / "logs/export.stdout.log",
        stderr_path=attempt / "logs/export.stderr.log",
        timeout_seconds=timeout_seconds,
    )
    result: dict[str, Any] = {
        "attempt": str(attempt.relative_to(output_root)),
        "export": export,
        "checkers": {},
    }
    if export["return_code"] != 0:
        result["status"] = "EXPORT_ERROR"
        return result

    configs = {
        "standing": REPO / "experiments/workflow_checkers/configs/standing.json",
        "fea": EXPERIMENT / "configs" / case["fea_config"],
    }
    check_env = checker_environment(environment)
    for checker, config in configs.items():
        checker_output = attempt / "checkers" / checker
        command = [
            str(python.resolve()), str(checker_run.resolve()), checker,
            "--asset-dir", str((asset_dir / "render").resolve()),
            "--source", str(source.resolve()),
            "--output-dir", str(checker_output.resolve()),
            "--config", str(config.resolve()),
        ]
        process = run_logged(
            command,
            environment=check_env,
            stdout_path=attempt / f"logs/{checker}.stdout.log",
            stderr_path=attempt / f"logs/{checker}.stderr.log",
            timeout_seconds=timeout_seconds,
        )
        result_path = checker_output / "result.json"
        payload = read_json(result_path) if result_path.is_file() else None
        result["checkers"][checker] = {
            "process": process,
            "result_path": str(result_path.relative_to(output_root)),
            "result": payload,
        }
        if process["return_code"] != 0 or payload is None:
            result["status"] = "CHECKER_ERROR"
            return result
    result["status"] = "COMPLETED"
    return result


def run_arm(
    *,
    case: dict[str, Any],
    arm: str,
    output_root: Path,
    args: argparse.Namespace,
    environment: dict[str, str],
) -> dict[str, Any]:
    state_path = output_root / "state" / arm / f"{case['case_id']}.json"
    if state_path.is_file():
        state = read_json(state_path)
        if state.get("status") == "COMPLETED":
            return {**state, "skipped_existing": True}
        if not args.resume_existing:
            raise RuntimeError(
                f"non-complete state exists for {case['case_id']}/{arm}; "
                "use --resume-existing after review"
            )

    workspace = output_root / "workspaces" / arm / case["case_id"]
    if workspace.exists():
        if not args.resume_existing:
            raise FileExistsError(f"refusing to overwrite {workspace}")
        command = resume_command(
            case=case, arm=arm, workspace=workspace, adsl_run=args.adsl_run,
            model_config=args.model_config, max_rounds=args.max_rounds,
        )
        action = "resume"
    else:
        command = generation_command(
            case=case, arm=arm, workspace=workspace, adsl_run=args.adsl_run,
            model_config=args.model_config, max_rounds=args.max_rounds,
        )
        action = "create"

    record: dict[str, Any] = {
        "case_id": case["case_id"], "arm": arm, "category": case["category"],
        "status": "RUNNING", "action": action, "started_at": utc_now(),
        "workspace": str(workspace.relative_to(output_root)), "command": command,
    }
    stdout_path, stderr_path = next_generation_logs(
        output_root / "logs" / arm / case["case_id"]
    )
    record["generation_logs"] = {
        "stdout": str(stdout_path.relative_to(output_root)),
        "stderr": str(stderr_path.relative_to(output_root)),
    }
    write_json(state_path, record)
    append_jsonl(output_root / "events.jsonl", {**record, "event": "generation_started"})
    process = run_logged(
        command,
        environment=environment,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        timeout_seconds=args.case_timeout_seconds,
    )
    terminal, outcome, manifest = terminal_generation(workspace=workspace, process=process)
    record.update({
        "generation_process": process,
        "generation_outcome": outcome,
        "run_status": manifest.get("status"),
        "run_error_type": manifest.get("error_type"),
        "approved": manifest.get("approved"),
        "selected_round": manifest.get("selected_round"),
        "usage": manifest.get("usage", {}),
    })
    if not terminal:
        record.update({"status": "STOPPED_ERROR", "finished_at": utc_now()})
        write_json(state_path, record)
        append_jsonl(output_root / "events.jsonl", {**record, "event": "generation_stopped"})
        return record

    evaluation = evaluate_final_source(
        case=case, arm=arm, workspace=workspace, output_root=output_root,
        python=args.python, checker_run=args.checker_run,
        asset_executor=args.asset_executor, environment=environment,
        timeout_seconds=args.case_timeout_seconds,
    )
    record["evaluation"] = evaluation
    record["status"] = "COMPLETED" if evaluation["status"] == "COMPLETED" else "STOPPED_ERROR"
    record["finished_at"] = utc_now()
    write_json(state_path, record)
    append_jsonl(output_root / "events.jsonl", {**record, "event": "arm_finished"})
    return record


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--arm", action="append", choices=VALID_ARMS, default=[])
    parser.add_argument("--max-rounds", type=int, default=4)
    parser.add_argument("--case-timeout-seconds", type=float, default=21600)
    parser.add_argument("--resume-existing", action="store_true")
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--adsl-run", type=Path, default=DEFAULT_ADSL_RUN)
    parser.add_argument("--model-config", type=Path, default=DEFAULT_MODEL_CONFIG)
    parser.add_argument("--checker-run", type=Path, default=DEFAULT_CHECKER_RUN)
    parser.add_argument("--asset-executor", type=Path, default=DEFAULT_ASSET_EXECUTOR)
    parser.add_argument("--gpu-queue", type=Path, default=DEFAULT_GPU_QUEUE)
    args = parser.parse_args()
    if args.max_rounds != 4:
        parser.error("the frozen experiment requires exactly four maximum rounds")
    for path in (
        args.python, args.adsl_run, args.model_config, args.checker_run,
        args.asset_executor, DEFAULT_STANDING_SPEC, DEFAULT_REPAIR_POLICY,
    ):
        if not path.expanduser().resolve().is_file():
            raise FileNotFoundError(path)
    if not args.gpu_queue.expanduser().resolve().is_dir():
        raise FileNotFoundError(args.gpu_queue)
    from adsl.tools.gpu_render_queue import worker_is_live
    if not worker_is_live(args.gpu_queue):
        raise RuntimeError(
            f"GPU render worker heartbeat is not live: {args.gpu_queue.resolve()}"
        )

    manifest_path = args.manifest.expanduser().resolve()
    manifest = read_json(manifest_path)
    cases = case_by_id(manifest, set(args.case))
    selected_arms = set(args.arm or VALID_ARMS)
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    environment = runtime_environment(output_root, args.gpu_queue)
    frozen_config = {
        "created_at": utc_now(),
        "manifest": str(manifest_path),
        "manifest_sha256": file_sha256(manifest_path),
        "repository_commit": subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=REPO, text=True
        ).strip(),
        "model_config": str(args.model_config.resolve()),
        "max_rounds": args.max_rounds,
        "render": {"width": 512, "height": 512, "samples": 64, "views": 8},
        "llm_seed_available": False,
        "python_hash_seed": 20260909,
        "planned_cases": [case["case_id"] for case in manifest["cases"]],
        "planned_arms": list(VALID_ARMS),
    }
    config_path = output_root / "batch_config.json"
    if config_path.is_file():
        previous = read_json(config_path)
        invariant_keys = (
            "manifest_sha256", "repository_commit", "model_config",
            "max_rounds", "render", "llm_seed_available", "python_hash_seed",
        )
        changed = [
            key for key in invariant_keys if previous.get(key) != frozen_config.get(key)
        ]
        if changed:
            raise RuntimeError(f"frozen batch configuration changed: {changed}")
    else:
        write_json(config_path, frozen_config)
    append_jsonl(output_root / "invocations.jsonl", {
        "started_at": utc_now(),
        "selected_cases": [case["case_id"] for case in cases],
        "selected_arms": sorted(selected_arms),
        "resume_existing": args.resume_existing,
    })

    for case in cases:
        order = [arm for arm in case["arm_order"] if arm in selected_arms]
        for arm in order:
            row = run_arm(
                case=case, arm=arm, output_root=output_root,
                args=args, environment=environment,
            )
            if row["status"] != "COMPLETED":
                pause_status = classify_pause(
                    output_root=output_root, case_id=case["case_id"], arm=arm, row=row
                )
                write_json(output_root / "batch_terminal.json", {
                    "status": pause_status, "stopped_at": utc_now(),
                    "case_id": case["case_id"], "arm": arm,
                    "reason": row.get("generation_outcome", row.get("evaluation", {}).get("status")),
                })
                return 1
    write_json(output_root / "batch_terminal.json", {
        "status": "COMPLETE", "finished_at": utc_now(),
        "completed_cases": len(cases), "completed_arms": len(cases) * len(selected_arms),
    })
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
