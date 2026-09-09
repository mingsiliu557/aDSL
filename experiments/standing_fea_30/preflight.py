#!/usr/bin/env python3
"""Exercise the real export -> standing -> FEA chain on five connected fixtures."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.standing_fea_30.run_batch import (
    DEFAULT_ASSET_EXECUTOR,
    DEFAULT_CHECKER_RUN,
    DEFAULT_PYTHON,
    evaluate_final_source,
    runtime_environment,
    write_json,
)


PARTS = {
    "chair_stool": [
        ("base", (.65, .65, .08), (0, 0, .04)),
        ("pedestal", (.16, .16, .48), (0, 0, .28)),
        ("seat", (.62, .58, .10), (0, 0, .53)),
        ("backrest", (.62, .10, .42), (0, .24, .72)),
    ],
    "table_desk": [
        ("base", (.75, .65, .08), (0, 0, .04)),
        ("pedestal", (.18, .18, .62), (0, 0, .35)),
        ("tabletop", (1.10, .70, .10), (0, 0, .69)),
    ],
    "bookshelf": [
        ("base", (.75, .48, .10), (0, 0, .05)),
        ("central_column", (.20, .20, 1.65), (0, 0, .875)),
        ("top_shelf", (.75, .48, .12), (0, 0, 1.68)),
    ],
    "floor_lamp": [
        ("base", (.65, .65, .10), (0, 0, .05)),
        ("pole", (.12, .12, 1.38), (0, 0, .77)),
        ("shade", (.48, .48, .24), (0, 0, 1.48)),
    ],
    "tower_speaker": [
        ("speaker_enclosure", (.42, .40, 1.20), (0, 0, .60)),
    ],
}


def source_text(category: str) -> str:
    lines = [
        "from adsl.core import *", "", "class Fixture(Asset):",
        "    def __init__(self):", f"        super().__init__(label={category!r})",
    ]
    for name, size, center in PARTS[category]:
        lines.append(
            f"        self.attach_part({name!r}, Cube({size!r}, center={center!r}))"
        )
    return "\n".join(lines + ["", "scene = Fixture()", ""])


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-root", type=Path, required=True)
    args = parser.parse_args()
    root = args.output_root.resolve()
    environment = runtime_environment(root, root / "unused_gpu_queue")
    rows = []
    for category in PARTS:
        workspace = root / "workspaces" / "preflight" / category
        workspace.mkdir(parents=True, exist_ok=True)
        (workspace / "source.py").write_text(source_text(category), encoding="utf-8")
        case = {
            "case_id": category, "category": category,
            "fea_config": f"fea_{category}.json",
        }
        result = evaluate_final_source(
            case=case, arm="preflight", workspace=workspace, output_root=root,
            python=DEFAULT_PYTHON, checker_run=DEFAULT_CHECKER_RUN,
            asset_executor=DEFAULT_ASSET_EXECUTOR, environment=environment,
            timeout_seconds=1800,
        )
        statuses = {
            name: item.get("result", {}).get("status")
            for name, item in result.get("checkers", {}).items()
        }
        passed = (
            result["status"] == "COMPLETED"
            and statuses.get("standing") in {"PASS", "FAIL"}
            and statuses.get("fea") in {"PASS", "FAIL"}
        )
        rows.append({
            "category": category, "pipeline_status": result["status"],
            "checker_statuses": statuses, "passed": passed,
        })
        if not passed:
            write_json(root / "preflight_summary.json", {"passed": False, "rows": rows})
            return 1
    write_json(root / "preflight_summary.json", {"passed": True, "rows": rows})
    print(root / "preflight_summary.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
