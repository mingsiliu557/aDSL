from __future__ import annotations

from dataclasses import dataclass
import json
import os
from pathlib import Path
import signal
import subprocess
import sys


@dataclass(frozen=True)
class ExecutionResult:
    output_root: Path
    glb_path: Path
    urdf_path: Path | None
    render_paths: tuple[Path, ...]
    stdout: str
    stderr: str
    source_index_path: Path | None = None


class AssetExecutionError(RuntimeError):
    pass


class AssetInfrastructureError(AssetExecutionError):
    """The source ran, but an external execution service was unavailable."""


_INFRASTRUCTURE_ERROR_MARKERS = (
    "GpuRenderWorkerUnavailable",
    "GpuRenderJobFailed",
    "GpuRenderQueueError",
    "GPU render worker became unavailable",
)


def _stop_process_group(process: subprocess.Popen[str], *, grace_seconds: float = 1.0) -> None:
    """Best-effort, bounded cleanup for the executor and any descendants it spawned."""

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
        # Linux processes in uninterruptible disk sleep cannot exit until the
        # kernel I/O operation returns. Keep cleanup bounded; SIGKILL remains
        # pending and will be honored as soon as the process becomes runnable.
        pass


def execute_asset_source(
    source_path: str | Path,
    output_root: str | Path,
    *,
    render: bool = True,
    render_view_count: int = 8,
    render_elevation: float = 15.0,
    export_urdf: bool = True,
    timeout: float = 300.0,
    working_directory: str | Path | None = None,
) -> ExecutionResult:
    """Execute one generated aDSL program in an isolated child process."""

    source = Path(source_path).expanduser().resolve()
    output = Path(output_root).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    workdir = source.parent if working_directory is None else Path(working_directory).expanduser().resolve()
    if not workdir.is_dir():
        raise NotADirectoryError(workdir)
    if render and render_view_count < 1:
        raise ValueError("render_view_count must be at least 1")
    output.mkdir(parents=True, exist_ok=False)
    command = [
        sys.executable,
        str(Path(__file__).with_name("asset_executor.py")),
        "--source",
        str(source),
        "--output",
        str(output),
    ]
    if render:
        command.extend(
            [
                "--render",
                "--render-view-count",
                str(render_view_count),
                "--render-elevation",
                str(render_elevation),
            ]
        )
    if export_urdf:
        command.append("--urdf")
    process = subprocess.Popen(
        command,
        cwd=workdir,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        start_new_session=True,
    )
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException:
        _stop_process_group(process)
        raise
    completed = subprocess.CompletedProcess(
        command,
        process.returncode,
        stdout,
        stderr,
    )
    if completed.returncode != 0:
        error_type = (
            AssetInfrastructureError
            if any(
                marker in completed.stderr or marker in completed.stdout
                for marker in _INFRASTRUCTURE_ERROR_MARKERS
            )
            else AssetExecutionError
        )
        raise error_type(
            f"Generated source exited with code {completed.returncode}.\n"
            f"STDOUT:\n{completed.stdout}\nSTDERR:\n{completed.stderr}"
        )
    manifest_path = output / "execution.json"
    if not manifest_path.is_file():
        raise AssetExecutionError("Generated source did not write execution.json")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    glb_path = Path(manifest["glb_path"]).resolve()
    if not glb_path.is_file():
        raise AssetExecutionError(f"Generated GLB is missing: {glb_path}")
    urdf_value = manifest.get("urdf_path")
    urdf_path = None if urdf_value is None else Path(urdf_value).resolve()
    if urdf_path is not None and not urdf_path.is_file():
        raise AssetExecutionError(f"Generated URDF is missing: {urdf_path}")
    render_paths = tuple(sorted((output / "render").glob("*.png")))
    if render and not render_paths:
        raise AssetExecutionError("Rendering was requested but no PNG was produced")
    source_index_value = manifest.get("source_index_path")
    source_index_path = (
        Path(source_index_value).resolve() if source_index_value else None
    )
    if source_index_path is not None and not source_index_path.is_file():
        raise AssetExecutionError(f"Generated SourceIndex is missing: {source_index_path}")
    return ExecutionResult(
        output_root=output,
        glb_path=glb_path,
        urdf_path=urdf_path,
        render_paths=render_paths,
        stdout=completed.stdout,
        stderr=completed.stderr,
        source_index_path=source_index_path,
    )


__all__ = [
    "AssetExecutionError",
    "AssetInfrastructureError",
    "ExecutionResult",
    "execute_asset_source",
]
