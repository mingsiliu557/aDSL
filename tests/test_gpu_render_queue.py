from __future__ import annotations

import json
from pathlib import Path
import sys
import threading
import time

import pytest

from adsl.tools import gpu_render_queue as queue


def _wait_until(predicate, timeout: float = 3.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("condition did not become true before timeout")
        time.sleep(0.01)


def _write_fake_renderer(path: Path) -> Path:
    path.write_text(
        """from __future__ import annotations
import fcntl
import json
from pathlib import Path
import sys
import time

output = Path(sys.argv[1])
job_id = sys.argv[2]
events = Path(sys.argv[3])
lock_path = Path(sys.argv[4])
sleep_seconds = float(sys.argv[5])
exit_code = int(sys.argv[6])
output.mkdir(parents=True, exist_ok=True)
with lock_path.open("a+") as lock_handle:
    try:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        with events.open("a", encoding="utf-8") as handle:
            handle.write(f"overlap {job_id}\\n")
        raise SystemExit(97)
    with events.open("a", encoding="utf-8") as handle:
        handle.write(f"start {job_id}\\n")
    time.sleep(sleep_seconds)
    if exit_code == 0:
        for index in range(8):
            (output / f"render_{index:04d}.png").write_bytes(b"fake-png")
        (output / "meta.json").write_text(json.dumps({"job_id": job_id}))
    with events.open("a", encoding="utf-8") as handle:
        handle.write(f"end {job_id}\\n")
    raise SystemExit(exit_code)
""",
        encoding="utf-8",
    )
    return path


def _builder(
    script: Path,
    events: Path,
    renderer_lock: Path,
    *,
    sleep_seconds: float = 0.15,
    exit_code: int = 0,
):
    def build(request):
        return [
            sys.executable,
            str(script),
            str(request["output_dir"]),
            str(request["job_id"]),
            str(events),
            str(renderer_lock),
            str(sleep_seconds),
            str(exit_code),
        ]

    return build


def _start_worker(queue_root: Path, **kwargs):
    result: list[int] = []
    errors: list[BaseException] = []

    def target() -> None:
        try:
            result.append(
                queue.serve_queue(
                    queue_root=queue_root,
                    poll_interval=0.02,
                    idle_timeout=5.0,
                    require_gpu=False,
                    **kwargs,
                )
            )
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=target, daemon=True)
    thread.start()
    _wait_until(lambda: queue.worker_is_live(queue_root))
    return thread, result, errors


def _dummy_glb(path: Path) -> Path:
    path.write_bytes(b"fake-glb")
    return path


def test_second_submission_waits_for_first_renderer_exit(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    events = tmp_path / "events.log"
    renderer_lock = tmp_path / "renderer.lock"
    fake_renderer = _write_fake_renderer(tmp_path / "fake_renderer.py")
    thread, result, errors = _start_worker(
        queue_root,
        max_jobs=2,
        render_command_builder=_builder(fake_renderer, events, renderer_lock),
    )

    first = queue.enqueue_render_job(
        queue_root=queue_root,
        glb_path=_dummy_glb(tmp_path / "first.glb"),
        output_dir=tmp_path / "first-output",
    )
    _wait_until(lambda: bool(list((queue_root / "running").glob("*.json"))))
    second = queue.enqueue_render_job(
        queue_root=queue_root,
        glb_path=_dummy_glb(tmp_path / "second.glb"),
        output_dir=tmp_path / "second-output",
    )

    first_result = queue.wait_render_job(queue_root=queue_root, job_id=first)
    second_result = queue.wait_render_job(queue_root=queue_root, job_id=second)
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert errors == []
    assert result == [0]
    assert first_result["status"] == "completed"
    assert second_result["status"] == "completed"
    assert first_result["background"] == "transparent"
    assert first_result["material_mode"] == "native"
    assert events.read_text(encoding="utf-8").splitlines() == [
        f"start {first}",
        f"end {first}",
        f"start {second}",
        f"end {second}",
    ]


def test_render_command_forwards_evaluation_appearance(tmp_path: Path) -> None:
    request = {
        "glb_path": str(tmp_path / "scene.glb"),
        "output_dir": str(tmp_path / "render"),
        "width": 1024,
        "height": 1024,
        "elevations": [15.0],
        "num_camera_per_layer": 8,
        "render_samples": 256,
        "background": "white",
        "material_mode": "neutral",
    }

    command = queue._render_command(request, python_executable="python")

    assert command[command.index("--background") + 1] == "white"
    assert command[command.index("--material-mode") + 1] == "neutral"


def test_wait_ignores_one_transient_worker_heartbeat_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_root = queue.initialize_queue(tmp_path / "queue")
    job_id = "transient-heartbeat"
    live_states = iter((False, True))
    monkeypatch.setattr(queue, "worker_is_live", lambda _root: next(live_states))

    def complete_during_sleep(_seconds: float) -> None:
        (queue_root / "completed" / f"{job_id}.json").write_text(
            json.dumps({"job_id": job_id, "status": "completed"}),
            encoding="utf-8",
        )

    monkeypatch.setattr(queue.time, "sleep", complete_during_sleep)

    result = queue.wait_render_job(
        queue_root=queue_root,
        job_id=job_id,
        timeout=1.0,
        poll_interval=0.01,
        worker_unavailable_grace=0.1,
    )

    assert result["status"] == "completed"
    assert not (queue_root / "cancelled" / f"{job_id}.json").exists()


def test_enqueue_ignores_one_transient_worker_heartbeat_read(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue_root = queue.initialize_queue(tmp_path / "queue")
    live_states = iter((False, True))
    monkeypatch.setattr(queue, "worker_is_live", lambda _root: next(live_states))
    monkeypatch.setattr(queue.time, "sleep", lambda _seconds: None)

    job_id = queue.enqueue_render_job(
        queue_root=queue_root,
        glb_path=_dummy_glb(tmp_path / "scene.glb"),
        output_dir=tmp_path / "output",
        worker_unavailable_grace=0.1,
    )

    assert (queue_root / "pending" / f"{job_id}.json").is_file()


def test_renderer_hard_timeout_fails_job_without_retry(tmp_path: Path) -> None:
    queue_root = tmp_path / "queue"
    events = tmp_path / "events.log"
    fake_renderer = _write_fake_renderer(tmp_path / "fake_renderer.py")
    thread, result, errors = _start_worker(
        queue_root,
        max_jobs=1,
        job_timeout=0.08,
        render_command_builder=_builder(
            fake_renderer,
            events,
            tmp_path / "renderer.lock",
            sleep_seconds=2.0,
        ),
    )
    job_id = queue.enqueue_render_job(
        queue_root=queue_root,
        glb_path=_dummy_glb(tmp_path / "scene.glb"),
        output_dir=tmp_path / "output",
    )

    with pytest.raises(queue.GpuRenderJobFailed, match="hard timeout"):
        queue.wait_render_job(queue_root=queue_root, job_id=job_id)
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert errors == []
    assert result == [0]
    assert events.read_text(encoding="utf-8").splitlines() == [f"start {job_id}"]
    assert len(list((queue_root / "failed").glob("*.json"))) == 1


def test_memory_not_quiescent_blocks_worker_and_preserves_pending_job(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue_root = tmp_path / "queue"
    events = tmp_path / "events.log"
    fake_renderer = _write_fake_renderer(tmp_path / "fake_renderer.py")
    memory_calls = 0

    def fake_memory() -> int:
        nonlocal memory_calls
        memory_calls += 1
        return 100 if memory_calls == 1 else 500

    monkeypatch.setattr(queue, "_gpu_inventory", lambda: "GPU 0: Fake NVIDIA GPU")
    monkeypatch.setattr(queue, "_gpu_memory_used_mib", fake_memory)

    result: list[int] = []
    thread = threading.Thread(
        target=lambda: result.append(
            queue.serve_queue(
                queue_root=queue_root,
                poll_interval=0.02,
                idle_timeout=5.0,
                job_timeout=1.0,
                quiesce_timeout=0.06,
                memory_tolerance_mib=100,
                require_gpu=True,
                render_command_builder=_builder(
                    fake_renderer,
                    events,
                    tmp_path / "renderer.lock",
                    sleep_seconds=0.05,
                ),
            )
        ),
        daemon=True,
    )
    thread.start()
    _wait_until(lambda: queue.worker_is_live(queue_root))

    first = queue.enqueue_render_job(
        queue_root=queue_root,
        glb_path=_dummy_glb(tmp_path / "first.glb"),
        output_dir=tmp_path / "first-output",
    )
    _wait_until(lambda: bool(list((queue_root / "running").glob("*.json"))))
    second = queue.enqueue_render_job(
        queue_root=queue_root,
        glb_path=_dummy_glb(tmp_path / "second.glb"),
        output_dir=tmp_path / "second-output",
    )
    thread.join(timeout=3.0)

    assert not thread.is_alive()
    assert result == [2]
    failed = json.loads(
        (queue_root / "failed" / f"{first}.json").read_text(encoding="utf-8")
    )
    assert "did not return to baseline" in failed["error"]
    assert (queue_root / "pending" / f"{second}.json").is_file()
    status = queue.queue_status(queue_root)
    assert status["worker"]["status"] == "blocked"
    assert status["worker_live"] is False
    assert events.read_text(encoding="utf-8").splitlines() == [
        f"start {first}",
        f"end {first}",
    ]
