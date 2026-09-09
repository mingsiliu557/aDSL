#!/usr/bin/env python3
"""Freeze 30 unique aDSL-source prompts for the standing/FEA comparison."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import sys
from typing import Any

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.standing_fea_30.profiles import (
    CAP3D_REVISION,
    CATEGORIES,
    MARVEL_REVISION,
    SELECTION_SEED,
    SOURCE_FILES,
    CategoryDefinition,
    prompt_is_eligible,
)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rank_key(dataset: str, object_id: str, caption_source: str) -> str:
    value = f"{SELECTION_SEED}:{dataset}:{object_id}:{caption_source}"
    return hashlib.sha256(value.encode()).hexdigest()


def load_cap3d_shapenet(path: Path) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            synset_id, separator, object_id = row[0].partition("_")
            if separator and object_id and row[1].strip():
                values[object_id] = {"synset_id": synset_id, "cap3d": row[1].strip()}
    return values


def load_cap3d_abo(path: Path) -> dict[str, dict[str, str]]:
    values: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if len(row) >= 3 and row[0].strip() and row[2].strip():
                values[row[0].strip()] = {"synset_id": "", "cap3d": row[2].strip()}
    return values


def load_paired_records(
    *,
    dataset: str,
    cap3d: dict[str, dict[str, str]],
    marvel_path: Path,
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with marvel_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            object_id = row.get("id", "").strip()
            source = cap3d.get(object_id)
            marvel = row.get("marvel_level_2", "").strip()
            if source is None or not marvel:
                continue
            records.append({
                "dataset": dataset,
                "object_id": object_id,
                "synset_id": source["synset_id"],
                "filtered_name": row.get("filtered_name", "").strip(),
                "cap3d": source["cap3d"],
                "marvel": marvel,
            })
    return records


def select_category(
    category: CategoryDefinition,
    records: dict[str, list[dict[str, str]]],
    *,
    used: set[tuple[str, str]],
) -> list[dict[str, Any]]:
    selected: list[dict[str, Any]] = []
    for dataset, caption_source in category.slots:
        candidates = [
            record for record in records[dataset]
            if (dataset, record["object_id"]) not in used
            and prompt_is_eligible(
                category,
                dataset=dataset,
                filtered_name=record["filtered_name"],
                synset_id=record["synset_id"],
                prompt=record[caption_source],
            )
        ]
        candidates.sort(
            key=lambda row: rank_key(dataset, row["object_id"], caption_source)
        )
        if not candidates:
            raise RuntimeError(
                f"no unused candidate for {category.name}/{dataset}/{caption_source}"
            )
        chosen = candidates[0]
        used.add((dataset, chosen["object_id"]))
        selected.append({
            "dataset": dataset,
            "object_id": chosen["object_id"],
            "synset_id": chosen["synset_id"],
            "category": category.name,
            "filtered_name": chosen["filtered_name"],
            "caption_source": caption_source,
            "prompt": chosen[caption_source],
            "rank_key": rank_key(dataset, chosen["object_id"], caption_source),
            "eligible_candidate_count": len(candidates),
            "fea_config": category.fea_config,
            "semantic_scale": {
                "measure": category.scale_measure,
                "target_m": category.scale_target_m,
            },
        })
    return selected


def build_manifest(metadata_root: Path) -> dict[str, Any]:
    actual_hashes = {
        relative: sha256(metadata_root / relative)
        for relative in SOURCE_FILES
    }
    mismatches = {
        relative: {"expected": SOURCE_FILES[relative], "actual": actual}
        for relative, actual in actual_hashes.items()
        if actual != SOURCE_FILES[relative]
    }
    if mismatches:
        raise RuntimeError(f"source SHA-256 mismatch: {mismatches}")

    cap3d_root = metadata_root / "cap3d" / CAP3D_REVISION
    marvel_root = metadata_root / "marvel_40m" / MARVEL_REVISION / "annotation"
    records = {
        "shapenet": load_paired_records(
            dataset="shapenet",
            cap3d=load_cap3d_shapenet(cap3d_root / "Cap3D_automated_ShapeNet.csv"),
            marvel_path=marvel_root / "shapenet.csv",
        ),
        "abo": load_paired_records(
            dataset="abo",
            cap3d=load_cap3d_abo(cap3d_root / "Cap3D_automated_ABO.csv"),
            marvel_path=marvel_root / "abo.csv",
        ),
    }
    used: set[tuple[str, str]] = set()
    cases: list[dict[str, Any]] = []
    for category in CATEGORIES:
        cases.extend(select_category(category, records, used=used))
    for index, case in enumerate(cases, start=1):
        case["case_id"] = f"SF{index:02d}"
        case["arm_order"] = ["adsl", "ours"] if index % 2 else ["ours", "adsl"]

    return {
        "protocol": {
            "name": "adsl_vs_checker_unified_standing_fea_30",
            "sample_size_objects": 30,
            "arms": ["adsl", "ours"],
            "generation_runs": 60,
            "selection_seed": SELECTION_SEED,
            "selection": (
                "five instability-prone load-bearing categories; fixed dataset/caption "
                "slots; SHA256(seed:dataset:object_id:caption_source) rank; unique objects"
            ),
            "paper_exact_prompt_ids_public": False,
            "scope": "targeted same-source/different-sample CAP3D/MARVEL subset",
            "prompt_engineering_for_physics": False,
            "llm_seed_available": False,
            "max_rounds_per_arm": 4,
            "standing_threshold_deg": 25.0,
            "fea_indeterminate_policy": "retain and report separately; do not resample",
        },
        "sources": {
            "metadata_root": str(metadata_root.resolve()),
            "cap3d_revision": CAP3D_REVISION,
            "marvel_revision": MARVEL_REVISION,
            "sha256": actual_hashes,
        },
        "cases": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--metadata-root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    manifest = build_manifest(args.metadata_root.expanduser().resolve())
    output = args.output.expanduser().resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_name(f".{output.name}.tmp")
    temporary.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(output)
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
