"""Shell lifecycle tests with a fake proxy; no network or experiments."""
import os
from pathlib import Path
import subprocess

import pytest

REPO = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize('mode,expected,stops', [
    ('success', 0, 1), ('failure', 7, 1),
    ('replacement', 0, 0), ('existing', 2, 0),
])
def test_proxy_owned_once(tmp_path, mode, expected, stops):
    (tmp_path / 'run').mkdir()
    bashrc = tmp_path / 'bashrc'
    bashrc.write_text('''
export LMS_STEPCODE_ROOT="$TEST_ROOT"
lms_proxy() {
  echo "$1" >> "$TEST_ROOT/actions"
  if [[ "$1" == start ]]; then echo $$ > "$TEST_ROOT/run/header-proxy.pid"; fi
}
''')
    fake_python = tmp_path / 'python'
    fake_python.write_text('''#!/bin/bash
echo "fake batch output"
case "$TEST_MODE" in
  failure) exit 7 ;;
  replacement) echo 99999999 > "$TEST_ROOT/run/header-proxy.pid" ;;
esac
''')
    fake_python.chmod(0o755)
    if mode == 'existing':
        (tmp_path / 'run/header-proxy.pid').write_text(str(os.getpid()))
    script = (REPO / 'experiments/standing_fea_30/run_batch.sh').read_text()
    script = script.replace(f'REPO={REPO}', f'REPO={tmp_path}')
    script = script.replace('LMS_BASHRC=/vepfs_default/chanxueyan/lhp/lms/.bashrc', f'LMS_BASHRC={bashrc}')
    script = script.replace('PYTHON=/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python', f'PYTHON={fake_python}')
    result = subprocess.run(['bash', '-c', script], env={**os.environ, 'TEST_ROOT': str(tmp_path), 'TEST_MODE': mode}, capture_output=True, text=True, timeout=10)
    assert result.returncode == expected, result.stderr
    actions = (tmp_path / 'actions').read_text().splitlines() if (tmp_path / 'actions').exists() else []
    assert actions.count('stop') == stops
    assert actions.count('start') == (mode != 'existing')


def test_console_and_log_keep_failure_code(tmp_path):
    log = tmp_path / 'batch.log'
    result = subprocess.run(['bash', '-o', 'pipefail', '-c',
        '{ echo stdout; echo stderr >&2; exit 7; } 2>&1 | tee -a "$1"', 'test', str(log)],
        capture_output=True, text=True, timeout=5)
    assert result.returncode == 7
    assert result.stdout == log.read_text() == 'stdout\nstderr\n'
