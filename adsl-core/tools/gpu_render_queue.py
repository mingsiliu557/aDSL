from __future__ import annotations

import argparse
from datetime import datetime, timezone
import fcntl
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import time
from typing import Any, Callable, Mapping, Sequence
import uuid


QUEUE_SCHEMA_VERSION = 1
DEFAULT_WORKER_STALE_SECONDS = 30.0
RenderCommandBuilder = Callable[[Mapping[str, Any]], Sequence[str]]


class GpuRenderQueueError(RuntimeError):
    pass


class GpuRenderWorkerUnavailable(GpuRenderQueueError):
    pass


class GpuRenderJobFailed(GpuRenderQueueError):
    pass


class GpuMemoryNotQuiescent(GpuRenderQueueError):
    """The renderer exited, but the allocation is not safe for another job."""


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _write_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(dict(payload), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _queue_root(value: str | Path) -> Path:
    return Path(value).expanduser().resolve()


def initialize_queue(value: str | Path) -> Path:
    root = _queue_root(value)
    for name in (
        "incoming",
        "pending",
        "running",
        "completed",
        "failed",
        "cancelled",
        "logs",
        "state",
    ):
        (root / name).mkdir(parents=True, exist_ok=True)
    return root


def _worker_state(root: Path) -> dict[str, Any] | None:
    path = root / "state" / "worker.json"
    if not path.is_file():
        return None
    try:
        return _read_json(path)
    except (OSError, ValueError, TypeError):
        return None


def worker_is_live(
    queue_root: str | Path,
    *,
    stale_seconds: float = DEFAULT_WORKER_STALE_SECONDS,
) -> bool:
    root = initialize_queue(queue_root)
    state = _worker_state(root)
    if not state or state.get("status") not in {
        "starting",
        "idle",
        "rendering",
        "quiescing",
    }:
        return False
    try:
        heartbeat = _parse_time(str(state["heartbeat_at"]))
    except (KeyError, TypeError, ValueError):
        return False
    age = (datetime.now(timezone.utc) - heartbeat).total_seconds()
    return age <= float(stale_seconds)


def _positive_int(value: int, name: str) -> int:
    result = int(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def _positive_float(value: float, name: str) -> float:
    result = float(value)
    if result <= 0:
        raise ValueError(f"{name} must be positive")
    return result


def enqueue_render_job(
    *,
    queue_root: str | Path,
    glb_path: str | Path,
    output_dir: str | Path,
    width: int = 1024,
    height: int = 1024,
    render_samples: int = 256,
    elevations: Sequence[float] = (15.0,),
    num_camera_per_layer: int = 8,
    background: str = "transparent",
    material_mode: str = "native",
    require_worker: bool = True,
) -> str:
    root = initialize_queue(queue_root)
    source = Path(glb_path).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.glob("render_*.png")) or (output / "meta.json").exists():
        raise FileExistsError(f"render output already exists: {output}")
    if require_worker and not worker_is_live(root):
        raise GpuRenderWorkerUnavailable(
            f"GPU render worker is not live for queue: {root}"
        )

    width = _positive_int(width, "width")
    height = _positive_int(height, "height")
    render_samples = _positive_int(render_samples, "render_samples")
    num_camera_per_layer = _positive_int(
        num_camera_per_layer,
        "num_camera_per_layer",
    )
    elevation_values = [float(value) for value in elevations]
    if not elevation_values:
        raise ValueError("elevations must not be empty")
    if background not in {"transparent", "white", "gray"}:
        raise ValueError("background must be transparent, white, or gray")
    if material_mode not in {"native", "neutral"}:
        raise ValueError("material_mode must be native or neutral")

    job_id = f"{time.time_ns():020d}-{uuid.uuid4().hex[:12]}"
    request = {
        "schema_version": QUEUE_SCHEMA_VERSION,
        "job_id": job_id,
        "status": "pending",
        "submitted_at": _utc_now(),
        "submitter": {"host": socket.gethostname(), "pid": os.getpid()},
        "glb_path": str(source),
        "output_dir": str(output),
        "width": width,
        "height": height,
        "render_samples": render_samples,
        "elevations": elevation_values,
        "num_camera_per_layer": num_camera_per_layer,
        "background": background,
        "material_mode": material_mode,
    }
    incoming = root / "incoming" / f"{job_id}.json"
    pending = root / "pending" / f"{job_id}.json"
    _write_json(incoming, request)
    incoming.replace(pending)
    return job_id


def cancel_render_job(queue_root: str | Path, job_id: str, reason: str) -> Path:
    root = initialize_queue(queue_root)
    path = root / "cancelled" / f"{job_id}.json"
    _write_json(
        path,
        {
            "schema_version": QUEUE_SCHEMA_VERSION,
            "job_id": job_id,
            "cancelled_at": _utc_now(),
            "reason": reason,
        },
    )
    return path


def wait_render_job(
    *,
    queue_root: str | Path,
    job_id: str,
    timeout: float = 900.0,
    poll_interval: float = 0.2,
) -> dict[str, Any]:
    root = initialize_queue(queue_root)
    timeout = _positive_float(timeout, "timeout")
    poll_interval = _positive_float(poll_interval, "poll_interval")
    started = time.monotonic()
    while True:
        completed = root / "completed" / f"{job_id}.json"
        if completed.is_file():
            return _read_json(completed)
        failed = root / "failed" / f"{job_id}.json"
        if failed.is_file():
            payload = _read_json(failed)
            raise GpuRenderJobFailed(
                f"GPU render job {job_id} failed: {payload.get('error', 'unknown error')}"
            )
        elapsed = time.monotonic() - started
        if elapsed >= timeout:
            cancel_render_job(root, job_id, f"client timeout after {timeout:g}s")
            raise TimeoutError(f"GPU render job {job_id} timed out after {timeout:g}s")
        if not worker_is_live(root):
            cancel_render_job(root, job_id, "worker heartbeat became unavailable")
            raise GpuRenderWorkerUnavailable(
                f"GPU render worker became unavailable while waiting for {job_id}"
            )
        time.sleep(min(poll_interval, timeout - elapsed))


def submit_render_job(**kwargs: Any) -> dict[str, Any]:
    wait_timeout = float(kwargs.pop("wait_timeout", 900.0))
    poll_interval = float(kwargs.pop("poll_interval", 0.2))
    job_id = enqueue_render_job(**kwargs)

    previous_term_handler: Any = None
    installed_term_handler = False
    if hasattr(signal, "SIGTERM"):
        try:
            previous_term_handler = signal.getsignal(signal.SIGTERM)

            def _raise_interrupted(_signum: int, _frame: Any) -> None:
                raise InterruptedError("GPU render client received SIGTERM")

            signal.signal(signal.SIGTERM, _raise_interrupted)
            installed_term_handler = True
        except ValueError:
            pass
    try:
        return wait_render_job(
            queue_root=kwargs["queue_root"],
            job_id=job_id,
            timeout=wait_timeout,
            poll_interval=poll_interval,
        )
    except GpuRenderJobFailed:
        raise
    except (TimeoutError, GpuRenderWorkerUnavailable):
        # wait_render_job already wrote the precise cancellation reason.
        raise
    except BaseException as exc:
        cancel_render_job(kwargs["queue_root"], job_id, f"client interrupted: {exc}")
        raise
    finally:
        if installed_term_handler:
            signal.signal(signal.SIGTERM, previous_term_handler)


def _stop_process_group(
    process: subprocess.Popen[str],
    *,
    grace_seconds: float = 10.0,
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
        pass


def _gpu_inventory() -> str:
    result = subprocess.run(
        ["nvidia-smi", "-L"],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    return result.stdout.strip()


def _gpu_memory_used_mib() -> int | None:
    result = subprocess.run(
        [
            "nvidia-smi",
            "--query-gpu=memory.used",
            "--format=csv,noheader,nounits",
        ],
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        check=False,
    )
    if result.returncode != 0:
        return None
    values = []
    for line in result.stdout.splitlines():
        try:
            values.append(int(line.strip()))
        except ValueError:
            return None
    return sum(values) if values else None


def _render_command(
    request: Mapping[str, Any],
    *,
    python_executable: str,
) -> list[str]:
    return [
        python_executable,
        "-u",
        "-m",
        "adsl.tools.render",
        "--glb-path",
        str(request["glb_path"]),
        "--output-dir",
        str(request["output_dir"]),
        "--width",
        str(request["width"]),
        "--height",
        str(request["height"]),
        "--elevations",
        *[str(value) for value in request["elevations"]],
        "--num-camera-per-layer",
        str(request["num_camera_per_layer"]),
        "--render-samples",
        str(request["render_samples"]),
        "--background",
        str(request.get("background", "transparent")),
        "--material-mode",
        str(request.get("material_mode", "native")),
    ]


def _write_worker_state(
    root: Path,
    *,
    status: str,
    started_at: str,
    current_job: str | None,
    **fields: Any,
) -> None:
    _write_json(
        root / "state" / "worker.json",
        {
            "schema_version": QUEUE_SCHEMA_VERSION,
            "status": status,
            "host": socket.gethostname(),
            "pid": os.getpid(),
            "started_at": started_at,
            "heartbeat_at": _utc_now(),
            "current_job": current_job,
            **fields,
        },
    )


def _recover_interrupted_jobs(root: Path) -> None:
    for path in sorted((root / "running").glob("*.json")):
        try:
            request = _read_json(path)
        except Exception as exc:
            request = {"job_id": path.stem, "request_read_error": str(exc)}
        request.update(
            {
                "status": "failed",
                "failed_at": _utc_now(),
                "error": "worker restarted while job was marked running; not retried",
            }
        )
        _write_json(root / "failed" / path.name, request)
        path.unlink(missing_ok=True)


def _wait_for_gpu_quiescence(
    *,
    baseline_mib: int | None,
    tolerance_mib: int,
    timeout: float,
    poll_interval: float,
    heartbeat: Callable[[], None] | None = None,
) -> int | None:
    if baseline_mib is None:
        return None
    started = time.monotonic()
    while True:
        if heartbeat is not None:
            heartbeat()
        current = _gpu_memory_used_mib()
        if current is not None and current <= baseline_mib + tolerance_mib:
            return current
        if time.monotonic() - started >= timeout:
            raise GpuMemoryNotQuiescent(
                "GPU memory did not return to baseline after renderer exit: "
                f"baseline={baseline_mib} MiB current={current} MiB "
                f"tolerance={tolerance_mib} MiB"
            )
        time.sleep(poll_interval)


def _execute_request(
    *,
    root: Path,
    request: dict[str, Any],
    command: Sequence[str],
    job_timeout: float,
    poll_interval: float,
    started_at: str,
    baseline_mib: int | None,
    memory_tolerance_mib: int,
    quiesce_timeout: float,
) -> dict[str, Any]:
    job_id = str(request["job_id"])
    log_path = root / "logs" / f"{job_id}.log"
    cancel_path = root / "cancelled" / f"{job_id}.json"
    environment = os.environ.copy()
    environment.update(
        {
            "ADSL_RENDER_ENGINE": "BLENDER_EEVEE",
            "ADSL_RENDER_WIDTH": str(request["width"]),
            "ADSL_RENDER_HEIGHT": str(request["height"]),
            "ADSL_RENDER_SAMPLES": str(request["render_samples"]),
            "PYTHONUNBUFFERED": "1",
        }
    )
    started_monotonic = time.monotonic()
    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            list(command),
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
            env=environment,
            start_new_session=True,
        )
        cancelled = False
        timed_out = False
        while process.poll() is None:
            elapsed = time.monotonic() - started_monotonic
            if cancel_path.is_file():
                cancelled = True
                _stop_process_group(process)
                break
            if elapsed >= job_timeout:
                timed_out = True
                _stop_process_group(process)
                break
            _write_worker_state(
                root,
                status="rendering",
                started_at=started_at,
                current_job=job_id,
                renderer_pid=process.pid,
                renderer_elapsed_seconds=round(elapsed, 3),
            )
            time.sleep(min(poll_interval, max(0.05, job_timeout - elapsed)))
        returncode = process.poll()
        if returncode is None:
            returncode = process.wait()

    memory_after = _wait_for_gpu_quiescence(
        baseline_mib=baseline_mib,
        tolerance_mib=memory_tolerance_mib,
        timeout=quiesce_timeout,
        poll_interval=poll_interval,
        heartbeat=lambda: _write_worker_state(
            root,
            status="quiescing",
            started_at=started_at,
            current_job=job_id,
            renderer_pid=None,
            renderer_elapsed_seconds=round(
                time.monotonic() - started_monotonic,
                3,
            ),
        ),
    )
    if cancelled:
        raise GpuRenderJobFailed("job cancelled by client")
    if timed_out:
        raise TimeoutError(f"renderer hard timeout after {job_timeout:g}s")
    if returncode != 0:
        raise GpuRenderJobFailed(f"renderer exited with code {returncode}")

    output = Path(str(request["output_dir"]))
    render_paths = sorted(output.glob("render_*.png"))
    expected_count = len(request["elevations"]) * int(
        request["num_camera_per_layer"]
    )
    meta_path = output / "meta.json"
    if len(render_paths) != expected_count:
        raise GpuRenderJobFailed(
            f"renderer produced {len(render_paths)}/{expected_count} expected PNGs"
        )
    if any(path.stat().st_size == 0 for path in render_paths):
        raise GpuRenderJobFailed("renderer produced an empty PNG")
    if not meta_path.is_file() or meta_path.stat().st_size == 0:
        raise GpuRenderJobFailed("renderer did not produce a non-empty meta.json")
    return {
        **request,
        "status": "completed",
        "completed_at": _utc_now(),
        "elapsed_seconds": round(time.monotonic() - started_monotonic, 3),
        "renderer_returncode": returncode,
        "gpu_memory_baseline_mib": baseline_mib,
        "gpu_memory_after_mib": memory_after,
        "log_path": str(log_path),
        "render_paths": [str(path.resolve()) for path in render_paths],
        "meta_path": str(meta_path.resolve()),
    }


def serve_queue(
    *,
    queue_root: str | Path,
    python_executable: str = sys.executable,
    poll_interval: float = 0.5,
    idle_timeout: float = 3600.0,
    job_timeout: float = 600.0,
    quiesce_timeout: float = 60.0,
    memory_tolerance_mib: int = 256,
    require_gpu: bool = True,
    max_jobs: int | None = None,
    render_command_builder: RenderCommandBuilder | None = None,
) -> int:
    root = initialize_queue(queue_root)
    poll_interval = _positive_float(poll_interval, "poll_interval")
    idle_timeout = _positive_float(idle_timeout, "idle_timeout")
    job_timeout = _positive_float(job_timeout, "job_timeout")
    quiesce_timeout = _positive_float(quiesce_timeout, "quiesce_timeout")
    memory_tolerance_mib = _positive_int(
        memory_tolerance_mib,
        "memory_tolerance_mib",
    )
    if max_jobs is not None:
        max_jobs = _positive_int(max_jobs, "max_jobs")
    lock_path = root / "state" / "worker.lock"
    started_at = _utc_now()
    processed = 0
    with lock_path.open("a+", encoding="utf-8") as lock_handle:
        try:
            fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise GpuRenderQueueError("another GPU render worker owns the queue") from exc

        inventory = _gpu_inventory() if require_gpu else "GPU check disabled"
        if require_gpu and not any(
            line.startswith("GPU ") for line in inventory.splitlines()
        ):
            raise GpuRenderQueueError(f"no allocated NVIDIA GPU found: {inventory}")
        baseline_mib = _gpu_memory_used_mib() if require_gpu else None
        if require_gpu and baseline_mib is None:
            raise GpuRenderQueueError("could not query baseline GPU memory")

        _recover_interrupted_jobs(root)
        stop_path = root / "state" / "STOP_REQUESTED"
        stop_path.unlink(missing_ok=True)
        last_activity = time.monotonic()
        _write_worker_state(
            root,
            status="starting",
            started_at=started_at,
            current_job=None,
            gpu_inventory=inventory,
            gpu_memory_baseline_mib=baseline_mib,
        )

        final_status = "stopped"
        fatal_error: str | None = None
        try:
            while True:
                # A graceful stop finishes the current renderer, then leaves all
                # still-pending requests for the next worker invocation.
                if stop_path.is_file():
                    return 0
                pending = sorted((root / "pending").glob("*.json"))
                if not pending:
                    if time.monotonic() - last_activity >= idle_timeout:
                        return 0
                    _write_worker_state(
                        root,
                        status="idle",
                        started_at=started_at,
                        current_job=None,
                        processed_jobs=processed,
                        gpu_inventory=inventory,
                        gpu_memory_baseline_mib=baseline_mib,
                    )
                    time.sleep(poll_interval)
                    continue

                pending_path = pending[0]
                running_path = root / "running" / pending_path.name
                try:
                    pending_path.replace(running_path)
                except FileNotFoundError:
                    continue
                try:
                    request = _read_json(running_path)
                    job_id = str(request["job_id"])
                except (OSError, ValueError, TypeError, KeyError) as exc:
                    failed = {
                        "schema_version": QUEUE_SCHEMA_VERSION,
                        "job_id": running_path.stem,
                        "status": "failed",
                        "failed_at": _utc_now(),
                        "error": f"invalid queue request: {type(exc).__name__}: {exc}",
                    }
                    _write_json(root / "failed" / running_path.name, failed)
                    running_path.unlink(missing_ok=True)
                    processed += 1
                    continue
                last_activity = time.monotonic()

                if (root / "cancelled" / f"{job_id}.json").is_file():
                    failed = {
                        **request,
                        "status": "failed",
                        "failed_at": _utc_now(),
                        "error": "job was cancelled before rendering started",
                    }
                    _write_json(root / "failed" / running_path.name, failed)
                    running_path.unlink(missing_ok=True)
                    processed += 1
                    continue

                builder = render_command_builder or (
                    lambda value: _render_command(
                        value,
                        python_executable=python_executable,
                    )
                )
                memory_fault: GpuMemoryNotQuiescent | None = None
                try:
                    completed = _execute_request(
                        root=root,
                        request=request,
                        command=builder(request),
                        job_timeout=job_timeout,
                        poll_interval=poll_interval,
                        started_at=started_at,
                        baseline_mib=baseline_mib,
                        memory_tolerance_mib=memory_tolerance_mib,
                        quiesce_timeout=quiesce_timeout,
                    )
                except GpuMemoryNotQuiescent as exc:
                    memory_fault = exc
                    failed = {
                        **request,
                        "status": "failed",
                        "failed_at": _utc_now(),
                        "error": str(exc),
                        "log_path": str((root / "logs" / f"{job_id}.log").resolve()),
                    }
                    _write_json(root / "failed" / running_path.name, failed)
                except GpuRenderQueueError as exc:
                    failed = {
                        **request,
                        "status": "failed",
                        "failed_at": _utc_now(),
                        "error": str(exc),
                        "log_path": str((root / "logs" / f"{job_id}.log").resolve()),
                    }
                    _write_json(root / "failed" / running_path.name, failed)
                except (KeyboardInterrupt, SystemExit) as exc:
                    failed = {
                        **request,
                        "status": "failed",
                        "failed_at": _utc_now(),
                        "error": f"worker interrupted: {type(exc).__name__}: {exc}",
                        "log_path": str((root / "logs" / f"{job_id}.log").resolve()),
                    }
                    _write_json(root / "failed" / running_path.name, failed)
                    raise
                except Exception as exc:
                    failed = {
                        **request,
                        "status": "failed",
                        "failed_at": _utc_now(),
                        "error": f"{type(exc).__name__}: {exc}",
                        "log_path": str((root / "logs" / f"{job_id}.log").resolve()),
                    }
                    _write_json(root / "failed" / running_path.name, failed)
                else:
                    _write_json(root / "completed" / running_path.name, completed)
                finally:
                    running_path.unlink(missing_ok=True)
                    processed += 1

                if memory_fault is not None:
                    # Fail closed: never launch another Blender process on an
                    # allocation whose memory did not settle after process exit.
                    final_status = "blocked"
                    fatal_error = str(memory_fault)
                    return 2
                if max_jobs is not None and processed >= max_jobs:
                    return 0
        except BaseException as exc:
            final_status = "failed"
            fatal_error = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            _write_worker_state(
                root,
                status=final_status,
                started_at=started_at,
                current_job=None,
                stopped_at=_utc_now(),
                processed_jobs=processed,
                gpu_inventory=inventory,
                gpu_memory_baseline_mib=baseline_mib,
                fatal_error=fatal_error,
            )


def request_worker_stop(queue_root: str | Path) -> Path:
    root = initialize_queue(queue_root)
    path = root / "state" / "STOP_REQUESTED"
    path.write_text(f"requested_at={_utc_now()}\n", encoding="utf-8")
    return path


def queue_status(queue_root: str | Path) -> dict[str, Any]:
    root = initialize_queue(queue_root)
    return {
        "queue_root": str(root),
        "worker_live": worker_is_live(root),
        "worker": _worker_state(root),
        "counts": {
            name: len(list((root / name).glob("*.json")))
            for name in ("pending", "running", "completed", "failed", "cancelled")
        },
        "stop_requested": (root / "state" / "STOP_REQUESTED").is_file(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Persistent serial GPU Eevee render queue")
    subparsers = parser.add_subparsers(dest="command", required=True)

    init = subparsers.add_parser("init")
    init.add_argument("--queue-root", required=True)

    serve = subparsers.add_parser("serve")
    serve.add_argument("--queue-root", required=True)
    serve.add_argument("--python-executable", default=sys.executable)
    serve.add_argument("--poll-interval", type=float, default=0.5)
    serve.add_argument("--idle-timeout", type=float, default=3600.0)
    serve.add_argument("--job-timeout", type=float, default=600.0)
    serve.add_argument("--quiesce-timeout", type=float, default=60.0)
    serve.add_argument("--memory-tolerance-mib", type=int, default=256)
    serve.add_argument("--skip-gpu-check", action="store_true")

    submit = subparsers.add_parser("submit")
    submit.add_argument("--queue-root", required=True)
    submit.add_argument("--glb-path", required=True)
    submit.add_argument("--output-dir", required=True)
    submit.add_argument("--width", type=int, default=1024)
    submit.add_argument("--height", type=int, default=1024)
    submit.add_argument("--render-samples", type=int, default=256)
    submit.add_argument("--elevations", type=float, nargs="+", default=[15.0])
    submit.add_argument("--num-camera-per-layer", type=int, default=8)
    submit.add_argument(
        "--background",
        choices=("transparent", "white", "gray"),
        default="transparent",
    )
    submit.add_argument(
        "--material-mode",
        choices=("native", "neutral"),
        default="native",
    )
    submit.add_argument("--wait-timeout", type=float, default=900.0)

    status = subparsers.add_parser("status")
    status.add_argument("--queue-root", required=True)
    stop = subparsers.add_parser("stop")
    stop.add_argument("--queue-root", required=True)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.command == "init":
        print(initialize_queue(args.queue_root))
        return 0
    if args.command == "serve":
        return serve_queue(
            queue_root=args.queue_root,
            python_executable=args.python_executable,
            poll_interval=args.poll_interval,
            idle_timeout=args.idle_timeout,
            job_timeout=args.job_timeout,
            quiesce_timeout=args.quiesce_timeout,
            memory_tolerance_mib=args.memory_tolerance_mib,
            require_gpu=not args.skip_gpu_check,
        )
    if args.command == "submit":
        result = submit_render_job(
            queue_root=args.queue_root,
            glb_path=args.glb_path,
            output_dir=args.output_dir,
            width=args.width,
            height=args.height,
            render_samples=args.render_samples,
            elevations=args.elevations,
            num_camera_per_layer=args.num_camera_per_layer,
            background=args.background,
            material_mode=args.material_mode,
            wait_timeout=args.wait_timeout,
        )
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0
    if args.command == "status":
        print(json.dumps(queue_status(args.queue_root), indent=2, ensure_ascii=False))
        return 0
    if args.command == "stop":
        print(request_worker_stop(args.queue_root))
        return 0
    raise AssertionError(args.command)


if __name__ == "__main__":
    raise SystemExit(main())
