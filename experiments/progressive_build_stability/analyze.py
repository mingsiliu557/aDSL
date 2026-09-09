#!/usr/bin/env python3
"""Delta-h progressive-build stability analysis for existing aDSL URDFs."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.util
import json
import math
from pathlib import Path
import sys
import tempfile
from typing import Any

import numpy as np
import trimesh

FINAL_PATH = Path(__file__).parents[1] / "final_standing_stability" / "analyze.py"
SPEC = importlib.util.spec_from_file_location("final_standing_shared", FINAL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {FINAL_PATH}")
FINAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FINAL
SPEC.loader.exec_module(FINAL)

DEFAULT_STEP = 0.01
EVENT_EPSILON = 1e-6


def geometry_bounds(items: list[Any]) -> np.ndarray:
    vertices = np.vstack([item.transformed_mesh().vertices for item in items])
    return np.vstack((vertices.min(axis=0), vertices.max(axis=0)))


def height_samples(items: list[Any], step: float) -> tuple[list[float], dict[float, list[str]]]:
    if not 0.0 < step <= 1.0:
        raise ValueError("height step fraction must be in (0, 1]")
    bounds = geometry_bounds(items)
    ground, top = float(bounds[0, 2]), float(bounds[1, 2])
    total = top - ground
    if total <= 0:
        raise ValueError("model has zero build height")
    sample_map: dict[float, set[str]] = {}

    def add(height: float, reason: str) -> None:
        height = min(top, max(ground + total * EVENT_EPSILON, height))
        sample_map.setdefault(round(height, 12), set()).add(reason)

    for index in range(1, int(math.ceil(1.0 / step)) + 1):
        add(ground + min(index * step, 1.0) * total, "uniform_delta_h")
    add(top, "completed_build")
    for item in items:
        bounds = item.transformed_mesh().bounds
        add(float(bounds[0, 2]) + total * EVENT_EPSILON, f"geometry_birth:{item.name}")
        add(float(bounds[1, 2]), f"geometry_complete:{item.name}")
    heights = sorted(sample_map)
    return heights, {h: sorted(sample_map[h]) for h in heights}


def clip_below_height(items: list[Any], height: float) -> dict[str, Any]:
    partials, failures = [], []
    capped = open_count = 0
    for item in items:
        mesh = item.transformed_mesh()
        tolerance = max(float(np.linalg.norm(mesh.extents)), 1.0) * 1e-10
        if float(mesh.bounds[0, 2]) > height + tolerance:
            continue
        if float(mesh.bounds[1, 2]) <= height + tolerance:
            partial = mesh.copy()
        else:
            try:
                partial = mesh.slice_plane([0, 0, height], [0, 0, -1], cap=True)
                capped += 1
            except Exception:
                try:
                    partial = mesh.slice_plane([0, 0, height], [0, 0, -1], cap=False)
                    open_count += 1
                except Exception as error:
                    failures.append(f"{item.name}: {type(error).__name__}: {error}")
                    continue
        if partial is None or len(partial.vertices) < 3 or len(partial.faces) < 1:
            continue
        partial.remove_unreferenced_vertices()
        partials.append(FINAL.CollisionGeometry(
            item.name, "partial_mesh", partial, np.eye(4), {}, item.source_path))
    return {"geometries": partials, "capped_count": capped,
            "open_count": open_count, "failures": failures}


def select_mass_model(models: dict[str, dict[str, Any]]) -> tuple[str | None, dict[str, Any] | None]:
    for name in ("uniform_solid", "uniform_shell"):
        if models[name].get("available"):
            return name, models[name]
    return None, None


def geometry_assessment(support: dict[str, Any], models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    name, model = select_mass_model(models)
    if model is None:
        return {"verdict": "INDETERMINATE", "mass_model": None,
                "reasons": ["NO_COM_ESTIMATE"]}
    reasons = [] if name == "uniform_solid" else ["SOLID_COM_UNAVAILABLE_USING_SHELL_PROXY"]
    if support["degenerate"]:
        reasons.append("DEGENERATE_SUPPORT_REGION")
    if not model.get("inside", False):
        reasons.append("COM_PROJECTION_OUTSIDE_SUPPORT")
    if support["degenerate"] or not model.get("inside", False):
        verdict = "UNSTABLE"
    elif float(model.get("normalized_margin", 0.0)) < 0.01:
        verdict = "MARGINAL"
        reasons.append("NORMALIZED_MARGIN_LT_0P01")
    else:
        verdict = "STABLE_CANDIDATE"
    return {"verdict": verdict, "mass_model": name, "reasons": reasons}


def adhesion_demand(support: dict[str, Any], models: dict[str, dict[str, Any]]) -> dict[str, Any]:
    name, model = select_mass_model(models)
    if model is None:
        return {"available": False, "reason": "NO_COM_ESTIMATE"}
    margin = model.get("signed_margin")
    if margin is not None:
        lever = max(0.0, -float(margin))
    else:
        points, com = np.asarray(support["contact_points"]), np.asarray(model["com"])
        lever = None if not len(points) else float(np.min(np.linalg.norm(points - com[:2], axis=1)))
    scale = max(float(support["horizontal_diagonal"]), 1e-12)
    return {"available": lever is not None, "mass_model": name,
            "gravity_overturning_moment_per_weight": lever,
            "normalized_gravity_overturning_demand": None if lever is None else lever / scale,
            "scope": "gravity only; compare with measured bed/interface resistance"}


def settle_only(xml: str) -> dict[str, Any]:
    import mujoco
    model, data = mujoco.MjModel.from_xml_string(xml), None
    data = mujoco.MjData(model)
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "assembly")
    mujoco.mj_forward(model, data)
    peak = FINAL.run_steps(model, data, mujoco,
                           int(FINAL.SETTLE_DURATION_SECONDS / model.opt.timestep), body_id)
    final = FINAL.body_tilt_deg(data, body_id)
    return {"available": True, "mode": "unbonded_free_rigid_body_on_build_plane",
            "settle_duration_s": FINAL.SETTLE_DURATION_SECONDS,
            "topple_threshold_deg": FINAL.TOPPLE_TILT_THRESHOLD_DEG,
            "peak_tilt_deg": peak, "final_tilt_deg": final,
            "tipped": FINAL.exceeds_topple_threshold(peak),
            "contact_count_final": int(data.ncon), "mujoco_version": mujoco.__version__}


def analyze_partial(items: list[Any], height: float, ground: float, top: float,
                    events: list[str], physics: bool, scratch: Path,
                    density: float, friction: float) -> dict[str, Any]:
    clipped = clip_below_height(items, height)
    row: dict[str, Any] = {"height": height, "height_fraction": (height - ground) / (top - ground),
                           "events": events,
                           "active_geometry_names": [item.name for item in clipped["geometries"]],
                           "partial_geometry_count": len(clipped["geometries"]),
                           "capped_geometry_count": clipped["capped_count"],
                           "open_fallback_count": clipped["open_count"],
                           "clip_failures": clipped["failures"]}
    if not clipped["geometries"]:
        row.update({"verdict": "INDETERMINATE", "reasons": ["EMPTY_PARTIAL_GEOMETRY"]})
        return row
    support = FINAL.support_region(clipped["geometries"])
    solid_com, solid_info = FINAL.weighted_com(clipped["geometries"], "solid")
    shell_com, shell_info = FINAL.weighted_com(clipped["geometries"], "shell")
    models = {"uniform_solid": {**solid_info, **FINAL.margin_metrics(solid_com, support)},
              "uniform_shell": {**shell_info, **FINAL.margin_metrics(shell_com, support)}}
    free = geometry_assessment(support, models)
    row.update({"support": support, "mass_models": models, "free_standing": free,
                "bed_adhesion": adhesion_demand(support, models),
                "verdict": free["verdict"], "reasons": list(free["reasons"])})
    if clipped["failures"]:
        row["verdict"] = "INDETERMINATE"
        row["reasons"].append("PARTIAL_CLIP_FAILURE")
    if physics:
        try:
            xml, proxy = FINAL.build_mujoco_xml(
                clipped["geometries"], scratch, support["ground_z"], density, friction)
            row["mujoco_proxy"], row["mujoco"] = proxy, settle_only(xml)
            if row["mujoco"]["tipped"]:
                row["verdict"] = "UNSTABLE"
                row["reasons"].append("MUJOCO_TILT_GT_25_DEG")
        except Exception as error:
            row["mujoco"] = {"available": False, "error": f"{type(error).__name__}: {error}"}
            row["reasons"].append("MUJOCO_PARTIAL_PROXY_FAILED")
    return row


def summarize(samples: list[dict[str, Any]]) -> dict[str, Any]:
    usable = [row for row in samples if row.get("support")]
    dynamic = [row for row in usable if row.get("mujoco", {}).get("available")]
    tipped = [row for row in dynamic if row["mujoco"]["tipped"]]
    unstable = [row for row in usable if row["free_standing"]["verdict"] == "UNSTABLE"]
    uniform = [row for row in samples if "uniform_delta_h" in row["events"]]
    uniform_dynamic = [row for row in uniform if row.get("mujoco", {}).get("available")]
    peak = max(dynamic, key=lambda row: row["mujoco"]["peak_tilt_deg"], default=None)
    adhesion = [row for row in usable
                if row["bed_adhesion"].get("normalized_gravity_overturning_demand") is not None]
    worst = max(adhesion,
                key=lambda row: row["bed_adhesion"]["normalized_gravity_overturning_demand"],
                default=None)
    return {"sample_count": len(samples), "dynamic_sample_count": len(dynamic),
            "dynamic_failure_count": len(tipped), "any_dynamic_tilt_gt_25_deg": bool(tipped),
            "first_dynamic_failure_fraction": None if not tipped else tipped[0]["height_fraction"],
            "peak_dynamic_tilt_deg": None if peak is None else peak["mujoco"]["peak_tilt_deg"],
            "peak_dynamic_tilt_fraction": None if peak is None else peak["height_fraction"],
            "geometric_unstable_sample_count": len(unstable),
            "first_geometric_unstable_fraction": None if not unstable else unstable[0]["height_fraction"],
            "uniform_delta_h_count": len(uniform),
            "uniform_delta_h_dynamic_count": len(uniform_dynamic),
            "uniform_delta_h_complete": len(uniform) == len(uniform_dynamic),
            "maximum_normalized_bed_adhesion_demand": None if worst is None else worst["bed_adhesion"]["normalized_gravity_overturning_demand"],
            "maximum_bed_adhesion_demand_fraction": None if worst is None else worst["height_fraction"]}


def analyze_case(case_dir: Path, step: float, physics: bool, scratch_root: Path,
                 density: float, friction: float) -> dict[str, Any]:
    parsed = FINAL.parse_urdf(case_dir / "scene.urdf")
    items = FINAL.collision_geometries(parsed, FINAL.state_values(parsed)["initial"])
    bounds = geometry_bounds(items)
    ground, top = float(bounds[0, 2]), float(bounds[1, 2])
    case: dict[str, Any] = {"case_id": case_dir.name,
                            "input_urdf": str(case_dir / "scene.urdf"),
                            "input_urdf_sha256": parsed.sha256, "state": "initial",
                            "movable_joint_count": sum(j.kind in FINAL.MOVABLE_JOINT_TYPES for j in parsed.joints),
                            "ground_z": ground, "top_z": top,
                            "total_height_scene_units": top - ground, "samples": []}
    if case_dir.name == "S01-living-room":
        case.update({"excluded": True, "exclusion_reason": "TRIVIAL_FULL_ROOM_FLOOR_SUPPORT"})
        return case
    heights, events = height_samples(items, step)
    with tempfile.TemporaryDirectory(prefix=f"adsl-progressive-{case_dir.name}-",
                                     dir=scratch_root) as temp:
        for index, height in enumerate(heights):
            case["samples"].append(analyze_partial(
                items, height, ground, top, events[height], physics,
                Path(temp) / f"layer_{index:04d}", density, friction))
    case["summary"] = summarize(case["samples"])
    return case


def report_markdown(payload: dict[str, Any]) -> str:
    active = [case for case in payload["cases"] if not case.get("excluded")]
    dynamic_bad = sum(case["summary"]["any_dynamic_tilt_gt_25_deg"] for case in active)
    geometry_bad = sum(bool(case["summary"]["geometric_unstable_sample_count"]) for case in active)
    lines = ["# 02 Progressive Build Stability：当前案例验证", "",
             f"生成时间：{payload['generated_at_utc']}", "", "## 结论摘要", "",
             f"- 分析 {len(active)} 个 initial-pose 模型；排除 {len(payload['cases']) - len(active)} 个带整块房间地板的场景。",
             f"- 无粘附 MuJoCo 逐高度扫描：{dynamic_bad}/{len(active)} 个模型至少一个 partial 超过 25°。",
             f"- 质心—支撑区扫描：{geometry_bad}/{len(active)} 个模型至少一个 partial 不稳定。",
             "- 同时报告无粘附自立与底板粘附需求；没有实测粘附强度时不虚构 pass/fail。",
             "- 本阶段只判断整体倾倒；弯曲、屈曲与 FEA 明确不进入本次判定。",
             "", "## 方法", "",
             f"- Δh 为总高度的 {payload['assumptions']['height_step_fraction']:.1%}，并加入 geometry 出现/完成事件点。",
             "- 每层与 `z <= h` 半空间求交并优先封口；实体质心不可用时降级到薄壳代理。",
             "- partial 是放在平面上的无粘附刚体，沉降 5 秒内最大倾角严格大于 25°才算倒下。不是从高处自由落体，只保留 0.002 场景单位接触间隙。",
             "- 粘附轨只附加报告重力倾覆力矩/自重，不改变逐 Δh 的倒/不倒主结论。",
             "", "## 逐案例", "",
             "| Case | samples | Δh physics | >25° layers | peak tilt | geometric unstable | max normalized adhesion |",
             "|---|---:|---:|---:|---:|---:|---:|"]
    for case in payload["cases"]:
        if case.get("excluded"):
            lines.append(f"| {case['case_id']} | — | — | — | — | — | excluded |")
            continue
        summary = case["summary"]
        peak, adhesion = summary["peak_dynamic_tilt_deg"], summary["maximum_normalized_bed_adhesion_demand"]
        coverage = f"{summary['uniform_delta_h_dynamic_count']}/{summary['uniform_delta_h_count']}"
        lines.append(f"| {case['case_id']} | {summary['sample_count']} | {coverage} | "
                     f"{summary['dynamic_failure_count']} | {'NA' if peak is None else f'{peak:.3f}°'} | "
                     f"{summary['geometric_unstable_sample_count']} | "
                     f"{'NA' if adhesion is None else f'{adhesion:.6f}'} |")
    lines += ["", "## 证据边界", "",
              "- 场景单位未被可信映射为毫米，归一化 Δh 不能解释为真实 0.2 mm 层高。",
              "- 初始关节姿态被当作整体打印；这不证明 articulated asset 可用 print-in-place 工艺制造。",
              "- MuJoCo 是刚体复核；热收缩、层间开裂、喷嘴拖曳、弹性弯曲、屈曲与 FEA 均在本阶段范围外。",
              "- 状态为 `ANALYZED`，不是打印工程认证；逐层真值见 `results.json` 和 `summary.csv`。", ""]
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = ["case_id", "height_fraction", "height", "verdict", "dynamic_tipped",
              "peak_tilt_deg", "support_area", "normalized_margin",
              "normalized_bed_adhesion_demand", "events", "reasons"]
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for case in payload["cases"]:
            for row in case.get("samples", []):
                _, mass = select_mass_model(row["mass_models"]) if row.get("mass_models") else (None, None)
                writer.writerow({"case_id": case["case_id"], "height_fraction": row["height_fraction"],
                                 "height": row["height"], "verdict": row["verdict"],
                                 "dynamic_tipped": row.get("mujoco", {}).get("tipped"),
                                 "peak_tilt_deg": row.get("mujoco", {}).get("peak_tilt_deg"),
                                 "support_area": row.get("support", {}).get("area"),
                                 "normalized_margin": None if mass is None else mass.get("normalized_margin"),
                                 "normalized_bed_adhesion_demand": row.get("bed_adhesion", {}).get("normalized_gravity_overturning_demand"),
                                 "events": ";".join(row["events"]), "reasons": ";".join(row["reasons"])})
    (output_dir / "progressive_build_stability_analysis.md").write_text(
        report_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path,
                        default=Path("/jiigan-hp/lms/aDSL/experiment/audit_20260830"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--height-step-fraction", type=float, default=DEFAULT_STEP)
    parser.add_argument("--physics", choices=("off", "all"), default="all")
    parser.add_argument("--mujoco-pythonpath", type=Path)
    parser.add_argument("--scratch-dir", type=Path, default=Path("/tmp"))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--density", type=float, default=1000.0)
    parser.add_argument("--friction", type=float, default=2.0)
    args = parser.parse_args()
    input_root, output_dir = args.input_root.resolve(), args.output_dir.resolve()
    if input_root == output_dir or input_root in output_dir.parents:
        raise ValueError("output directory must not be inside frozen input")
    if args.mujoco_pythonpath:
        sys.path.insert(0, str(args.mujoco_pythonpath.resolve()))
    args.scratch_dir.mkdir(parents=True, exist_ok=True)
    case_dirs = sorted(path for path in input_root.iterdir() if (path / "scene.urdf").exists())
    if args.case:
        selected = set(args.case)
        case_dirs = [path for path in case_dirs if path.name in selected]
        missing = selected - {path.name for path in case_dirs}
        if missing:
            raise ValueError(f"unknown cases: {sorted(missing)}")
    payload = {"verification_status": "ANALYZED",
               "generated_at_utc": datetime.now(timezone.utc).isoformat(),
               "input_root": str(input_root), "output_dir": str(output_dir),
               "assumptions": {"height_step_fraction": args.height_step_fraction,
                               "event_height_policy": "geometry min+epsilon and max",
                               "build_orientation": "authored URDF Z-up",
                               "joint_state": "initial only",
                               "bed_tracks": ["unbonded", "finite_adhesion_demand"],
                               "density_kg_m3": args.density,
                               "friction_coefficient": args.friction,
                               "topple_threshold_deg_strictly_greater_than": FINAL.TOPPLE_TILT_THRESHOLD_DEG},
               "environment": {"python": sys.version, "trimesh": trimesh.__version__},
               "cases": []}
    for case_dir in case_dirs:
        print(f"analyzing {case_dir.name}", flush=True)
        payload["cases"].append(analyze_case(
            case_dir, args.height_step_fraction, args.physics == "all", args.scratch_dir,
            args.density, args.friction))
    write_outputs(payload, output_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
