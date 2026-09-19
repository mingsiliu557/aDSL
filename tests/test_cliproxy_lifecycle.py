"""Exercise experiment launchers with a fake proxy and Python; no network."""

import os
from pathlib import Path
import subprocess

import pytest


REPO = Path(__file__).resolve().parents[1]
LMS = "/vepfs_default/chanxueyan/lhp/lms"


@pytest.mark.parametrize("experiment", ["fixed_assembly_prompt", "fixed_assembly_existing"])
@pytest.mark.parametrize("proxy_status,runner_status,expected_status", [
    pytest.param(1, 0, 1, id="proxy-not-running"),
    pytest.param(0, 0, 0, id="runner-success"),
    pytest.param(0, 7, 7, id="runner-failure"),
])
def test_launcher_never_manages_proxy(
    tmp_path, experiment, proxy_status, runner_status, expected_status,
):
    fake_lms = tmp_path / "lms"
    (fake_lms / "aDSL").mkdir(parents=True)
    actions = tmp_path / "actions"
    (fake_lms / ".bashrc").write_text('''
cliproxy_status() {
    echo status >> "$TEST_ACTIONS"
    return "$TEST_PROXY_STATUS"
}
cliproxy_use() { echo use >> "$TEST_ACTIONS"; }
cliproxy_start() { echo start >> "$TEST_ACTIONS"; }
cliproxy_stop() { echo stop >> "$TEST_ACTIONS"; }
''')
    fake_python = fake_lms / "envs/adsl/bin/python"
    fake_python.parent.mkdir(parents=True)
    fake_python.write_text('''#!/bin/bash
if [[ "$1" == - ]]; then
    echo health >> "$TEST_ACTIONS"
    exit 0
fi
echo runner >> "$TEST_ACTIONS"
exit "$TEST_RUNNER_STATUS"
''')
    fake_python.chmod(0o755)
    script = (REPO / "experiments" / experiment / "launch.sh").read_text()
    script = script.replace(LMS, str(fake_lms))
    env = {
        **os.environ,
        "TEST_ACTIONS": str(actions),
        "TEST_PROXY_STATUS": str(proxy_status),
        "TEST_RUNNER_STATUS": str(runner_status),
    }
    env.pop("BASH_ENV", None)
    result = subprocess.run(
        ["bash", "--noprofile", "--norc", "-c", script],
        env=env, capture_output=True, text=True, timeout=10,
    )

    assert result.returncode == expected_status, result.stderr
    expected_actions = ["status"] if proxy_status else ["status", "use", "health", "runner"]
    assert actions.read_text().splitlines() == expected_actions
    if proxy_status:
        assert "Start CLIProxy in its own tmux first" in result.stderr
