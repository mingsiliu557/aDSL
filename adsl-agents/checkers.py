from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
from typing import Iterable, Mapping

from .feedback_schema import canonicalize_result
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
    # The leader may exit while descendants still hold its resources.
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        # Uninterruptible kernel I/O cannot be reaped until it returns.
        pass


def _error_result(
    spec: CheckerSpec, summary: str, *, code: str = "CHECKER_INFRASTRUCTURE_ERROR",
    details: dict | None = None, artifacts: dict[str, str] | None = None,
) -> CheckerResult:
    return canonicalize_result(
        CheckerResult(
            checker=spec.name,
            status="ERROR",
            summary=summary,
            violations=[{"code": code, "message": summary, **(details or {})}],
            artifacts=artifacts or {},
        ),
        required=spec.required,
    )


def run_checker(
    spec: CheckerSpec,
    *,
    execution: ExecutionResult,
    source_path: Path,
    round_root: Path,
    environment: Mapping[str, str] | None = None,
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
        "analysis_geometry": (
            ""
            if execution.analysis_geometry_path is None
            else str(execution.analysis_geometry_path.resolve())
        ),
        "source_index": (
            ""
            if execution.source_index_path is None
            else str(execution.source_index_path.resolve())
        ),
    }
    try:
        command = tuple(token.format_map(values) for token in spec.command)
    except (KeyError, ValueError) as error:
        result = _error_result(spec, f"Invalid checker command template: {error}")
        write_json(output_dir / "result.json", result.model_dump())
        return CheckerRun(spec, result, output_dir, ())

    environment = dict(os.environ if environment is None else environment)
    progress_path = output_dir / "geometry_progress.jsonl"
    environment["ADSL_GEOMETRY_PROGRESS_LOG"] = str(progress_path.resolve())
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
    # Direct files avoid unbounded communicate() if descendants retain pipes.
    with (output_dir / "stdout.log").open("w") as stdout, (output_dir / "stderr.log").open("w") as stderr:
        process = subprocess.Popen(
            command, cwd=project_root, env=environment, stdout=stdout, stderr=stderr,
            text=True, start_new_session=True,
        )
        try:
            process.wait(timeout=spec.timeout_seconds)
        except subprocess.TimeoutExpired:
            _terminate(process)
            details = {}
            code = "CHECKER_TIMEOUT"
            if progress_path.is_file():
                try:
                    last = json.loads(progress_path.read_text().splitlines()[-1])
                    if last.get("event") == "start" and last.get("operation", "").startswith("OCC "):
                        code = "GEOMETRY_PREPROCESS_TIMEOUT"
                        details = {key: last[key] for key in ("part", "operation", "operand_count", "started_at") if key in last}
                        if details.get("part"):
                            details["part_names"] = [details["part"]]
                except (ValueError, IndexError, OSError):
                    pass
            result = _error_result(
                spec, f"Checker timed out after {spec.timeout_seconds:g} seconds",
                code=code, details=details,
                artifacts={"geometry_progress": str(progress_path)} if progress_path.is_file() else {},
            )
            write_json(output_dir / "result.json", result.model_dump())
            return CheckerRun(spec, result, output_dir, command)
        except BaseException:
            _terminate(process)
            raise
        else:
            # A finished checker must not leave background descendants running.
            _terminate(process)
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
            result = canonicalize_result(result, required=spec.required)
            write_json(result_path, result.model_dump())
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
    environment: Mapping[str, str] | None = None,
) -> list[CheckerRun]:
    runs: list[CheckerRun] = []
    for spec in specs:
        blocked = spec.name == "fea" and any(
            run.spec.name == "topology" and run.result.status == "ERROR"
            and any(v.get("code") == "GEOMETRY_PREPROCESS_TIMEOUT" for v in run.result.violations)
            for run in runs
        )
        if blocked:
            output_dir = round_root / "checkers" / spec.name
            output_dir.mkdir(parents=True, exist_ok=False)
            result = canonicalize_result(CheckerResult(
                checker=spec.name, status="INDETERMINATE",
                summary="FEA was not evaluated: shared topology geometry preprocessing timed out",
                violations=[{"code": "GEOMETRY_PREPROCESS_UNAVAILABLE", "dependency": "topology"}],
            ), required=spec.required)
            write_json(output_dir / "result.json", result.model_dump())
            runs.append(CheckerRun(spec, result, output_dir, ()))
            continue
        runs.append(run_checker(
            spec,
            execution=execution,
            source_path=source_path,
            round_root=round_root,
            **({"environment": environment} if environment is not None else {}),
        ))
    return runs


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
