#!/usr/bin/env python3
"""Freeze paper-source prompts for the CPU overhang-feedback pilot."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Any, Callable


CAP3D_REVISION = "0f05a726ff7eb3db93444a4efc65f0552bce84a4"
MARVEL_REVISION = "b44089646984abc6976baefdd5289b2198c46e2c"
SOURCE_FILES = {
    f"cap3d/{CAP3D_REVISION}/Cap3D_automated_ShapeNet.csv":
        "d1e881d7c22d3313ab989d1fdf5e43559526fe4a9e1d7e7b1c2d48ba702a9e54",
    f"cap3d/{CAP3D_REVISION}/Cap3D_automated_ABO.csv":
        "442d23e1dc39b52e002c9fbc556a48ffdf1dfd255552982778dde1cdc497cbaf",
    f"marvel_40m/{MARVEL_REVISION}/annotation/shapenet.csv":
        "af1faec8ad4601a1c7d424a6cf26edce4a6acec8e2e41676ab109837d2290c85",
    f"marvel_40m/{MARVEL_REVISION}/annotation/abo.csv":
        "00776554b146d6e78d4c51d955c66ae4db152cbc41c0ae7e7bae19c4ea60da3d",
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def rank_key(dataset: str, object_id: str) -> str:
    return hashlib.sha256(f"{dataset}:{object_id}".encode()).hexdigest()


def load_cap3d_shapenet(path: Path) -> dict[str, dict[str, str]]:
    captions: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if len(row) < 2:
                continue
            synset_id, separator, object_id = row[0].partition("_")
            if separator and object_id:
                captions[object_id] = {
                    "synset_id": synset_id,
                    "cap3d": row[1].strip(),
                }
    return captions


def load_cap3d_abo(path: Path) -> dict[str, dict[str, str]]:
    captions: dict[str, dict[str, str]] = {}
    with path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.reader(handle):
            if len(row) >= 3:
                captions[row[0]] = {"synset_id": "", "cap3d": row[2].strip()}
    return captions


def select_group(
    *,
    dataset: str,
    group: str,
    marvel_path: Path,
    cap3d: dict[str, dict[str, str]],
    matches: Callable[[dict[str, str], str, str], bool],
) -> dict[str, str]:
    candidates: list[dict[str, str]] = []
    with marvel_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            object_id = row["id"]
            source = cap3d.get(object_id)
            if source is None:
                continue
            cap3d_text = source["cap3d"].strip()
            marvel_text = row.get("marvel_level_2", "").strip()
            if not cap3d_text or not marvel_text:
                continue
            if not matches(row, cap3d_text.lower(), marvel_text.lower()):
                continue
            candidates.append({
                "dataset": dataset,
                "group": group,
                "object_id": object_id,
                "synset_id": source["synset_id"],
                "category": row.get("filtered_name", ""),
                "rank_key": rank_key(dataset, object_id),
                "cap3d": cap3d_text,
                "marvel": marvel_text,
            })
    candidates.sort(key=lambda item: item["rank_key"])
    if not candidates:
        raise RuntimeError(f"{group} has no candidates")
    selected = dict(candidates[0])
    selected["candidate_count"] = str(len(candidates))
    return selected


def build_manifest(metadata_root: Path) -> dict[str, Any]:
    for relative, expected in SOURCE_FILES.items():
        actual = sha256(metadata_root / relative)
        if actual != expected:
            raise RuntimeError(f"SHA-256 mismatch for {relative}: {actual} != {expected}")

    cap3d_root = metadata_root / "cap3d" / CAP3D_REVISION
    marvel_root = metadata_root / "marvel_40m" / MARVEL_REVISION / "annotation"
    shapenet = load_cap3d_shapenet(cap3d_root / "Cap3D_automated_ShapeNet.csv")
    abo = load_cap3d_abo(cap3d_root / "Cap3D_automated_ABO.csv")

    selections = [
        (
            select_group(
                dataset="shapenet",
                group="shapenet_mug_handle",
                marvel_path=marvel_root / "shapenet.csv",
                cap3d=shapenet,
                matches=lambda row, cap, marvel: (
                    (row.get("filtered_name", "").lower() == "mug"
                     or row.get("id", "") in shapenet
                     and shapenet[row["id"]]["synset_id"] == "03797390")
                    and "handle" in cap and "handle" in marvel
                ),
            ),
            {"measure": "height", "target_m": 0.10},
        ),
        (
            select_group(
                dataset="shapenet",
                group="shapenet_chair_armrests",
                marvel_path=marvel_root / "shapenet.csv",
                cap3d=shapenet,
                matches=lambda row, cap, marvel: (
                    (row.get("filtered_name", "").lower() == "chair"
                     or row.get("id", "") in shapenet
                     and shapenet[row["id"]]["synset_id"] == "03001627")
                    and "armrest" in cap and "armrest" in marvel
                ),
            ),
            {"measure": "height", "target_m": 0.90},
        ),
        (
            select_group(
                dataset="shapenet",
                group="shapenet_tabletop_drawer",
                marvel_path=marvel_root / "shapenet.csv",
                cap3d=shapenet,
                matches=lambda row, cap, marvel: (
                    (row.get("filtered_name", "").lower() in {"table", "desk"}
                     or row.get("id", "") in shapenet
                     and shapenet[row["id"]]["synset_id"] == "04379243")
                    and any(term in cap for term in ("tabletop", "table top", "desktop"))
                    and any(term in marvel for term in ("tabletop", "table top", "desktop"))
                    and "drawer" in cap and "drawer" in marvel
                ),
            ),
            {"measure": "height", "target_m": 0.75},
        ),
        (
            select_group(
                dataset="abo",
                group="abo_curved_arm_floor_lamp",
                marvel_path=marvel_root / "abo.csv",
                cap3d=abo,
                matches=lambda _row, cap, marvel: (
                    "floor lamp" in marvel
                    and "curved" in cap and "arm" in cap
                    and "curved" in marvel and "arm" in marvel
                ),
            ),
            {"measure": "height", "target_m": 1.60},
        ),
    ]

    cases: list[dict[str, Any]] = []
    case_number = 0
    for selection, semantic_scale in selections:
        for caption_source in ("cap3d", "marvel"):
            case_number += 1
            cases.append({
                "case_id": f"O{case_number:02d}",
                "dataset": selection["dataset"],
                "group": selection["group"],
                "object_id": selection["object_id"],
                "synset_id": selection["synset_id"],
                "category": selection["category"],
                "candidate_count": int(selection["candidate_count"]),
                "selection_rank": 0,
                "rank_key": selection["rank_key"],
                "caption_source": caption_source,
                "prompt": selection[caption_source],
                "semantic_scale": semantic_scale,
            })

    return {
        "protocol": {
            "scope": "same public CAP3D/MARVEL sources, overhang-prone conditional CPU pilot",
            "paper_exact_prompt_ids_public": False,
            "prompt_engineering_for_baseline": False,
            "selection": (
                "four feature-bearing category filters fixed before generation; "
                "SHA256(dataset:object_id) rank 0; both source captions must name the feature"
            ),
            "available_source_ratio": "3 ShapeNet : 1 ABO, preserving the paper's 60:20 ratio after excluding Objaverse",
            "objaverse_status": (
                "excluded from this pilot: local captions are absent and the immutable "
                "MARVEL objaverse.csv is 3,789,414,322 bytes; the Hugging Face row server "
                "fails on the repository's mixed CSV schema"
            ),
            "paired_design": (
                "baseline uses the untouched source caption; repair starts from the exact "
                "baseline source.py and adds only checker-mediated manufacturing feedback"
            ),
            "primary_metric": "PrusaSlicer nominal support-contact area at fixed profile, orientation, and print-space AABB",
            "eligibility": "baseline analysis must complete and nominal support-contact area must be > 0",
            "success": (
                ">=1% nominal support-contact reduction, no geometric overhang increase, "
                "and print-space AABB delta <=0.01 mm"
            ),
        },
        "sources": {
            "cap3d_revision": CAP3D_REVISION,
            "marvel_revision": MARVEL_REVISION,
            "sha256": SOURCE_FILES,
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
    output.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
