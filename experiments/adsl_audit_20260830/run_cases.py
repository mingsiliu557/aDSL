from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
from typing import Any


SYSTEMIC_ERROR_MARKERS = (
    "401",
    "403",
    "api key",
    "authentication",
    "connection error",
    "connection refused",
    "connecterror",
    "no route to host",
    "stepcode credential",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _stop_process_group(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = 5.0,
) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
        return
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        # A process in uninterruptible I/O sleep exits only after the kernel call returns.
        pass


def _tail_text(path: Path, limit: int = 32768) -> str:
    if not path.is_file():
        return ""
    with path.open("rb") as handle:
        handle.seek(0, os.SEEK_END)
        size = handle.tell()
        handle.seek(max(0, size - limit))
        return handle.read().decode("utf-8", errors="replace")


def _is_systemic_failure(stdout_path: Path, stderr_path: Path) -> bool:
    text = (_tail_text(stdout_path) + "\n" + _tail_text(stderr_path)).lower()
    return any(marker in text for marker in SYSTEMIC_ERROR_MARKERS)


def _command(
    *,
    repo_root: Path,
    profile: Path,
    output_root: Path,
    invocation: dict[str, Any],
) -> list[str]:
    action = str(invocation["action"])
    workspace = output_root / str(invocation["output"])
    command = [
        sys.executable,
        "-m",
        "adsl.agents.cli",
        "--model-config",
        str(profile),
        action,
        str(invocation["requirement"]),
        "--output",
        str(workspace),
        "--task-id",
        f"audit-{invocation['id']}",
        "--max-rounds",
        str(invocation["max_rounds"]),
    ]
    for image in invocation.get("images", []):
        command.extend(["--image", str((repo_root / str(image)).resolve())])
    if invocation.get("articulation", False):
        command.append("--articulation")
    if action == "edit":
        source_id = str(invocation["source_from"])
        source_path = output_root / source_id / "source.py"
        command.extend(
            [
                "--source",
                str(source_path),
                "--edit-kind",
                str(invocation.get("edit_kind", "continue")),
            ]
        )
    return command


def _runtime_summary(workspace: Path) -> dict[str, Any]:
    manifest_path = workspace / "run.json"
    if not manifest_path.is_file():
        return {}
    manifest = _read_json(manifest_path)
    keys = (
        "status",
        "selected_round",
        "approved",
        "critic_skipped",
        "finalization_reason",
        "failures",
        "requests",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "wall_time_seconds",
    )
    summary = {key: manifest.get(key) for key in keys if key in manifest}
    usage = manifest.get("usage")
    if isinstance(usage, dict):
        summary.update(
            {
                key: usage.get(key)
                for key in ("requests", "input_tokens", "output_tokens", "total_tokens")
                if key in usage
            }
        )
    return summary


def _run_one(
    *,
    repo_root: Path,
    profile: Path,
    output_root: Path,
    runtime: dict[str, Any],
    invocation: dict[str, Any],
    ledger: dict[str, Any],
    ledger_path: Path,
    dry_run: bool,
) -> tuple[bool, bool]:
    invocation_id = str(invocation["id"])
    workspace = output_root / str(invocation["output"])
    records = ledger.setdefault("invocations", {})
    previous = records.get(invocation_id)

    if previous is not None:
        print(
            json.dumps(
                {
                    "event": "not_retried",
                    "invocation": invocation_id,
                    "previous_status": previous.get("status"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return previous.get("status") in {"completed", "skipped_completed"}, bool(
            previous.get("systemic_failure", False)
        )

    missing_dependencies = [
        dependency
        for dependency in invocation.get("depends_on", [])
        if not (output_root / str(dependency) / "source.py").is_file()
    ]
    if missing_dependencies:
        records[invocation_id] = {
            "status": "blocked_dependency",
            "missing_dependencies": missing_dependencies,
            "recorded_at": _utc_now(),
        }
        _write_json(ledger_path, ledger)
        return False, False

    if workspace.exists():
        completed = (
            (workspace / "run.json").is_file()
            and _read_json(workspace / "run.json").get("status") == "completed"
        )
        records[invocation_id] = {
            "status": "skipped_completed" if completed else "blocked_existing_workspace",
            "workspace": str(workspace),
            "recorded_at": _utc_now(),
            "runtime_summary": _runtime_summary(workspace),
        }
        _write_json(ledger_path, ledger)
        return completed, False

    command = _command(
        repo_root=repo_root,
        profile=profile,
        output_root=output_root,
        invocation=invocation,
    )
    if dry_run:
        print(
            json.dumps(
                {
                    "event": "dry_run",
                    "invocation": invocation_id,
                    "action": invocation["action"],
                    "workspace": str(workspace),
                    "max_rounds": invocation["max_rounds"],
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return True, False

    logs_root = output_root / "_launcher_logs"
    logs_root.mkdir(parents=True, exist_ok=True)
    stdout_path = logs_root / f"{invocation_id}.stdout.log"
    stderr_path = logs_root / f"{invocation_id}.stderr.log"
    environment = os.environ.copy()
    environment.update({str(k): str(v) for k, v in runtime["environment"].items()})
    environment["PYTHONUNBUFFERED"] = "1"
    hard_timeout = float(runtime["hard_timeout_seconds"])
    monitor_interval = float(runtime["monitor_interval_seconds"])
    started_at = _utc_now()
    started_monotonic = time.monotonic()

    with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr_handle:
        process = subprocess.Popen(
            command,
            cwd=repo_root,
            env=environment,
            stdout=stdout_handle,
            stderr=stderr_handle,
            text=True,
            start_new_session=True,
        )
        records[invocation_id] = {
            "status": "running",
            "logical_case": invocation["logical_case"],
            "action": invocation["action"],
            "workspace": str(workspace),
            "pid": process.pid,
            "started_at": started_at,
            "last_heartbeat": started_at,
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "attempt": 1,
        }
        _write_json(ledger_path, ledger)
        print(
            json.dumps(
                {
                    "event": "started",
                    "invocation": invocation_id,
                    "pid": process.pid,
                    "workspace": str(workspace),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

        timed_out = False
        interrupted = False
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started_monotonic
                if elapsed >= hard_timeout:
                    timed_out = True
                    _stop_process_group(process)
                    break
                records[invocation_id]["last_heartbeat"] = _utc_now()
                records[invocation_id]["elapsed_seconds"] = round(elapsed, 3)
                _write_json(ledger_path, ledger)
                print(
                    json.dumps(
                        {
                            "event": "heartbeat",
                            "invocation": invocation_id,
                            "elapsed_seconds": round(elapsed, 1),
                        }
                    ),
                    flush=True,
                )
                time.sleep(min(monitor_interval, max(0.1, hard_timeout - elapsed)))
        except KeyboardInterrupt:
            interrupted = True
            _stop_process_group(process)
        returncode = process.poll()

    elapsed = round(time.monotonic() - started_monotonic, 3)
    if timed_out:
        status = "hard_timeout"
    elif interrupted:
        status = "interrupted"
    elif returncode == 0:
        status = "completed"
    else:
        status = "failed"
    systemic_failure = status == "failed" and _is_systemic_failure(
        stdout_path,
        stderr_path,
    )
    records[invocation_id].update(
        {
            "status": status,
            "returncode": returncode,
            "finished_at": _utc_now(),
            "elapsed_seconds": elapsed,
            "timed_out": timed_out,
            "interrupted": interrupted,
            "systemic_failure": systemic_failure,
            "runtime_summary": _runtime_summary(workspace),
        }
    )
    _write_json(ledger_path, ledger)
    print(
        json.dumps(
            {
                "event": "finished",
                "invocation": invocation_id,
                "status": status,
                "elapsed_seconds": elapsed,
                "systemic_failure": systemic_failure,
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return status == "completed", systemic_failure


def _parser() -> argparse.ArgumentParser:
    default_manifest = Path(__file__).with_name("case_manifest.json")
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, default=default_manifest)
    parser.add_argument("--invocation", action="append", default=[])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    return parser


def main() -> int:
    args = _parser().parse_args()
    manifest_path = args.manifest.expanduser().resolve()
    manifest = _read_json(manifest_path)
    repo_root = Path(__file__).resolve().parents[2]
    runtime = manifest["runtime"]
    profile = (repo_root / runtime["model_profile"]).resolve()
    output_root = (repo_root / runtime["output_root"]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    invocations = manifest["invocations"]
    by_id = {item["id"]: item for item in invocations}

    if args.list:
        for item in invocations:
            print(
                f"{item['id']}\t{item['logical_case']}\t{item['action']}\t"
                f"rounds={item['max_rounds']}"
            )
        return 0

    selected_ids = args.invocation or [item["id"] for item in invocations]
    unknown = sorted(set(selected_ids) - set(by_id))
    if unknown:
        raise SystemExit(f"Unknown invocation IDs: {unknown}")

    ledger_path = output_root / "launcher_results.json"
    ledger = (
        _read_json(ledger_path)
        if ledger_path.is_file()
        else {
            "schema_version": 1,
            "study_id": manifest["study_id"],
            "created_at": _utc_now(),
            "retry_policy": runtime["retry_policy"],
            "invocations": {},
        }
    )
    failures = 0
    for invocation_id in selected_ids:
        ok, systemic = _run_one(
            repo_root=repo_root,
            profile=profile,
            output_root=output_root,
            runtime=runtime,
            invocation=by_id[invocation_id],
            ledger=ledger,
            ledger_path=ledger_path,
            dry_run=args.dry_run,
        )
        if not ok:
            failures += 1
        if systemic:
            ledger["paused_for_systemic_failure"] = {
                "invocation": invocation_id,
                "recorded_at": _utc_now(),
            }
            _write_json(ledger_path, ledger)
            break
    ledger["last_updated_at"] = _utc_now()
    _write_json(ledger_path, ledger)
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
