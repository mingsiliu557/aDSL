from __future__ import annotations

import argparse
import asyncio
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import time
import traceback
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


def _runtime_summary(workspace: Path) -> dict[str, Any]:
    path = workspace / "run.json"
    if not path.is_file():
        return {}
    payload = _read_json(path)
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
    summary = {key: payload.get(key) for key in keys if key in payload}
    usage = payload.get("usage")
    if isinstance(usage, dict):
        summary.update(
            {
                key: usage.get(key)
                for key in ("requests", "input_tokens", "output_tokens", "total_tokens")
                if key in usage
            }
        )
    return summary


def _is_systemic(error: BaseException) -> bool:
    text = f"{type(error).__name__}: {error}".lower()
    return any(marker in text for marker in SYSTEMIC_ERROR_MARKERS)


async def _await_with_heartbeat(
    *,
    task: asyncio.Task[Any],
    invocation_id: str,
    record: dict[str, Any],
    ledger: dict[str, Any],
    ledger_path: Path,
    monitor_interval: float,
    hard_timeout: float,
    started_monotonic: float,
) -> Any:
    while True:
        elapsed = time.monotonic() - started_monotonic
        remaining = hard_timeout - elapsed
        if remaining <= 0:
            task.cancel()
            try:
                await task
            except BaseException:
                pass
            raise TimeoutError(
                f"case hard timeout after {hard_timeout:g} seconds"
            )
        done, _ = await asyncio.wait(
            {task},
            timeout=min(monitor_interval, remaining),
        )
        if done:
            return await task
        elapsed = time.monotonic() - started_monotonic
        record["last_heartbeat"] = _utc_now()
        record["elapsed_seconds"] = round(elapsed, 3)
        _write_json(ledger_path, ledger)
        print(
            json.dumps(
                {
                    "event": "heartbeat",
                    "invocation": invocation_id,
                    "elapsed_seconds": round(elapsed, 1),
                    "mode": "persistent",
                }
            ),
            flush=True,
        )


async def _run_one(
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

    if dry_run:
        print(
            json.dumps(
                {
                    "event": "dry_run",
                    "invocation": invocation_id,
                    "mode": "persistent",
                    "workspace": str(workspace),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        return True, False

    from adsl.agents.models import ObjectRequest
    from adsl.agents.service import ObjectWorkflow

    request = ObjectRequest(
        requirement=str(invocation["requirement"]),
        workspace=workspace,
        task_id=f"audit-{invocation_id}",
        image_paths=tuple(
            (repo_root / str(path)).resolve()
            for path in invocation.get("images", [])
        ),
        articulation=bool(invocation.get("articulation", False)),
        max_rounds=int(invocation["max_rounds"]),
    )
    workflow = ObjectWorkflow(profile)
    started_at = _utc_now()
    started_monotonic = time.monotonic()
    record = {
        "status": "running",
        "logical_case": invocation["logical_case"],
        "action": invocation["action"],
        "workspace": str(workspace),
        "pid": os.getpid(),
        "started_at": started_at,
        "last_heartbeat": started_at,
        "attempt": 1,
        "launcher_mode": "persistent",
    }
    records[invocation_id] = record
    _write_json(ledger_path, ledger)
    print(
        json.dumps(
            {
                "event": "started",
                "invocation": invocation_id,
                "pid": os.getpid(),
                "workspace": str(workspace),
                "mode": "persistent",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )

    action = str(invocation["action"])
    if action == "create":
        coroutine = workflow.generate(request)
    elif action == "edit":
        source = output_root / str(invocation["source_from"]) / "source.py"
        coroutine = workflow.edit(
            request,
            source=source,
            edit_kind=str(invocation.get("edit_kind", "continue")),
        )
    else:
        raise ValueError(f"Unsupported action: {action}")

    task = asyncio.create_task(coroutine)
    error: BaseException | None = None
    try:
        await _await_with_heartbeat(
            task=task,
            invocation_id=invocation_id,
            record=record,
            ledger=ledger,
            ledger_path=ledger_path,
            monitor_interval=float(runtime["monitor_interval_seconds"]),
            hard_timeout=float(runtime["hard_timeout_seconds"]),
            started_monotonic=started_monotonic,
        )
        status = "completed"
    except TimeoutError as caught:
        error = caught
        status = "hard_timeout"
    except BaseException as caught:
        error = caught
        status = "failed"

    elapsed = round(time.monotonic() - started_monotonic, 3)
    systemic = error is not None and _is_systemic(error)
    record.update(
        {
            "status": status,
            "finished_at": _utc_now(),
            "elapsed_seconds": elapsed,
            "systemic_failure": systemic,
            "runtime_summary": _runtime_summary(workspace),
        }
    )
    if error is not None:
        logs_root = output_root / "_launcher_logs"
        logs_root.mkdir(parents=True, exist_ok=True)
        exception_path = logs_root / f"{invocation_id}.exception.log"
        exception_path.write_text(
            "".join(
                traceback.format_exception(
                    type(error),
                    error,
                    error.__traceback__,
                )
            ),
            encoding="utf-8",
        )
        record["exception_type"] = type(error).__name__
        record["exception_log"] = str(exception_path)
    _write_json(ledger_path, ledger)
    print(
        json.dumps(
            {
                "event": "finished",
                "invocation": invocation_id,
                "status": status,
                "elapsed_seconds": elapsed,
                "systemic_failure": systemic,
                "mode": "persistent",
            },
            ensure_ascii=False,
        ),
        flush=True,
    )
    return status == "completed", systemic


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(__file__).with_name("case_manifest.json"),
    )
    parser.add_argument("--invocation", action="append", default=[])
    parser.add_argument("--dry-run", action="store_true")
    return parser


async def _main_async(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[2]
    manifest = _read_json(args.manifest.expanduser().resolve())
    runtime = manifest["runtime"]
    os.environ.update(
        {str(key): str(value) for key, value in runtime["environment"].items()}
    )
    profile = (repo_root / runtime["model_profile"]).resolve()
    output_root = (repo_root / runtime["output_root"]).resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    by_id = {item["id"]: item for item in manifest["invocations"]}
    selected_ids = args.invocation or [
        item["id"] for item in manifest["invocations"]
    ]
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
    ledger["launcher_mode"] = "persistent"
    failures = 0
    for invocation_id in selected_ids:
        ok, systemic = await _run_one(
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
            break
    ledger["last_updated_at"] = _utc_now()
    _write_json(ledger_path, ledger)
    return 1 if failures else 0


def main() -> int:
    return asyncio.run(_main_async(_parser().parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())
