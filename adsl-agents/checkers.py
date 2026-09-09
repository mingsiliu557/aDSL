from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Iterable

from .models import CheckerResult, CheckerSpec
from .utils.execution import ExecutionResult
from .utils.io import write_json


class CheckerExecutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class CheckerRun:
    spec: CheckerSpec
    result: CheckerResult
    output_dir: Path
    command: tuple[str, ...]


def load_checker_spec(path: str | Path) -> CheckerSpec:
    config_path = Path(path).expanduser().resolve()
    payload = json.loads(config_path.read_text(encoding="utf-8"))
    return CheckerSpec.model_validate(payload)


def _terminate(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
        process.wait(timeout=2)
    except (ProcessLookupError, subprocess.TimeoutExpired):
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass


def _error_result(spec: CheckerSpec, summary: str) -> CheckerResult:
    return CheckerResult(
        checker=spec.name,
        status="ERROR",
        summary=summary,
        violations=[{"code": "CHECKER_INFRASTRUCTURE_ERROR", "message": summary}],
    )


def run_checker(
    spec: CheckerSpec,
    *,
    execution: ExecutionResult,
    source_path: Path,
    round_root: Path,
) -> CheckerRun:
    project_root = Path(__file__).resolve().parents[1]
    output_dir = round_root / "checkers" / spec.name
    output_dir.mkdir(parents=True, exist_ok=False)
    values = {
        "python": sys.executable,
        "project_root": str(project_root),
        "workspace": str(source_path.parent),
        "source": str(source_path.resolve()),
        "glb": str(execution.glb_path.resolve()),
        "urdf": "" if execution.urdf_path is None else str(execution.urdf_path.resolve()),
        "asset_dir": str(execution.glb_path.parent.resolve()),
        "round_dir": str(round_root.resolve()),
        "output_dir": str(output_dir.resolve()),
    }
    try:
        command = tuple(token.format_map(values) for token in spec.command)
    except (KeyError, ValueError) as error:
        result = _error_result(spec, f"Invalid checker command template: {error}")
        write_json(output_dir / "result.json", result.model_dump())
        return CheckerRun(spec, result, output_dir, ())

    environment = os.environ.copy()
    environment_prepend: dict[str, list[str]] = {}
    for name, raw_paths in spec.prepend_environment.items():
        paths = [str(Path(value.format_map(values)).expanduser().resolve()) for value in raw_paths]
        environment_prepend[name] = paths
        current = environment.get(name, "")
        environment[name] = os.pathsep.join([*paths, *([current] if current else [])])
    write_json(
        output_dir / "invocation.json",
        {
            "checker": spec.name,
            "required": spec.required,
            "timeout_seconds": spec.timeout_seconds,
            "command": list(command),
            "environment_prepend": environment_prepend,
            "placeholders": values,
        },
    )
    process = subprocess.Popen(
        command,
        cwd=project_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=spec.timeout_seconds)
    except subprocess.TimeoutExpired:
        _terminate(process)
        stdout, stderr = process.communicate()
        (output_dir / "stdout.log").write_text(stdout, encoding="utf-8")
        (output_dir / "stderr.log").write_text(stderr, encoding="utf-8")
        result = _error_result(
            spec, f"Checker timed out after {spec.timeout_seconds:g} seconds"
        )
        write_json(output_dir / "result.json", result.model_dump())
        return CheckerRun(spec, result, output_dir, command)
    except BaseException:
        _terminate(process)
        raise

    (output_dir / "stdout.log").write_text(stdout, encoding="utf-8")
    (output_dir / "stderr.log").write_text(stderr, encoding="utf-8")
    result_path = output_dir / "result.json"
    if process.returncode != 0:
        result = _error_result(
            spec,
            f"Checker exited with code {process.returncode}; inspect stdout.log and stderr.log",
        )
        write_json(result_path, result.model_dump())
    elif not result_path.is_file():
        result = _error_result(spec, "Checker did not write output_dir/result.json")
        write_json(result_path, result.model_dump())
    else:
        try:
            result = CheckerResult.model_validate_json(result_path.read_text(encoding="utf-8"))
            if result.checker != spec.name:
                raise ValueError(
                    f"result checker {result.checker!r} does not match spec {spec.name!r}"
                )
        except Exception as error:
            result = _error_result(spec, f"Invalid checker result: {type(error).__name__}: {error}")
            write_json(result_path, result.model_dump())
    return CheckerRun(spec, result, output_dir, command)


def run_checkers(
    specs: Iterable[CheckerSpec],
    *,
    execution: ExecutionResult,
    source_path: Path,
    round_root: Path,
) -> list[CheckerRun]:
    return [
        run_checker(
            spec,
            execution=execution,
            source_path=source_path,
            round_root=round_root,
        )
        for spec in specs
    ]


def required_checker_failures(runs: Iterable[CheckerRun]) -> list[CheckerRun]:
    return [run for run in runs if run.spec.required and run.result.status != "PASS"]


def required_checker_errors(runs: Iterable[CheckerRun]) -> list[CheckerRun]:
    return [run for run in runs if run.spec.required and run.result.status == "ERROR"]


__all__ = [
    "CheckerExecutionError",
    "CheckerRun",
    "load_checker_spec",
    "required_checker_errors",
    "required_checker_failures",
    "run_checker",
    "run_checkers",
]
