"""Exercise the installed console-script path, without pytest's repository sys.path."""
import os
from pathlib import Path
import subprocess
import sys


def test_protection_analyzer_import_without_repository_on_pythonpath(tmp_path):
    repo = Path(__file__).resolve().parents[1]
    script = '''
import importlib.util
from pathlib import Path
from adsl.agents.overhang_edit import _support_analysis
assert importlib.util.find_spec("experiments") is None
analyzer = _support_analysis()
assert Path(analyzer.__file__).resolve() == Path(__import__("sys").argv[1]).resolve()
assert callable(analyzer.FINAL.parse_urdf)
assert callable(analyzer.face_selector)
assert _support_analysis() is analyzer
assert importlib.util.find_spec("experiments") is None
'''
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    completed = subprocess.run([sys.executable, "-I", "-c", script,
        str(repo / "experiments/support_requirement_critical_surfaces/analyze.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=30)
    assert completed.returncode == 0, completed.stderr
