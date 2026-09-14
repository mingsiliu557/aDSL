#!/usr/bin/env python3
"""Run the frozen CAP3D/MARVEL overhang pilot on CPU."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import shutil
import hashlib
import time
import sys
from typing import Any, Sequence


REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))
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
        "maximum_print_extent_delta_mm": 0.01,
    }
    return config


def checker_spec(config_path: Path) -> dict[str, Any]:
    return {
        "name": "overhang",
        "required": False,
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


def paired_phase(args, cases: list[dict[str, Any]]) -> int:
    """Existing assets only. Measurements never live inside control workspaces."""
    from experiments.support_requirement_critical_surfaces import analyze as analyzer
    from adsl.agents.overhang_edit import protection_check
    root = args.output_root.resolve()
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for case in cases:
        cid = case["case_id"]
        print(f"[{utc_now()}] {args.phase} {cid}: started", flush=True)
        case_root = root / cid
        case_root.mkdir(exist_ok=True)
        original = case_root / "original"
        row = {"case_id": cid, "phase": args.phase}
        started = time.monotonic()
        try:
            if args.phase == "prepare":
                if "prompt" not in case:
                    prompts = json.loads((REPO / case["prompt_manifest"]).read_text())
                    case = {**case, "prompt": next(c["prompt"] for c in prompts["cases"] if c["case_id"] == cid)}
                source = (REPO / case["asset_dir"]).resolve()
                for name in ("source.py", "scene.glb", "scene.urdf"):
                    if not (source / name).is_file():
                        raise ValueError(f"missing existing asset: {name}")
                if original.exists():
                    raise ValueError("prepared original already exists; use a new output directory")
                original.mkdir()
                for name in ("source.py", "scene.glb", "scene.urdf", "source_index.json", "meshes", "render"):
                    item = source / name
                    if item.is_dir():
                        shutil.copytree(item, original / name)
                    elif item.is_file():
                        shutil.copy2(item, original / name)
                if not list((original / "render").glob("*.png")):
                    raise ValueError("no reference renders; not ready")
                options = {"protection": case["protection"], "original_urdf": str(original / "scene.urdf"),
                           "max_candidates": args.max_candidates}
                protection = protection_check(original / "scene.urdf", original / "scene.urdf", options)
                if protection["status"] != "PASS":
                    raise ValueError(f"protection not measurable: {protection}")
                hashes = {str(p.relative_to(original)): hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in original.rglob("*") if p.is_file()}
                write_json(case_root / "prepared.json", {"case": case, "hashes": hashes,
                    "options": options, "readiness": "assets_ready_measurement_pending"})
                row["status"] = "ASSETS_READY_MEASUREMENT_PENDING"
            else:
                if not (case_root / "prepared.json").is_file():
                    preparation = case_root / "prepare_status.json"
                    reason = (json.loads(preparation.read_text()).get("reason", "preparation not ready")
                              if preparation.is_file() else "preparation not completed; run prepare first")
                    row.update(status="NOT_READY", reason=reason)
                    continue
                prepared = json.loads((case_root / "prepared.json").read_text())
                case = prepared["case"]
                for name, expected in prepared["hashes"].items():
                    if hashlib.sha256((original / name).read_bytes()).hexdigest() != expected:
                        raise ValueError("original asset changed since preparation")
                config_path = case_root / "measurement_config.json"
                if args.phase == "evaluate" and not config_path.is_file():
                    row.update(status="NOT_READY", reason="baseline calibration did not complete; evaluation does not retry it")
                    continue
                if not config_path.exists():
                    config = case_config(case)
                    config["case"]["exterior_method"] = "blender_exact_union"
                    baseline = analyzer.analyze_case(cid, original, case_root / "baseline_measurement",
                        {"profile": PROFILE, "cases": {cid: config["case"]}},
                        DEFAULT_SLICER, DEFAULT_PROFILE, 900)
                    write_json(case_root / "baseline.json", baseline)
                    if baseline["status"] != "ANALYZED" or not baseline.get("measurement"):
                        raise ValueError(f"baseline measurement unavailable: {baseline['status']}")
                    config["case"]["frozen_measurement"] = baseline["measurement"]
                    repeat = analyzer.analyze_case(cid, original, case_root / "baseline_repeat",
                        {"profile": PROFILE, "cases": {cid: config["case"]}},
                        DEFAULT_SLICER, DEFAULT_PROFILE, 900)
                    write_json(case_root / "baseline_repeat.json", repeat)
                    if repeat["status"] != "ANALYZED":
                        raise ValueError(f"baseline repeat unavailable: {repeat['status']}")
                    config["case"]["repeatability_mm2"] = abs(baseline["overhang"]["area_mm2"] - repeat["overhang"]["area_mm2"])
                    config["optimization"] = {"maximum_print_extent_delta_mm": 0.01}
                    write_json(config_path, config)
                config = json.loads(config_path.read_text())
                if args.phase == "edit":
                    if not args.model_config:
                        raise ValueError("edit requires explicit --model-config (use existing StepCode profile)")
                    requirement = (case["prompt"] + "\nPreserve key shape, function and appearance first; "
                        "then reduce geometric overhang through local source edits. Do not delete necessary parts, "
                        "scale the object or alter print/checker settings. You may stop without improvement.\n" +
                        json.dumps(case["protection"], ensure_ascii=False))
                    arms = []
                    for arm in ("control", "feedback"):
                        print(f"[{utc_now()}] {cid}/{arm}: edit starting", flush=True)
                        options = {**prepared["options"], "arm": arm,
                                   "scale_mm_per_source_unit": config["case"]["frozen_measurement"]["scale_mm_per_source_unit"]}
                        options_path = case_root / f"{arm}_options.json"
                        write_json(options_path, options)
                        policy_path = case_root / "repair_policy.json"
                        write_json(policy_path, {"max_total_candidates": options["max_candidates"], "max_candidates_per_round": 1})
                        command = [str(args.adsl_run), "--model-config", str(args.model_config.resolve()), "edit", requirement,
                            "--source", str(original / "source.py"), "--output", str(case_root / arm),
                            "--max-rounds", str(options["max_candidates"] + 1),
                            "--overhang-experiment-config", str(options_path), "--repair-policy-config", str(policy_path)]
                        for image in sorted((original / "render").glob("*.png"))[:4]:
                            command.extend(["--image", str(image)])
                        if arm == "feedback":
                            spec_path = case_root / "overhang_spec.json"
                            write_json(spec_path, checker_spec(config_path))
                            command.extend(["--check-first", "--checker-config", str(spec_path)])
                        arm_started = time.monotonic()
                        rc = run_logged(command, environment=runtime_environment(args.codex_binary),
                            stdout_path=case_root / f"{arm}.stdout.log", stderr_path=case_root / f"{arm}.stderr.log")
                        arm_timing = {"arm": arm, "returncode": rc, "elapsed_seconds": time.monotonic() - arm_started}
                        write_json(case_root / f"{arm}_timing.json", arm_timing)
                        arms.append(arm_timing)
                    row.update(status="EDIT_FINISHED", arms=arms)
                else:
                    baseline = json.loads((case_root / "baseline.json").read_text())
                    arms = []
                    for arm in ("control", "feedback"):
                        workspace = case_root / arm
                        run_file = workspace / "run.json"
                        run = json.loads(run_file.read_text()) if run_file.exists() else {}
                        # Only evaluate selected outputs after the editor has finished.
                        if run.get("status") != "completed":
                            arms.append({"arm": arm, "status": "NOT_COMPLETED", "reason": run.get("error")})
                            continue
                        final = analyzer.analyze_case(cid, workspace, case_root / f"final_{arm}",
                            {"profile": config["profile"], "cases": {cid: config["case"]}},
                            DEFAULT_SLICER, DEFAULT_PROFILE, 900)
                        write_json(case_root / f"{arm}_measurement.json", final)
                        area = final.get("overhang", {}).get("area_mm2")
                        initial_area = baseline["overhang"]["area_mm2"]
                        protection = protection_check(original / "scene.urdf", workspace / "scene.urdf", {
                            **prepared["options"], "scale_mm_per_source_unit": config["case"]["frozen_measurement"]["scale_mm_per_source_unit"]})
                        attempts = workspace / "edit_attempts.jsonl"
                        arms.append({"arm": arm, "status": final["status"],
                            "original_overhang_mm2": initial_area, "final_overhang_mm2": area,
                            "reduction_mm2": initial_area - area if area is not None else None,
                            "original_support_contact_mm2": baseline["nominal_contact"]["area_mm2"],
                            "final_support_contact_mm2": final.get("nominal_contact", {}).get("area_mm2"),
                            "support_required": final.get("slicer_support", {}).get("required"),
                            "protection": protection, "appearance_approved": run.get("appearance_approved"),
                            "candidate_count": len(attempts.read_text().splitlines()) if attempts.exists() else 0,
                            "usage": run.get("usage"), "selected_round": run.get("selected_round"),
                            "timing_report": str(case_root / f"{arm}_timing.json"),
                            "stop_reason": run.get("finalization_reason"),
                            "selection_policy": "original workflow; never reselect using measurement" if arm == "control" else "protected area improvement"})
                    row.update(status="EVALUATED", arms=arms)
        except Exception as exc:
            row.update(status="UNAVAILABLE", reason=f"{type(exc).__name__}: {exc}"[:400])
        finally:
            row["elapsed_seconds"] = time.monotonic() - started
            if args.phase == "prepare":
                write_json(case_root / "prepare_status.json", row)
            rows.append(row)
            print(f"[{utc_now()}] {args.phase} {cid}: {row['status']}", flush=True)
            write_json(root / f"{args.phase}_results.json", {"cases": rows})
    print(json.dumps(rows, indent=2, ensure_ascii=False))
    return int(any(row["status"] in {"UNAVAILABLE", "NOT_READY"} for row in rows))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--phase", choices=("prepare", "edit", "evaluate", "baseline", "repair", "all"), default="prepare")
    parser.add_argument("--max-candidates", type=int, default=2)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--python", type=Path, default=DEFAULT_PYTHON)
    parser.add_argument("--adsl-run", type=Path, default=DEFAULT_ADSL_RUN)
    parser.add_argument("--codex-binary", type=Path, default=DEFAULT_CODEX)
    parser.add_argument("--model-config", type=Path)
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
    if args.max_candidates < 1:
        parser.error("--max-candidates must be positive")
    if args.phase in {"prepare", "edit", "evaluate"}:
        return paired_phase(args, cases)
    if args.phase in {"repair", "all"}:
        parser.error("legacy percentage-gated repair is retired; use prepare/edit/evaluate with paired_assets.json")
    args.model_config = args.model_config or DEFAULT_MODEL_CONFIG
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
