from __future__ import annotations

from pathlib import Path
import signal
import subprocess
from unittest.mock import Mock

import pytest

from adsl.agents.utils import execution


def _source(tmp_path: Path) -> Path:
    path = tmp_path / "source.py"
    path.write_text("from adsl.core import *\n", encoding="utf-8")
    return path


def test_executor_starts_in_its_own_process_group(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    process = Mock()
    process.communicate.return_value = ("stdout", "stderr")
    process.returncode = 1
    popen = Mock(return_value=process)
    monkeypatch.setattr(execution.subprocess, "Popen", popen)

    with pytest.raises(execution.AssetExecutionError, match="exited with code 1"):
        execution.execute_asset_source(_source(tmp_path), tmp_path / "output")

    command = popen.call_args.args[0]
    assert command[0]
    assert command[1].endswith("asset_executor.py")
    assert "-m" not in command
    assert popen.call_args.kwargs["start_new_session"] is True
    process.communicate.assert_called_once_with(timeout=300.0)


def test_gpu_worker_failure_is_typed_as_infrastructure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    process = Mock()
    process.communicate.return_value = (
        "",
        "adsl.tools.gpu_render_queue.GpuRenderWorkerUnavailable: heartbeat lost",
    )
    process.returncode = 1
    monkeypatch.setattr(execution.subprocess, "Popen", Mock(return_value=process))

    with pytest.raises(execution.AssetInfrastructureError):
        execution.execute_asset_source(_source(tmp_path), tmp_path / "output")


@pytest.mark.parametrize("failure", [KeyboardInterrupt(), subprocess.TimeoutExpired(["executor"], 3.0)])
def test_executor_cleans_process_group_on_interruption(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure: BaseException,
) -> None:
    process = Mock()
    process.pid = 4321
    process.poll.return_value = None
    process.communicate.side_effect = failure
    process.wait.side_effect = [subprocess.TimeoutExpired(["executor"], 1.0), None]
    monkeypatch.setattr(execution.subprocess, "Popen", Mock(return_value=process))
    killpg = Mock()
    monkeypatch.setattr(execution.os, "killpg", killpg)

    with pytest.raises(type(failure)):
        execution.execute_asset_source(_source(tmp_path), tmp_path / "output")

    assert killpg.call_args_list == [
        ((4321, signal.SIGTERM),),
        ((4321, signal.SIGKILL),),
    ]
    assert process.wait.call_count == 2
