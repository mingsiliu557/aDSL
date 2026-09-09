#!/usr/bin/env python3
"""Run the frozen CAP3D/MARVEL overhang pilot on CPU."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[2]
DEFAULT_PYTHON = Path("/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/python")
DEFAULT_ADSL_RUN = Path("/vepfs_default/chanxueyan/lhp/lms/envs/adsl/bin/adsl-run")
DEFAULT_CODEX = Path("/vepfs_default/chanxueyan/lhp/lms/npm-global/bin/codex")
DEFAULT_MODEL_CONFIG = REPO / "adsl-agents/configs/llm/codex-cli-gpt-5.6-sol.yaml"
DEFAULT_CHECKER_RUN = REPO / "experiments/workflow_checkers/run.py"
DEFAULT_SLICER = Path(
    "/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/"
    "sysroot/usr/bin/prusa-slicer"
)
DEFAULT_PROFILE = REPO / "experiments/support_requirement_critical_surfaces/fff_profile.ini"
SLICER_LIBS = [
    "/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/"
    "sysroot/usr/lib/x86_64-linux-gnu",
    "/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/"
    "sysroot/lib/x86_64-linux-gnu",
]
PROFILE = {
    "name": "generic_PLA_FFF_overhang_optimization",
    "nozzle_diameter_mm": 0.4,
    "extrusion_width_mm": 0.45,
    "layer_height_mm": 0.2,
    "overhang_threshold_from_horizontal_deg": 45.0,
    "top_contact_z_distance_mm": 0.2,
    "max_print_extent_mm": 180.0,
    "bed_margin_mm": 10.0,
    "critical_overlap_epsilon_mm2": 0.01,
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def case_config(case: dict[str, Any]) -> dict[str, Any]:
    return {
        "profile": PROFILE,
        "case": {
            "semantic_scale": case["semantic_scale"],
            "critical_reliable": False,
            "critical_surfaces": [],
        },
        "slicer": str(DEFAULT_SLICER),
        "profile_path": str(DEFAULT_PROFILE),
        "slicer_timeout_seconds": 900,
    }


def optimization_config(case: dict[str, Any], raw: dict[str, Any]) -> dict[str, Any]:
    config = case_config(case)
    config["optimization"] = {
        "baseline_nominal_contact_area_mm2":
            float(raw["nominal_contact"]["area_mm2"]),
        "baseline_overhang_area_mm2": float(raw["overhang"]["area_mm2"]),
        "baseline_print_extent_mm": [
            float(value) for value in raw["scale"]["print_extent_mm"]
        ],
        "minimum_contact_area_reduction_fraction": 0.01,
        "maximum_overhang_area_increase_fraction": 0.0,
        "maximum_print_extent_delta_mm": 0.01,
    }
    return config


def checker_spec(config_path: Path) -> dict[str, Any]:
    return {
        "name": "overhang",
        "required": True,
        "timeout_seconds": 1200,
        "command": [
            "{python}",
            "{project_root}/experiments/workflow_checkers/run.py",
            "overhang",
            "--asset-dir",
            "{asset_dir}",
            "--source",
            "{source}",
            "--output-dir",
            "{output_dir}",
            "--config",
            str(config_path.resolve()),
        ],
        "prepend_environment": {"LD_LIBRARY_PATH": SLICER_LIBS},
    }


def runtime_environment(codex_binary: Path) -> dict[str, str]:
    environment = dict(os.environ)
    environment.pop("ADSL_GPU_RENDER_QUEUE", None)
    environment.update({
        "ADSL_CODEX_CLI_BIN": str(codex_binary.resolve()),
        "ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS": "1200",
        "ADSL_RENDER_ENGINE": "BLENDER_EEVEE",
        "ADSL_RENDER_WIDTH": "512",
        "ADSL_RENDER_HEIGHT": "512",
        "ADSL_RENDER_SAMPLES": "64",
    })
    current = environment.get("LD_LIBRARY_PATH", "")
    environment["LD_LIBRARY_PATH"] = os.pathsep.join(
        [*SLICER_LIBS, *([current] if current else [])]
    )
    return environment


def run_logged(
    command: Sequence[str],
    *,
    environment: dict[str, str],
    stdout_path: Path,
    stderr_path: Path,
) -> int:
    stdout_path.parent.mkdir(parents=True, exist_ok=True)
    with stdout_path.open("w", encoding="utf-8") as stdout, stderr_path.open(
        "w", encoding="utf-8"
    ) as stderr:
        completed = subprocess.run(
            list(command),
            cwd=REPO,
            env=environment,
            stdout=stdout,
            stderr=stderr,
            text=True,
            check=False,
        )
    return completed.returncode


def analyze_asset(
    *,
    case: dict[str, Any],
    asset_dir: Path,
    output_dir: Path,
    config_path: Path,
    python: Path,
    checker_run: Path,
    environment: dict[str, str],
    logs_root: Path,
) -> tuple[int, dict[str, Any] | None]:
    write_json(config_path, case_config(case))
    command = [
        str(python.resolve()),
        str(checker_run.resolve()),
        "support",
        "--asset-dir",
        str(asset_dir.resolve()),
        "--source",
        str((asset_dir / "source.py").resolve()),
        "--output-dir",
        str(output_dir.resolve()),
        "--config",
        str(config_path.resolve()),
    ]
    rc = run_logged(
        command,
        environment=environment,
        stdout_path=logs_root / f"{case['case_id']}.stdout.log",
        stderr_path=logs_root / f"{case['case_id']}.stderr.log",
    )
    raw_path = output_dir / "raw/result.json"
    raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else None
    return rc, raw


def baseline_case(
    case: dict[str, Any],
    *,
    output_root: Path,
    python: Path,
    adsl_run: Path,
    codex_binary: Path,
    model_config: Path,
    checker_run: Path,
    max_rounds: int,
    environment: dict[str, str],
) -> dict[str, Any]:
    case_id = case["case_id"]
    asset_dir = output_root / "baseline" / case_id
    if asset_dir.exists():
        raise FileExistsError(f"refusing to overwrite {asset_dir}")
    started = utc_now()
    command = [
        str(adsl_run.resolve()),
        "--model-config",
        str(model_config.resolve()),
        "create",
        case["prompt"],
        "--output",
        str(asset_dir.resolve()),
        "--task-id",
        f"overhang-baseline-{case_id}",
        "--max-rounds",
        str(max_rounds),
    ]
    rc = run_logged(
        command,
        environment=environment,
        stdout_path=output_root / "logs/baseline" / f"{case_id}.stdout.log",
        stderr_path=output_root / "logs/baseline" / f"{case_id}.stderr.log",
    )
    required = [asset_dir / name for name in ("source.py", "scene.glb", "scene.urdf")]
    generated = rc == 0 and all(path.is_file() for path in required)
    analysis_rc = None
    raw = None
    if generated:
        write_json(asset_dir / "experiment_case.json", case)
        analysis_rc, raw = analyze_asset(
            case=case,
            asset_dir=asset_dir,
            output_dir=output_root / "baseline_analysis" / case_id,
            config_path=output_root / "configs/screen" / f"{case_id}.json",
            python=python,
            checker_run=checker_run,
            environment=environment,
            logs_root=output_root / "logs/baseline_analysis",
        )
    return {
        "case_id": case_id,
        "started_at": started,
        "finished_at": utc_now(),
        "command": command,
        "return_code": rc,
        "generated": generated,
        "analysis_return_code": analysis_rc,
        "analysis_status": None if raw is None else raw.get("status"),
        "support_required": None if raw is None else raw.get("slicer_support", {}).get("required"),
        "nominal_contact_area_mm2":
            None if raw is None else raw.get("nominal_contact", {}).get("area_mm2"),
        "overhang_area_mm2": None if raw is None else raw.get("overhang", {}).get("area_mm2"),
    }


def repair_case(
    case: dict[str, Any],
    *,
    output_root: Path,
    adsl_run: Path,
    codex_binary: Path,
    model_config: Path,
    max_rounds: int,
    environment: dict[str, str],
) -> dict[str, Any]:
    case_id = case["case_id"]
    baseline_dir = output_root / "baseline" / case_id
    raw_path = output_root / "baseline_analysis" / case_id / "raw/result.json"
    if not raw_path.is_file():
        return {"case_id": case_id, "status": "SKIPPED_NO_BASELINE_ANALYSIS"}
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    if raw.get("status") != "ANALYZED":
        return {"case_id": case_id, "status": "SKIPPED_BASELINE_NOT_ANALYZED"}
    baseline_contact = float(raw.get("nominal_contact", {}).get("area_mm2", 0.0))
    if baseline_contact <= 0:
        return {"case_id": case_id, "status": "SKIPPED_ZERO_BASELINE_CONTACT"}

    repair_dir = output_root / "repair" / case_id
    if repair_dir.exists():
        raise FileExistsError(f"refusing to overwrite {repair_dir}")
    config_path = output_root / "configs/optimization" / f"{case_id}.json"
    spec_path = output_root / "checker_specs" / f"{case_id}.json"
    write_json(config_path, optimization_config(case, raw))
    write_json(spec_path, checker_spec(config_path))
    requirement = (
        "Preserve this original object description and every visible required feature: "
        f"{case['prompt']}\n\n"
        "Manufacturing edit: reduce the fixed-profile PrusaSlicer support-contact burden "
        "using localized self-supporting geometry. Do not delete required parts, change "
        "the authored Z-up print orientation, change global scale or print-space AABB, "
        "or alter checker/slicer settings. Prefer chamfers, angled undersides, arches, "
        "or small gussets that remain within the existing outer envelope."
    )
    command = [
        str(adsl_run.resolve()),
        "--model-config",
        str(model_config.resolve()),
        "edit",
        requirement,
        "--source",
        str((baseline_dir / "source.py").resolve()),
        "--output",
        str(repair_dir.resolve()),
        "--task-id",
        f"overhang-repair-{case_id}",
        "--check-first",
        "--max-rounds",
        str(max_rounds),
        "--checker-config",
        str(spec_path.resolve()),
    ]
    started = utc_now()
    rc = run_logged(
        command,
        environment=environment,
        stdout_path=output_root / "logs/repair" / f"{case_id}.stdout.log",
        stderr_path=output_root / "logs/repair" / f"{case_id}.stderr.log",
    )
    run_path = repair_dir / "run.json"
    run_payload = (
        json.loads(run_path.read_text(encoding="utf-8")) if run_path.is_file() else {}
    )
    return {
        "case_id": case_id,
        "started_at": started,
        "finished_at": utc_now(),
        "command": command,
        "return_code": rc,
        "status": run_payload.get("status", "MISSING_RUN_JSON"),
        "selected_round": run_payload.get("selected_round"),
        "approved": run_payload.get("approved"),
        "error": run_payload.get("error"),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("baseline", "repair", "all"), default="all")
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--adsl-run", type=Path, default=DEFAULT_ADSL_RUN)
    parser.add_argument("--codex-binary", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model-config", type=Path, default=DEFAULT_MODEL_CONFIG)
    parser.add_argument("--checker-run", type=Path, default=DEFAULT_CHECKER_RUN)
    parser.add_argument("--baseline-max-rounds", type=int, default=1)
    parser.add_argument("--repair-max-rounds", type=int, default=3)
    parser.add_argument("--continue-on-failure", action="store_true")
    args = parser.parse_args()

    manifest = json.loads(args.manifest.expanduser().resolve().read_text(encoding="utf-8"))
    selected = set(args.case)
    cases = [
        case for case in manifest["cases"]
        if not selected or case["case_id"] in selected
    ]
    if selected != {case["case_id"] for case in cases} and selected:
        parser.error("one or more --case values are absent from the manifest")
    if args.baseline_max_rounds < 1 or args.repair_max_rounds < 2:
        parser.error("baseline rounds must be >=1 and repair rounds must be >=2")

    for path in (
        args.python, args.adsl_run, args.codex_binary, args.model_config,
        args.checker_run, DEFAULT_SLICER, DEFAULT_PROFILE,
    ):
        if not path.expanduser().resolve().is_file():
            raise FileNotFoundError(path)

    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    environment = runtime_environment(args.codex_binary)
    status_path = output_root / "run_status.json"
    status: dict[str, Any] = {
        "started_at": utc_now(),
        "finished_at": None,
        "phase": args.phase,
        "manifest": str(args.manifest.expanduser().resolve()),
        "cpu_render": {"width": 512, "height": 512, "samples": 64},
        "cases": [],
    }
    write_json(status_path, status)
    final_rc = 0

    phases = ("baseline", "repair") if args.phase == "all" else (args.phase,)
    for phase in phases:
        for case in cases:
            try:
                if phase == "baseline":
                    row = baseline_case(
                        case,
                        output_root=output_root,
                        python=args.python,
                        adsl_run=args.adsl_run,
                        codex_binary=args.codex_binary,
                        model_config=args.model_config,
                        checker_run=args.checker_run,
                        max_rounds=args.baseline_max_rounds,
                        environment=environment,
                    )
                    failed = not row["generated"] or row["analysis_status"] != "ANALYZED"
                else:
                    row = repair_case(
                        case,
                        output_root=output_root,
                        adsl_run=args.adsl_run,
                        codex_binary=args.codex_binary,
                        model_config=args.model_config,
                        max_rounds=args.repair_max_rounds,
                        environment=environment,
                    )
                    failed = row.get("return_code", 0) != 0
            except Exception as error:
                row = {
                    "case_id": case["case_id"],
                    "status": "RUNNER_ERROR",
                    "error": f"{type(error).__name__}: {error}",
                }
                failed = True
            row["phase"] = phase
            status["cases"].append(row)
            write_json(status_path, status)
            if failed:
                final_rc = 1
                if not args.continue_on_failure:
                    status["finished_at"] = utc_now()
                    status["return_code"] = final_rc
                    write_json(status_path, status)
                    return final_rc

    status["finished_at"] = utc_now()
    status["return_code"] = final_rc
    write_json(status_path, status)
    print(json.dumps(status, indent=2, ensure_ascii=False))
    return final_rc


if __name__ == "__main__":
    raise SystemExit(main())
