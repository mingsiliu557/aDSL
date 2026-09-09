#!/usr/bin/env python3
"""Summarize paired runs while preserving FEA indeterminate outcomes."""
from __future__ import annotations

import argparse
import csv
import json
import math
from pathlib import Path
import random
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.standing_fea_30.run_batch import VALID_ARMS, read_json, write_json


def dig(value: dict[str, Any], *keys: str) -> Any:
    current: Any = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def reason(result: dict[str, Any] | None) -> str | None:
    violations = (result or {}).get("violations") or []
    return violations[0].get("code") if violations else None


def first_result(workspace: Path, checker: str) -> dict[str, Any] | None:
    paths = sorted(workspace.glob(
        f"rounds/round_[0-9][0-9]/checkers/{checker}/result.json"
    ))
    return read_json(paths[0]) if paths else None


def fields(name: str, result: dict[str, Any] | None) -> dict[str, Any]:
    row = {f"{name}_status": (result or {}).get("status"),
           f"{name}_reason": reason(result)}
    if name == "standing":
        state = dig(result or {}, "metrics", "states", "initial") or {}
        row.update(standing_peak_tilt_deg=state.get("peak_tilt_deg"),
                   standing_final_tilt_deg=state.get("final_tilt_deg"))
    else:
        metrics = (result or {}).get("metrics") or {}
        row.update(
            fea_displacement_ratio=metrics.get("max_displacement_over_characteristic_length"),
            fea_nominal_safety_factor=metrics.get("nominal_safety_factor"),
            fea_buckling_factor=metrics.get("first_positive_buckling_factor"),
            fea_hotspot_centroid_m=metrics.get("stress_hotspot_centroid_m"),
        )
    return row


def collect_rows(manifest: dict[str, Any], root: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for case in manifest["cases"]:
        for arm in VALID_ARMS:
            state_path = root / "state" / arm / f"{case['case_id']}.json"
            state = read_json(state_path) if state_path.is_file() else {}
            workspace = root / "workspaces" / arm / case["case_id"]
            standing = dig(state, "evaluation", "checkers", "standing", "result")
            fea = dig(state, "evaluation", "checkers", "fea", "result")
            row = {
                "case_id": case["case_id"], "arm": arm,
                "category": case["category"], "dataset": case["dataset"],
                "caption_source": case["caption_source"], "object_id": case["object_id"],
                "prompt": case["prompt"], "run_status": state.get("status", "NOT_RUN"),
                "generation_outcome": state.get("generation_outcome"),
                "approved": state.get("approved"), "selected_round": state.get("selected_round"),
                "elapsed_seconds": dig(state, "generation_process", "elapsed_seconds"),
                "total_tokens": dig(state, "usage", "total_tokens"),
            }
            row.update(fields("standing", standing))
            row.update(fields("fea", fea))
            row["joint_pass"] = (
                (standing or {}).get("status") == "PASS"
                and (fea or {}).get("status") == "PASS"
            ) if state.get("status") == "COMPLETED" else None
            for checker in ("standing", "fea"):
                value = first_result(workspace, checker)
                row[f"initial_{checker}_status"] = (value or {}).get("status")
                row[f"initial_{checker}_reason"] = reason(value)
            rows.append(row)
    return rows


def counts(rows: list[dict[str, Any]], arm: str, field: str) -> dict[str, int]:
    output: dict[str, int] = {}
    for row in rows:
        if row["arm"] == arm:
            key = str(row.get(field) or "MISSING")
            output[key] = output.get(key, 0) + 1
    return dict(sorted(output.items()))


def exact_mcnemar(adsl: list[bool], ours: list[bool]) -> dict[str, Any]:
    a_only = sum(a and not o for a, o in zip(adsl, ours))
    o_only = sum(o and not a for a, o in zip(adsl, ours))
    n = a_only + o_only
    p = 1.0 if n == 0 else min(
        1.0, 2 * sum(math.comb(n, k) for k in range(min(a_only, o_only) + 1)) / 2 ** n
    )
    return {"adsl_only": a_only, "ours_only": o_only, "p_value_two_sided": p}


def bootstrap(adsl: list[bool], ours: list[bool], draws: int = 10000) -> list[float]:
    if not adsl:
        return []
    rng = random.Random(20260909)
    values = []
    for _ in range(draws):
        indexes = [rng.randrange(len(adsl)) for _ in adsl]
        values.append(sum(ours[i] - adsl[i] for i in indexes) / len(indexes))
    values.sort()
    return [values[int(.025 * draws)], values[int(.975 * draws) - 1]]


def summarize(rows: list[dict[str, Any]], expected: int) -> dict[str, Any]:
    pairs: dict[str, dict[str, dict[str, Any]]] = {}
    for row in rows:
        if row["run_status"] == "COMPLETED":
            pairs.setdefault(row["case_id"], {})[row["arm"]] = row
    paired = [pair for pair in pairs.values() if set(pair) == set(VALID_ARMS)]
    adsl = [bool(pair["adsl"]["joint_pass"]) for pair in paired]
    ours = [bool(pair["ours"]["joint_pass"]) for pair in paired]
    return {
        "expected_pairs": expected, "complete_pairs": len(paired),
        "experiment_complete": len(paired) == expected,
        "status_counts": {
            arm: {
                field: counts(rows, arm, f"{field}_status" if field != "run" else "run_status")
                for field in ("run", "standing", "fea")
            } for arm in VALID_ARMS
        },
        "fea_indeterminate_causes": {
            arm: counts(
                [row for row in rows if row.get("fea_status") == "INDETERMINATE"],
                arm, "fea_reason",
            ) for arm in VALID_ARMS
        },
        "paired_joint_pass": {
            "adsl_rate": None if not adsl else sum(adsl) / len(adsl),
            "ours_rate": None if not ours else sum(ours) / len(ours),
            "difference": None if not adsl else (sum(ours) - sum(adsl)) / len(adsl),
            "bootstrap_95pct": bootstrap(adsl, ours),
            "mcnemar_exact": exact_mcnemar(adsl, ours),
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    manifest = read_json(args.manifest.resolve())
    root = args.output_root.resolve()
    rows = collect_rows(manifest, root)
    summary = summarize(rows, len(manifest["cases"]))
    write_json(root / "results.json", {"summary": summary, "rows": rows})
    with (root / "results.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        for row in rows:
            writer.writerow({
                key: json.dumps(value) if isinstance(value, (list, dict)) else value
                for key, value in row.items()
            })
    paired = summary["paired_joint_pass"]
    report = f"""# Standing + FEA 30-object paired experiment

Status: **{'complete' if summary['experiment_complete'] else 'in progress'}**; {summary['complete_pairs']}/{summary['expected_pairs']} complete pairs.

## Outcome

- Vanilla aDSL joint-pass rate: `{paired['adsl_rate']}`
- Ours joint-pass rate: `{paired['ours_rate']}`
- Paired difference: `{paired['difference']}`; fixed-seed bootstrap 95% interval: `{paired['bootstrap_95pct']}`
- Exact McNemar: `{paired['mcnemar_exact']}`

FEA `INDETERMINATE` is retained separately and is never relabeled as structural failure or success.

## Status counts

```json
{json.dumps(summary['status_counts'], indent=2)}
```

## FEA indeterminate causes

```json
{json.dumps(summary['fea_indeterminate_causes'], indent=2)}
```

## Material Passport

- Artifact root: `{root}`
- Prompt scope: {manifest['protocol']['scope']}; selection seed `{manifest['protocol']['selection_seed']}`.
- Sample: 30 unique objects, five categories, two independently generated arms.
- Same code/model/render settings and four-round cap; only Ours enables standing + FEA checking and source repair.
- The model API exposes no formal seed, so generation is not bitwise reproducible.
- Standing fails only when natural-settle tilt is strictly greater than 25 degrees.
- FEA is linear static/eigenvalue-buckling PLA screening defined by the category config.
- This subset is not an exact reproduction of the paper's undisclosed 200 prompt IDs.
- Provenance: manifest, frozen batch config, invocation log, per-arm state, source, logs, and checker artifacts.
"""
    (root / "REPORT.md").write_text(report, encoding="utf-8")
    print(root / "REPORT.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
