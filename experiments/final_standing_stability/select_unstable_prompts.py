#!/usr/bin/env python3
"""Freeze source-caption prompts for the final-standing instability experiment."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
from typing import Callable


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
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
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
    matches: Callable[[dict[str, str], str], bool],
) -> list[dict[str, str]]:
    candidates: list[dict[str, str]] = []
    with marvel_path.open(newline="", encoding="utf-8-sig") as handle:
        for row in csv.DictReader(handle):
            object_id = row["id"]
            source = cap3d.get(object_id)
            if source is None:
                continue
            marvel = row.get("marvel_level_2", "").strip()
            combined = f"{source['cap3d']} {marvel}".lower()
            if not matches(row, combined):
                continue
            candidates.append(
                {
                    "dataset": dataset,
                    "group": group,
                    "object_id": object_id,
                    "synset_id": source["synset_id"],
                    "category": row.get("filtered_name", ""),
                    "rank_key": rank_key(dataset, object_id),
                    "cap3d": source["cap3d"],
                    "marvel": marvel,
                }
            )
    candidates.sort(key=lambda item: item["rank_key"])
    if len(candidates) < 2:
        raise RuntimeError(f"{group} has only {len(candidates)} candidates")
    return candidates


def build_manifest(metadata_root: Path) -> dict[str, object]:
    for name, expected in SOURCE_FILES.items():
        actual = sha256(metadata_root / name)
        if actual != expected:
            raise RuntimeError(f"SHA-256 mismatch for {name}: {actual} != {expected}")

    cap3d_root = metadata_root / "cap3d" / CAP3D_REVISION
    marvel_root = metadata_root / "marvel_40m" / MARVEL_REVISION / "annotation"
    shapenet = load_cap3d_shapenet(cap3d_root / "Cap3D_automated_ShapeNet.csv")
    abo = load_cap3d_abo(cap3d_root / "Cap3D_automated_ABO.csv")
    groups = [
        select_group(
            dataset="shapenet",
            group="shapenet_floor_lamp",
            marvel_path=marvel_root / "shapenet.csv",
            cap3d=shapenet,
            matches=lambda row, text: (
                "lamp" in f"{row.get('filtered_name', '')} {row.get('filtered_tags', '')}".lower()
                and any(term in text for term in ("floor lamp", "standing lamp", "street lamp"))
            ),
        ),
        select_group(
            dataset="shapenet",
            group="shapenet_tower_speaker",
            marvel_path=marvel_root / "shapenet.csv",
            cap3d=shapenet,
            matches=lambda row, text: (
                any(
                    term in f"{row.get('filtered_name', '')} {row.get('filtered_tags', '')}".lower()
                    for term in ("speaker", "loudspeaker")
                )
                and any(term in text for term in ("tower speaker", "standing speaker"))
            ),
        ),
        select_group(
            dataset="abo",
            group="abo_floor_lamp",
            marvel_path=marvel_root / "abo.csv",
            cap3d=abo,
            matches=lambda _row, text: any(
                term in text for term in ("floor lamp", "standing lamp")
            ),
        ),
        select_group(
            dataset="abo",
            group="abo_bar_stool",
            marvel_path=marvel_root / "abo.csv",
            cap3d=abo,
            matches=lambda _row, text: "bar stool" in text,
        ),
    ]

    cases: list[dict[str, object]] = []
    case_number = 0
    for split, index in (("initial", 0), ("reserve", 1)):
        for candidates in groups:
            selected = candidates[index]
            for caption_source in ("cap3d", "marvel"):
                case_number += 1
                cases.append(
                    {
                        "case_id": f"U{case_number:02d}",
                        "split": split,
                        "dataset": selected["dataset"],
                        "group": selected["group"],
                        "object_id": selected["object_id"],
                        "synset_id": selected["synset_id"],
                        "category": selected["category"],
                        "selection_rank": index,
                        "rank_key": selected["rank_key"],
                        "caption_source": caption_source,
                        "prompt": selected[caption_source],
                    }
                )

    return {
        "protocol": {
            "scope": "same public CAP3D/MARVEL source datasets, different samples",
            "paper_exact_prompt_ids_public": False,
            "selection": (
                "category/phrase filters followed by SHA256(dataset:object_id); "
                "rank 0 is initial and rank 1 is reserve"
            ),
            "pre_run_amendment": (
                "Before any generation, replaced the horizontal rocket group with "
                "standing/tower loudspeakers so every candidate has a meaningful standing pose."
            ),
            "prompt_engineering_for_stability": False,
            "initial_cases": 8,
            "reserve_cases": 8,
            "reserve_trigger": "run only if all initial generated cases have natural_settle <= 25 degrees",
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
    manifest = build_manifest(args.metadata_root.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(f"Wrote {len(manifest['cases'])} frozen cases to {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
