import json
import os
from pathlib import Path
import sys
import time

import pytest

from adsl.agents.checkers import run_checkers
from adsl.agents.models import AnalysisContext, CheckerSpec
from adsl.agents.service import _engineering_feedback
from adsl.agents.utils.execution import ExecutionResult
from experiments.standing_fea_30 import run_batch as batch


@pytest.mark.parametrize("ignore_parent_term", [False, True])
def test_timeout_cleans_descendants_records_error_and_continues(tmp_path, ignore_parent_term):
    source = tmp_path / "source.py"
    source.write_text("scene = None")
    script = tmp_path / "checker.py"
    script.write_text('''import json, os, pathlib, signal, subprocess, sys, time
out = pathlib.Path(sys.argv[2])
name = sys.argv[1]
if name == 'topology':
    if sys.argv[3] == 'True': signal.signal(signal.SIGTERM, signal.SIG_IGN)
    child = subprocess.Popen([sys.executable, '-c', 'import signal,time; signal.signal(signal.SIGTERM,signal.SIG_IGN); time.sleep(60)'])
    (out/'pids.json').write_text(json.dumps([os.getpid(), child.pid]))
    progress = pathlib.Path(os.environ['ADSL_GEOMETRY_PROGRESS_LOG'])
    progress.write_text(json.dumps({'event':'start','part':'structural_frame/seat_apron','operation':'OCC fuse','operand_count':23,'started_at':'test'})+'\\n')
    time.sleep(60)
(out/'result.json').write_text(json.dumps({'checker':name,'status':'PASS','summary':'ok'}))
''')
    execution = ExecutionResult(tmp_path, tmp_path / "scene.glb", None, (), "", "")
    specs = [CheckerSpec(name=name, timeout_seconds=1.5 if name == "topology" else 5.0,
        command=[sys.executable, str(script), name, "{output_dir}", str(ignore_parent_term)])
        for name in ("topology", "standing", "fea")]
    started = time.monotonic()
    runs = run_checkers(specs, execution=execution, source_path=source, round_root=tmp_path / "first")
    assert time.monotonic() - started < 8
    assert [run.result.status for run in runs] == ["ERROR", "PASS", "INDETERMINATE"]
    violation = runs[0].result.violations[0]
    assert violation["code"] == "GEOMETRY_PREPROCESS_TIMEOUT"
    assert violation["operand_count"] == 23
    assert violation["part"] == "structural_frame/seat_apron"
    assert runs[0].result.findings[0].category == "infrastructure_error"
    assert runs[2].command == ()
    for pid in json.loads((runs[0].output_dir / "pids.json").read_text()):
        path = Path(f"/proc/{pid}/stat")
        for _ in range(30):
            if not path.exists() or path.read_text().split()[2] == "Z":
                break
            time.sleep(0.02)
        else:
            pytest.fail(f"checker process {pid} survived timeout")
    following = run_checkers([specs[1]], execution=execution, source_path=source, round_root=tmp_path / "next")
    assert following[0].result.status == "PASS"


def test_final_evaluation_uses_shared_specs_and_continues(tmp_path, monkeypatch):
    runner = tmp_path / "runner.py"
    runner.write_text('''import json, os, pathlib, sys, time
name = sys.argv[1]; out = pathlib.Path(sys.argv[sys.argv.index('--output-dir')+1])
if name == 'topology':
    pathlib.Path(os.environ['ADSL_GEOMETRY_PROGRESS_LOG']).write_text(json.dumps({'event':'start','part':'seat','operation':'OCC fuse','operand_count':23})+'\\n')
    time.sleep(60)
(out/'result.json').write_text(json.dumps({'checker':name,'status':'PASS','summary':'ok'}))
''')
    specs = {}
    for name in ("topology", "standing", "fea"):
        path = tmp_path / f"{name}.json"
        path.write_text(CheckerSpec(name=name, timeout_seconds=1.0 if name == "topology" else 5.0,
            command=["{python}", str(runner), name, "--output-dir", "{output_dir}"]).model_dump_json())
        specs[name] = path
    monkeypatch.setattr(batch, "DEFAULT_TOPOLOGY_SPEC", specs["topology"])
    monkeypatch.setattr(batch, "DEFAULT_STANDING_SPEC", specs["standing"])
    monkeypatch.setattr(batch, "fea_spec_path", lambda case: specs["fea"])
    monkeypatch.setattr(batch, "run_logged", lambda *a, **kw: {"return_code": 0})
    for case_id in ("C01", "C02"):
        workspace = tmp_path / case_id
        workspace.mkdir()
        (workspace / "source.py").write_text("scene=None")
        result = batch.evaluate_final_source(
            case={"case_id": case_id}, arm="ours", workspace=workspace,
            output_root=tmp_path / "out", python=Path(sys.executable),
            checker_run=runner, asset_executor=runner, environment=dict(os.environ),
            timeout_seconds=21600,
        )
        assert result["status"] == "CHECKER_ERROR"
        checks = result["checkers"]
        assert checks["topology"]["process"]["timeout_seconds"] == 1.0
        assert checks["standing"]["result"]["status"] == "PASS"
        assert checks["fea"]["result"]["status"] == "INDETERMINATE"


def test_compact_feedback_preserves_localization_and_metric(tmp_path):
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerFinding, CheckerResult, MetricEvidence, SourceCandidate
    root = tmp_path / "rounds/round_01"
    finding = CheckerFinding(
        finding_id="fea:stress:1", rule_id="STRESS", category="physical_violation",
        metric=MetricEvidence(name="stress", value=60, unit="MPa", threshold=50, comparator="le"),
        source_candidates=[SourceCandidate(feature_id="seat", source_locations=["L66-L68"], method="direct")],
        domain={"repeated_raw_data": "x" * 10000},
    )
    result = CheckerResult(checker="fea", status="FAIL", summary="stress high", findings=[finding])
    run = CheckerRun(CheckerSpec(name="fea", command=["fake"]), result, root / "checkers/fea", ())
    context = AnalysisContext(source_sha256="a", geometry_sha256="b", checker_specs_sha256="c")
    payload = _engineering_feedback([run], context=context, localization=None,
        history=[{"accepted": False, "reason": "regression", "target_improvements": ["stress"], "scope_validation": {"large": "x"*10000}}],
        round_root=root, workspace=tmp_path)
    row = payload["typed_findings"][0]
    assert row["metric"]["threshold"] == 50
    assert row["source_candidates"][0]["source_locations"] == ["L66-L68"]
    assert row["result_ref"] == "rounds/round_01/checkers/fea/result.json"
    assert "domain" not in row
    assert payload["repair_history"][0]["target_improvements"] == ["stress"]
    assert len(json.dumps(payload)) < len(result.model_dump_json()) / 2
