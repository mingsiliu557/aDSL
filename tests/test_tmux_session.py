"""Exercise task-to-interactive-shell handoff on an isolated tmux server."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import time

import pytest


REPO = Path(__file__).resolve().parents[1]
HELPER = REPO / "experiments/tmux_session.sh"
pytestmark = pytest.mark.skipif(shutil.which("tmux") is None, reason="tmux unavailable")


def wait_until(predicate, timeout=8.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.05)
    raise AssertionError("isolated tmux condition was not met before timeout")


@pytest.fixture
def isolated_tmux():
    # A short socket directory avoids sockaddr_un limits. No command in this
    # fixture addresses the user's default server, including final cleanup.
    socket_root = Path(tempfile.mkdtemp(prefix="adsl-tmux-", dir="/tmp"))
    env = os.environ.copy()
    env.pop("TMUX", None)
    env.pop("TMUX_PANE", None)
    env["TMUX_TMPDIR"] = str(socket_root)

    def tmux(*args, check=True):
        return subprocess.run(
            ["tmux", *map(str, args)], env=env, text=True,
            capture_output=True, check=check, timeout=10,
        )

    try:
        # Starting once with no config isolates tests from user bindings and
        # default-shell settings; the helper connects to this same server.
        tmux("-f", "/dev/null", "new-session", "-d", "-s", "test_anchor",
             "bash", "--noprofile", "--norc", "-i")
        yield env, tmux
    finally:
        tmux("kill-server", check=False)
        shutil.rmtree(socket_root)


def launch(isolated_tmux, workdir, *command, session="task", check=True):
    env, _ = isolated_tmux
    return subprocess.run(
        ["bash", str(HELPER), session, str(workdir), *map(str, command)],
        env=env, cwd=REPO, text=True, capture_output=True, check=check, timeout=10,
    )


def pane_lines(tmux, session="task"):
    result = tmux("capture-pane", "-p", "-t", f"{session}:0.0", "-S", "-100")
    return [line.strip() for line in result.stdout.splitlines()]


def assert_returned_to_shell(tmux, expected_code, session="task"):
    pane = f"{session}:0.0"

    def code_matches():
        result = tmux("show-options", "-p", "-qv", "-t", pane,
                      "@adsl_task_exit_code", check=False)
        return result.returncode == 0 and result.stdout.strip() == str(expected_code)

    wait_until(code_matches)
    assert tmux("display-message", "-p", "-t", pane, "#{pane_dead}").stdout.strip() == "0"
    wait_until(lambda: any(
        line.startswith(f"[aDSL] task exited: rc={expected_code};")
        for line in pane_lines(tmux, session)
    ))
    marker = f"INTERACTIVE_{session}_{expected_code}"
    tmux("send-keys", "-t", pane, "-l", f"printf '%s\\n' '{marker}'")
    tmux("send-keys", "-t", pane, "C-m")
    wait_until(lambda: marker in pane_lines(tmux, session))
    # A later interactive command must not overwrite the recorded task status.
    assert tmux("show-options", "-p", "-qv", "-t", pane,
                "@adsl_task_exit_code").stdout.strip() == str(expected_code)


@pytest.mark.parametrize("exit_code", [0, 7])
def test_success_and_failure_leave_usable_shell(isolated_tmux, tmp_path, exit_code):
    launch(isolated_tmux, tmp_path, "bash", "-c", f"exit {exit_code}")
    assert_returned_to_shell(isolated_tmux[1], exit_code)


def test_builtin_exit_does_not_terminate_parent_shell(isolated_tmux, tmp_path):
    launch(isolated_tmux, tmp_path, "exit", "7")
    assert_returned_to_shell(isolated_tmux[1], 7)


def test_ctrl_c_stops_task_but_leaves_usable_shell(isolated_tmux, tmp_path):
    _, tmux = isolated_tmux
    launch(isolated_tmux, tmp_path, "bash", "-c", "printf 'TASK_READY\\n'; exec sleep 60")
    wait_until(lambda: "TASK_READY" in pane_lines(tmux))
    tmux("send-keys", "-t", "task:0.0", "C-c")
    assert_returned_to_shell(tmux, 130)


def test_argument_quoting_does_not_inject_commands(isolated_tmux, tmp_path):
    workdir = tmp_path / "directory with spaces"
    workdir.mkdir()
    arguments = ["two words", "$(touch injected_command)", "'; touch injected_quote; #"]
    launch(isolated_tmux, workdir, "bash", "-c", "printf 'ARG:<%s>\\n' \"$@\"", "_", *arguments)
    tmux = isolated_tmux[1]
    assert_returned_to_shell(tmux, 0)
    lines = pane_lines(tmux)
    for argument in arguments:
        assert f"ARG:<{argument}>" in lines
    assert not (workdir / "injected_command").exists()
    assert not (workdir / "injected_quote").exists()


def test_tee_keeps_output_and_original_failure_code(isolated_tmux, tmp_path):
    log_path = tmp_path / "task output.log"
    launch(
        isolated_tmux, tmp_path, "bash", "-o", "pipefail", "-c",
        "bash -c 'printf \"RECORDED_OUTPUT\\n\"; exit 9' | tee \"$1\"",
        "_", log_path,
    )
    assert_returned_to_shell(isolated_tmux[1], 9)
    assert log_path.read_text() == "RECORDED_OUTPUT\n"
    assert "RECORDED_OUTPUT" in pane_lines(isolated_tmux[1])


def test_session_collision_does_not_send_to_existing_pane(isolated_tmux, tmp_path):
    launch(isolated_tmux, tmp_path, "true")
    assert_returned_to_shell(isolated_tmux[1], 0)
    injected_file = tmp_path / "unexpected_second_task"
    result = launch(
        isolated_tmux, tmp_path, "bash", "-c", "touch \"$1\"", "_", injected_file,
        check=False,
    )
    assert result.returncode != 0
    assert not injected_file.exists()
    assert_returned_to_shell(isolated_tmux[1], 0)
