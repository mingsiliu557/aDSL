#!/usr/bin/env python3
"""Summarize deterministic controls and generated high-risk standing cases."""

from __future__ import annotations

import argparse
from collections import Counter
import json
from pathlib import Path
from typing import Any


def load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def initial_state(case: dict[str, Any]) -> dict[str, Any]:
    return next(state for state in case["states"] if state["state"] == "initial")


def fmt_float(value: Any) -> str:
    return "—" if value is None else f"{float(value):.3f}"


def result_row(case: dict[str, Any], metadata: dict[str, Any]) -> dict[str, Any]:
    state = initial_state(case)
    physics = state.get("mujoco", {})
    settle = physics.get("settle", {})
    force = physics.get("force_probe", {})
    impulse = physics.get("impulse_probe", {})
    solid = state["mass_models"]["uniform_solid"]
    shell = state["mass_models"]["uniform_shell"]
    return {
        "case_id": case["case_id"],
        "split": metadata["split"],
        "dataset": metadata["dataset"],
        "group": metadata["group"],
        "object_id": metadata["object_id"],
        "caption_source": metadata["caption_source"],
        "prompt": metadata["prompt"],
        "verdict": state["verdict"],
        "support_area": state["support"]["area"],
        "solid_margin": solid.get("signed_margin"),
        "shell_margin": shell.get("signed_margin"),
        "natural_fall_gt_25": settle.get("tipped"),
        "settle_peak_tilt_deg": settle.get("peak_tilt_deg"),
        "settle_final_tilt_deg": settle.get("final_tilt_deg"),
        "impulse_tipped_direction_count": impulse.get("tipped_direction_count"),
        "minimum_force_over_weight": force.get("minimum_observed_force_over_weight"),
        "reasons": state["reasons"],
    }


def markdown(payload: dict[str, Any]) -> str:
    summary = payload["summary"]
    lines = [
        "# 01 Final Standing Stability：高风险原始 caption 验证",
        "",
        "## 结论",
        "",
        (
            f"- 确定性 controls：稳定组自然倒下 {summary['stable_control_falls']}/"
            f"{summary['stable_control_count']}；易倒组自然倒下 "
            f"{summary['unstable_control_falls']}/{summary['unstable_control_count']}。"
        ),
        (
            f"- 已生成 aDSL cases：{summary['generated_case_count']}；自然沉降最大倾角严格 "
            f">25° 的 case：{summary['generated_natural_falls']}。"
        ),
        f"- 几何/综合 verdict：{summary['generated_verdict_counts']}。",
        f"- Reserve trigger：{summary['reserve_triggered']}。",
        "",
        "自然沉降是主要“会不会自己倒”的结果；固定 0.05 m/s 小冲量和水平力阶梯只作单独鲁棒性探针，不能改写自然沉降结论。control 与 aDSL failure rate 始终分栏。",
        "",
        "## 协议与证据边界",
        "",
        "- Prompt 使用 CAP3D 与 MARVEL-40M+ 的公开原始 caption，不加入 stable、wide base、top-heavy 等稳定性提示。",
        "- 论文未公开其 200 条 prompt 的对象 ID，因此本实验是 same-source/different-sample，不冒充论文 exact prompt reproduction。",
        "- 候选只按类别/短语过滤，再以 SHA256(dataset:object_id) 排序；初始 rank=0，预留 rank=1。",
        "- 生成前把横向 rocket 组修订为明确 standing/tower speaker；修订发生在任何模型生成与结果观察之前。",
        "- 均匀密度 1000 kg/m³、摩擦系数 2.0、刚性平地、无锚固；MuJoCo 是刚体代理，不是实物认证。",
        "",
        "## aDSL 初始姿态结果",
        "",
        "| Case | Source | Group | Verdict | Natural >25° | Settle peak | Impulse dirs >25° | Min F/W |",
        "|---|---|---|---|---|---:|---:|---:|",
    ]
    for row in payload["generated_cases"]:
        lines.append(
            f"| {row['case_id']} | {row['caption_source']} | {row['group']} | "
            f"{row['verdict']} | {row['natural_fall_gt_25']} | "
            f"{fmt_float(row['settle_peak_tilt_deg'])} | "
            f"{row['impulse_tipped_direction_count']} | "
            f"{row['minimum_force_over_weight']} |"
        )
    lines.extend(
        [
            "",
            "## Control 结果",
            "",
            "| Control | Verdict | Natural >25° | Settle peak |",
            "|---|---|---|---:|",
        ]
    )
    for row in payload["controls"]:
        lines.append(
            f"| {row['case_id']} | {row['verdict']} | "
            f"{row['natural_fall_gt_25']} | {fmt_float(row['settle_peak_tilt_deg'])} |"
        )
    lines.extend(
        [
            "",
            "机器可读明细见同目录 `evaluation_summary.json`、"
            "`generated_analysis/results.json` 与 `control_analysis/results.json`。",
            "",
        ]
    )
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument(
        "--generation-status", type=Path, required=True, action="append"
    )
    parser.add_argument("--control-results", type=Path, required=True)
    parser.add_argument("--generated-results", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    manifest = load(args.manifest)
    generations = [load(path) for path in args.generation_status]
    controls_raw = load(args.control_results)
    generated_raw = load(args.generated_results)
    metadata = {case["case_id"]: case for case in manifest["cases"]}
    successful = {
        row["case_id"]
        for generation in generations
        for row in generation["cases"]
        if row["return_code"] == 0
    }
    rows = [
        result_row(case, metadata[case["case_id"]])
        for case in generated_raw["cases"]
        if case["case_id"] in successful
    ]
    controls = []
    for case in controls_raw["cases"]:
        state = initial_state(case)
        controls.append(
            {
                "case_id": case["case_id"],
                "expected": (
                    "stable"
                    if case["case_id"].startswith("CTRL_STABLE_")
                    else "unstable"
                ),
                "verdict": state["verdict"],
                "natural_fall_gt_25": state["mujoco"]["settle"]["tipped"],
                "settle_peak_tilt_deg": state["mujoco"]["settle"]["peak_tilt_deg"],
            }
        )
    stable = [row for row in controls if row["expected"] == "stable"]
    unstable = [row for row in controls if row["expected"] == "unstable"]
    initial_rows = [row for row in rows if row["split"] == "initial"]
    initial_complete = len(initial_rows) == manifest["protocol"]["initial_cases"]
    payload = {
        "protocol": manifest["protocol"],
        "sources": manifest["sources"],
        "summary": {
            "stable_control_count": len(stable),
            "stable_control_falls": sum(row["natural_fall_gt_25"] for row in stable),
            "unstable_control_count": len(unstable),
            "unstable_control_falls": sum(row["natural_fall_gt_25"] for row in unstable),
            "generated_case_count": len(rows),
            "generated_natural_falls": sum(row["natural_fall_gt_25"] for row in rows),
            "generated_verdict_counts": dict(Counter(row["verdict"] for row in rows)),
            "initial_complete": initial_complete,
            "reserve_triggered": (
                initial_complete
                and not any(row["natural_fall_gt_25"] for row in initial_rows)
            ),
        },
        "controls": controls,
        "generated_cases": rows,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "evaluation_summary.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (args.output_dir / "EXPERIMENT.md").write_text(markdown(payload), encoding="utf-8")
    print(json.dumps(payload["summary"], indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
