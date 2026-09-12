from __future__ import annotations

import json
from pathlib import Path
import sys

from adsl.agents.checkers import (
    load_checker_spec,
    required_checker_failures,
    run_checker,
)
from adsl.agents.cli import _parser
from adsl.agents.models import (
    CheckerResult,
    CheckerSpec,
    EngineeringCriticDecision,
)
from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.execution import ExecutionResult
from experiments.workflow_checkers.run import (
    fea_result,
    overhang_result,
    progressive_result,
    standing_result,
    support_result,
)


def test_checker_runner_expands_placeholders_and_reads_protocol(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    asset.mkdir()
    source = tmp_path / "source.py"
    source.write_text("scene = None\n", encoding="utf-8")
    glb = asset / "scene.glb"
    urdf = asset / "scene.urdf"
    glb.write_bytes(b"glb")
    urdf.write_text("<robot/>", encoding="utf-8")
    script = tmp_path / "fake_checker.py"
    script.write_text(
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[1])\n"
        "payload = {'checker':'fake','status':'FAIL','summary':'measured',"
        "'metrics':{'source':sys.argv[2]},'violations':[{'code':'WEAK'}]}\n"
        "(out/'result.json').write_text(json.dumps(payload))\n",
        encoding="utf-8",
    )
    execution = ExecutionResult(
        output_root=asset.parent,
        glb_path=glb,
        urdf_path=urdf,
        render_paths=(),
        stdout="",
        stderr="",
    )
    spec = CheckerSpec(
        name="fake",
        command=[sys.executable, str(script), "{output_dir}", "{source}"],
    )
    run = run_checker(
        spec,
        execution=execution,
        source_path=source,
        round_root=tmp_path / "round",
    )
    assert run.result.status == "FAIL"
    assert run.result.metrics["source"] == str(source.resolve())
    assert required_checker_failures([run]) == [run]
    assert (run.output_dir / "invocation.json").is_file()


def test_checker_runner_missing_output_is_recorded_as_error(tmp_path: Path) -> None:
    asset = tmp_path / "asset"
    asset.mkdir()
    source = tmp_path / "source.py"
    source.write_text("scene = None\n", encoding="utf-8")
    glb = asset / "scene.glb"
    glb.write_bytes(b"glb")
    script = tmp_path / "silent_checker.py"
    script.write_text("raise SystemExit(0)\n", encoding="utf-8")
    execution = ExecutionResult(
        output_root=asset.parent,
        glb_path=glb,
        urdf_path=None,
        render_paths=(),
        stdout="",
        stderr="",
    )
    spec = CheckerSpec(
        name="silent",
        command=[sys.executable, str(script)],
    )

    run = run_checker(
        spec,
        execution=execution,
        source_path=source,
        round_root=tmp_path / "round",
    )

    assert run.result.status == "ERROR"
    assert "did not write" in run.result.summary
    assert (run.output_dir / "result.json").is_file()


def test_checker_spec_load_and_cli_registration(tmp_path: Path) -> None:
    path = tmp_path / "checker.json"
    path.write_text(
        json.dumps({"name": "fea", "command": ["tool"], "required": True}),
        encoding="utf-8",
    )
    assert load_checker_spec(path).name == "fea"
    args = _parser().parse_args(
        [
            "edit",
            "repair",
            "--source",
            "source.py",
            "--output",
            "out",
            "--check-first",
            "--checker-config",
            str(path),
        ]
    )
    assert args.check_first is True
    assert args.checker_config == [path]


def test_engineering_decision_is_fail_closed() -> None:
    decision = EngineeringCriticDecision(
        approved=True,
        observations=["looks fine"],
        required_changes=[],
    )
    normalized = ObjectWorkflow._normalize_engineering_decision(
        decision, has_required_failures=True
    )
    assert normalized.approved is False


def test_standing_adapter_uses_fixed_25_degree_natural_settle_rule(
    tmp_path: Path,
) -> None:
    raw_path = tmp_path / "raw.json"
    raw = {
        "states": [
            {
                "state": "initial",
                "geometric_verdict": "UNSTABLE",
                "mujoco": {
                    "available": True,
                    "topple_threshold_deg": 25.0,
                    "settle": {
                        "tipped": False,
                        "peak_tilt_deg": 24.9,
                        "final_tilt_deg": 2.0,
                    },
                    "force_probe": {"minimum_observed_force_over_weight": 0.1},
                },
            }
        ]
    }
    assert standing_result(raw, raw_path).status == "PASS"
    raw["states"][0]["mujoco"]["settle"]["tipped"] = True
    raw["states"][0]["mujoco"]["settle"]["peak_tilt_deg"] = 25.1
    assert standing_result(raw, raw_path).status == "FAIL"




def test_progressive_adapter_reports_first_and_worst_failed_heights(
    tmp_path: Path,
) -> None:
    def row(fraction: float, peak: float, tipped: bool) -> dict:
        return {
            "height_fraction": fraction,
            "height": fraction,
            "events": ["uniform_delta_h"],
            "active_geometry_names": ["base", "right_cantilever"],
            "support": {"area": 0.1},
            "mass_models": {
                "uniform_solid": {
                    "available": True,
                    "com": [0.2, 0.0, fraction / 2],
                    "signed_margin": -0.1,
                    "normalized_margin": -0.2,
                }
            },
            "mujoco": {
                "available": True,
                "peak_tilt_deg": peak,
                "final_tilt_deg": peak,
                "tipped": tipped,
            },
            "clip_failures": [],
        }

    raw = {
        "samples": [row(0.40, 10.0, False), row(0.50, 30.0, True), row(0.60, 80.0, True)],
        "summary": {
            "first_dynamic_failure_fraction": 0.50,
            "peak_dynamic_tilt_deg": 80.0,
            "peak_dynamic_tilt_fraction": 0.60,
            "geometric_unstable_sample_count": 2,
            "uniform_delta_h_complete": True,
        },
        "checker_assumptions": {"height_step_fraction": 0.01},
    }
    result = progressive_result(raw, tmp_path / "raw.json")
    assert result.status == "FAIL"
    assert result.metrics["first_dynamic_failure_fraction"] == 0.50
    assert result.violations[0]["active_geometry_names"] == ["base", "right_cantilever"]
    assert result.violations[-1]["peak_tilt_deg"] == 80.0


def test_progressive_adapter_fails_closed_on_incomplete_scan(tmp_path: Path) -> None:
    raw = {
        "samples": [{
            "height_fraction": 0.5,
            "support": {"area": 0.1},
            "mujoco": {"available": False},
            "clip_failures": [],
        }],
        "summary": {},
    }
    assert progressive_result(raw, tmp_path / "raw.json").status == "ERROR"


def test_fea_adapter_reports_hotspot_and_threshold_failures(tmp_path: Path) -> None:
    functional = {
        "max_displacement_m": 0.02,
        "max_von_mises_pa": 60e6,
        "nominal_safety_factor": 0.83,
        "first_positive_buckling_factor": 1.2,
        "stress_hotspot_centroid_m": [0.1, 0.2, 0.3],
        "load_regions": [{"name": "seat_load"}],
        "screening_assessment": {
            "status": "CONCERN",
            "max_displacement_over_characteristic_length": 0.022,
            "thresholds": {
                "max_displacement_ratio": 0.01,
                "minimum_nominal_yield_fos": 2.0,
                "minimum_linear_buckling_factor": 2.0,
            },
            "concerns": [
                "DISPLACEMENT_GT_1PCT_CHARACTERISTIC_LENGTH",
                "NOMINAL_YIELD_FOS_LT_2",
                "LINEAR_BUCKLING_FACTOR_LT_2",
            ],
        },
    }
    raw = {
        "status": "SOLVED",
        "scale": {},
        "material": {},
        "mesh_convergence": {"status": "PASSED"},
        "mesh_levels": [
            {
                "mesh_level": "fine",
                "analyses": {"functional": functional},
            }
        ],
    }
    result = fea_result(raw, tmp_path / "raw.json")
    assert result.status == "FAIL"
    assert result.metrics["stress_hotspot_centroid_m"] == [0.1, 0.2, 0.3]
    assert len(result.violations) == 3


def test_fea_adapter_distinguishes_missing_runtime_from_bad_geometry(
    tmp_path: Path,
) -> None:
    raw = {
        "status": "NOT_MESHABLE",
        "scale": {},
        "material": {},
        "mesh_levels": [
            {
                "mesh_level": "coarse",
                "status": "NOT_MESHABLE",
                "error": "ModuleNotFoundError: No module named 'gmsh'",
            }
        ],
    }
    result = fea_result(raw, tmp_path / "raw.json")
    assert result.status == "ERROR"
    assert result.violations[0]["code"] == "FEA_RUNTIME_UNAVAILABLE"




def test_overhang_adapter_passes_only_after_real_contact_reduction(
    tmp_path: Path,
) -> None:
    config = {
        "optimization": {
            "baseline_nominal_contact_area_mm2": 100.0,
            "baseline_overhang_area_mm2": 200.0,
            "baseline_print_extent_mm": [180.0, 40.0, 130.0],
            "minimum_contact_area_reduction_fraction": 0.01,
            "maximum_overhang_area_increase_fraction": 0.0,
            "maximum_print_extent_delta_mm": 0.01,
        },
        "profile": {"layer_height_mm": 0.2},
    }
    raw = {
        "status": "ANALYZED",
        "scale": {"print_extent_mm": [180.0, 40.0, 130.0]},
        "overhang": {"area_mm2": 200.0},
        "unsupported_region": {"regions": [{"collision": "shelf"}]},
        "slicer_support": {
            "required": True,
            "base_path_length_mm": 1000.0,
            "interface_path_length_mm": 100.0,
        },
        "bridge": {
            "path_length_mm": 20.0,
            "max_single_extrusion_span_mm": 10.0,
        },
        "nominal_contact": {"area_mm2": 100.0},
    }
    failed = overhang_result(raw, tmp_path / "raw.json", config)
    assert failed.status == "FAIL"
    assert failed.violations[0]["supported_regions"][0]["collision"] == "shelf"
    raw["nominal_contact"]["area_mm2"] = 98.0
    passed = overhang_result(raw, tmp_path / "raw.json", config)
    assert passed.status == "PASS"
    assert passed.metrics["contact_area_reduction_fraction"] == 0.02


def test_overhang_adapter_blocks_extent_or_overhang_gaming(tmp_path: Path) -> None:
    config = {
        "optimization": {
            "baseline_nominal_contact_area_mm2": 100.0,
            "baseline_overhang_area_mm2": 200.0,
            "baseline_print_extent_mm": [180.0, 40.0, 130.0],
            "minimum_contact_area_reduction_fraction": 0.01,
            "maximum_overhang_area_increase_fraction": 0.0,
            "maximum_print_extent_delta_mm": 0.01,
        },
        "profile": {},
    }
    raw = {
        "status": "ANALYZED",
        "scale": {"print_extent_mm": [179.0, 40.0, 130.0]},
        "overhang": {"area_mm2": 201.0},
        "unsupported_region": {"regions": []},
        "slicer_support": {"required": True},
        "bridge": {},
        "nominal_contact": {"area_mm2": 90.0},
    }
    result = overhang_result(raw, tmp_path / "raw.json", config)
    assert result.status == "FAIL"
    assert {row["code"] for row in result.violations} == {
        "GEOMETRIC_OVERHANG_AREA_INCREASED",
        "PRINT_EXTENT_CHANGED",
    }


def test_support_adapter_distinguishes_support_from_critical_contact(
    tmp_path: Path,
) -> None:
    raw = {
        "status": "ANALYZED",
        "slicer_support": {"required": True},
        "overhang": {"area_mm2": 10.0},
        "bridge": {"path_length_mm": 4.0},
        "nominal_contact": {"area_mm2": 2.0},
        "critical_surfaces": {
            "verdict": "PASS",
            "contact_overlap_area_mm2": 0.0,
            "reliable": True,
        },
    }
    assert support_result(raw, tmp_path / "raw.json", forbid_support=False).status == "PASS"
    assert support_result(raw, tmp_path / "raw.json", forbid_support=True).status == "FAIL"
    raw["critical_surfaces"]["verdict"] = "VIOLATION"
    assert support_result(raw, tmp_path / "raw.json", forbid_support=False).status == "FAIL"


def test_checker_result_rejects_unknown_status() -> None:
    try:
        CheckerResult(checker="x", status="MAYBE", summary="bad")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown checker status was accepted")
