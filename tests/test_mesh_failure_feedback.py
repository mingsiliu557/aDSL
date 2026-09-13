"""Bounded synthetic failures only: no real mesher, solver, renderer or API."""
import asyncio
import json
import sys

import pytest

from adsl.agents.checkers import CheckerRun, run_checkers
from adsl.agents.feedback_schema import canonicalize_result
from adsl.agents.models import CheckerResult, CheckerSpec, RepairPolicy, SourceCandidate
from adsl.agents.repair_policy import assess_candidate
from adsl.agents.service import _actionable_findings, _checker_evidence, _CandidateOutcome
from adsl.agents.utils.execution import ExecutionResult
from experiments.workflow_checkers.run import enrich_result, fea_result
from test_checker_fault_isolation import result, run, workflow_fixture


def mesh_result(code="MESH_TIMEOUT", located=True):
    r = canonicalize_result(CheckerResult(checker="fea", status="INDETERMINATE",
        summary="Mesh analysis unavailable; FEA unverified; cause undetermined",
        violations=[{"code": code, "stage": "mesh_generation", "part_names": ["cabinet/knots"]}]), required=True)
    if located:
        r.findings[0].source_candidates = [SourceCandidate(feature_id="knots", source_ids=["s1"], method="direct")]
    return r


@pytest.mark.parametrize("code", ["MESH_INVALID", "MESH_GENERATION_FAILED", "MESH_TIMEOUT"])
@pytest.mark.parametrize("located", [False, True])
def test_only_resolved_mesh_evidence_can_repair(tmp_path, code, located):
    r = mesh_result(code, located)
    check = CheckerRun(CheckerSpec(name="fea", command=["unused"]), r, tmp_path, ())
    assert bool(_actionable_findings(check)) == located
    r.findings[0].domain["full_log"] = "LOG" * 10000
    evidence = _checker_evidence([check], workspace=tmp_path)
    assert bool(evidence["typed_findings"]) == located
    assert "LOGLOG" not in json.dumps(evidence)
    assert len(json.dumps(evidence)) < 3500
    if located:
        r.findings[0].source_candidates[0].ambiguous = True
        assert not _actionable_findings(check)


@pytest.mark.parametrize("after,visual,standing,accept", [
    ("PASS", True, "PASS", True), ("FAIL", True, "PASS", False),
    ("INDETERMINATE", True, "PASS", False), ("PASS", False, "PASS", False),
    ("PASS", True, "FAIL", False), ("PASS", True, "ERROR", False),
])
def test_mesh_recovery_keeps_regression_guards(after, visual, standing, accept):
    before = mesh_result()
    decision = assess_candidate([before, result("standing", "PASS")],
        [result("fea", after), result("standing", standing)],
        target_finding_ids=[before.findings[0].finding_id], appearance_approved=visual, policy=RepairPolicy())
    assert decision.accepted == accept


def test_mesh_generation_failure_is_unverified(tmp_path):
    raw = {"status": "NOT_MESHABLE", "mesh_levels": [{"mesh_failure": {
        "code": "MESH_GENERATION_FAILED", "stage": "mesh_generation", "message": "native mesher failed"}}]}
    r = enrich_result(fea_result(raw, tmp_path / "result.json"))
    assert r.status == "INDETERMINATE"
    assert r.findings[0].repairability == "analysis"


def test_native_mesh_error_stops_before_solver(tmp_path, monkeypatch):
    import numpy as np
    from experiments.load_bearing_structural_performance import analyze as fea
    monkeypatch.setattr(fea, 'all_bounds', lambda _: np.array([[0,0,0],[1,1,1]]))
    def fail(*args):
        raise fea.TOPOLOGY.MeshGenerationError('Could not mesh surface 55')
    monkeypatch.setattr(fea, 'build_occ_mesh', fail)
    row=fea.analyze_mesh_level('x',tmp_path,[],1,'coarse',.1,{}, {},tmp_path/'ccx',tmp_path,1)
    r=fea_result({'status':'NOT_MESHABLE','mesh_levels':[row]},tmp_path/'result.json')
    assert r.status=='INDETERMINATE'
    assert r.violations[0]['code']=='MESH_GENERATION_FAILED'


def test_mesh_timeout_preserves_asset_and_independent_check(tmp_path):
    script = tmp_path / "fake.py"
    script.write_text('''import json,os,pathlib,sys,time
out=pathlib.Path(sys.argv[1]); name=sys.argv[2]
if name=='fea':
 pathlib.Path(os.environ['ADSL_GEOMETRY_PROGRESS_LOG']).write_text(json.dumps({'event':'start','operation':'Gmsh mesh.generate','surface_bounds_source':{'55':[[0,0,0],[1,1,1]]}})+'\\n')
 print('Warning : 816 elements remain invalid in surface 55',file=sys.stderr,flush=True)
 time.sleep(30)
(out/'result.json').write_text(json.dumps(dict(checker=name,status='PASS',summary='ok')))
''')
    asset = tmp_path / "scene.glb"
    asset.write_bytes(b"original asset")
    specs = [CheckerSpec(name=n, command=[sys.executable, str(script), "{output_dir}", n],
                         timeout_seconds=.5 if n == "fea" else 5) for n in ("fea", "standing")]
    rows = run_checkers(specs, execution=ExecutionResult(tmp_path, asset, None, (), "", ""),
                        source_path=tmp_path / "source.py", round_root=tmp_path / "round")
    assert [r.result.status for r in rows] == ["INDETERMINATE", "PASS"]
    assert rows[0].result.violations[0]["code"] == "MESH_TIMEOUT"
    assert rows[0].result.findings[0].region.bounds == [[0,0,0],[1,1,1]]
    assert asset.read_bytes() == b"original asset"
    assert not _actionable_findings(rows[0])  # no resolved source index yet


def test_exhausted_mesh_budget_saves_unverified_asset(tmp_path, monkeypatch):
    from unittest.mock import AsyncMock
    mesh = CheckerRun(CheckerSpec(name="fea", command=["unused"]), mesh_result(), tmp_path / "fea", ())
    workflow, kwargs, manifest, roles, _ = workflow_fixture(tmp_path, monkeypatch,
        [run(tmp_path, "standing", "PASS"), mesh])
    workflow._attempt_engineering_candidates = AsyncMock(return_value=_CandidateOutcome(
        None, (), False, ({"stage": "repair_budget", "reason": "maximum total candidate budget reached"},)))
    output = asyncio.run(workflow._iterate(**kwargs))
    assert output.glb_path.is_file() and not output.approved
    assert manifest["status"] == "completed" and manifest["unverified_checks"] == ["fea"]
    workflow._repair.assert_not_awaited()
