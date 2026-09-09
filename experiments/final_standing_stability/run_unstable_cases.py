#!/usr/bin/env python3
"""Generate frozen standing-stability cases sequentially through aDSL."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
from typing import Any


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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--split", choices=("initial", "reserve"), default="initial")
    parser.add_argument("--model-config", type=Path, required=True)
    parser.add_argument("--adsl-run", type=Path, required=True)
    parser.add_argument("--codex-binary", type=Path, required=True)
    parser.add_argument("--queue-root", type=Path, required=True)
    parser.add_argument("--max-rounds", type=int, default=2)
    parser.add_argument("--continue-on-failure", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    cases = [case for case in manifest["cases"] if case["split"] == args.split]
    if not cases:
        parser.error(f"manifest has no {args.split} cases")
    if args.max_rounds < 1:
        parser.error("--max-rounds must be positive")

    from adsl.tools.gpu_render_queue import worker_is_live

    queue_root = args.queue_root.resolve()
    if not worker_is_live(queue_root):
        parser.error(f"GPU render worker is not live: {queue_root}")

    output_root = args.output_root.resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    logs_root = output_root.parent / "generation_logs" / args.split
    logs_root.mkdir(parents=True, exist_ok=True)
    collisions = [
        case["case_id"]
        for case in cases
        if (output_root / case["case_id"]).exists()
    ]
    if collisions:
        parser.error(f"refusing to overwrite existing case directories: {collisions}")

    environment = dict(os.environ)
    environment.update(
        {
            "ADSL_CODEX_CLI_BIN": str(args.codex_binary.resolve()),
            "ADSL_GPU_RENDER_QUEUE": str(queue_root),
            "ADSL_GPU_RENDER_WAIT_TIMEOUT_SECONDS": "3600",
            "ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS": "3660",
            "ADSL_RENDER_ENGINE": "BLENDER_EEVEE",
            "ADSL_RENDER_WIDTH": "1024",
            "ADSL_RENDER_HEIGHT": "1024",
            "ADSL_RENDER_SAMPLES": "256",
        }
    )
    status: dict[str, Any] = {
        "started_at": utc_now(),
        "finished_at": None,
        "split": args.split,
        "manifest": str(args.manifest.resolve()),
        "model_config": str(args.model_config.resolve()),
        "queue_root": str(queue_root),
        "max_rounds": args.max_rounds,
        "cases": [],
    }
    status_path = output_root.parent / f"generation_status_{args.split}.json"
    write_json(status_path, status)

    final_return_code = 0
    for case in cases:
        case_id = case["case_id"]
        case_output = output_root / case_id
        stdout_path = logs_root / f"{case_id}.stdout.log"
        stderr_path = logs_root / f"{case_id}.stderr.log"
        command = [
            str(args.adsl_run.resolve()),
            "--model-config",
            str(args.model_config.resolve()),
            "create",
            case["prompt"],
            "--output",
            str(case_output),
            "--task-id",
            f"standing-instability-{case_id}",
            "--max-rounds",
            str(args.max_rounds),
        ]
        row = {
            "case_id": case_id,
            "started_at": utc_now(),
            "finished_at": None,
            "return_code": None,
            "output": str(case_output),
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
        }
        status["cases"].append(row)
        write_json(status_path, status)
        with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
            "w", encoding="utf-8"
        ) as stderr:
            completed = subprocess.run(
                command,
                check=False,
                env=environment,
                stdout=stdout,
                stderr=stderr,
                text=True,
            )
        row["finished_at"] = utc_now()
        row["return_code"] = completed.returncode
        if completed.returncode == 0:
            write_json(case_output / "experiment_case.json", case)
        else:
            final_return_code = completed.returncode or 1
        write_json(status_path, status)
        if final_return_code and not args.continue_on_failure:
            break

    status["finished_at"] = utc_now()
    status["return_code"] = final_return_code
    write_json(status_path, status)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return final_return_code


if __name__ == "__main__":
    raise SystemExit(main())
