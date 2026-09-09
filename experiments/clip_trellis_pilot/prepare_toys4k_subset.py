from __future__ import annotations

import argparse
import csv
import hashlib
from pathlib import Path
import random
from typing import Sequence
import zipfile

from experiments.clip_trellis_pilot.common import sha256, utc_now, write_json


DEFAULT_SEED = 260817975


def select_rows(rows: list[dict[str, str]], count: int, seed: int) -> list[dict[str, str]]:
    if count <= 0:
        raise ValueError("count must be positive")
    by_category: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        identifier = row.get("file_identifier", "")
        category = identifier.split("/", 1)[0]
        if not category or not row.get("sha256"):
            continue
        by_category.setdefault(category, []).append(row)
    if len(by_category) < count:
        raise ValueError(f"only {len(by_category)} valid categories for count={count}")

    rng = random.Random(seed)
    selected_categories = sorted(rng.sample(sorted(by_category), count))
    selected: list[dict[str, str]] = []
    for category in selected_categories:
        candidates = sorted(
            by_category[category],
            key=lambda row: (row["sha256"], row["file_identifier"]),
        )
        selected.append(dict(candidates[rng.randrange(len(candidates))]))
    return selected


def extract_selected(
    archive: Path,
    output_dir: Path,
    rows: list[dict[str, str]],
) -> list[dict[str, str]]:
    records: list[dict[str, str]] = []
    with zipfile.ZipFile(archive) as handle:
        names = set(handle.namelist())
        for row in rows:
            identifier = row["file_identifier"]
            candidates = (identifier, f"toys4k_blend_files/{identifier}")
            member = next((name for name in candidates if name in names), None)
            if member is None:
                records.append({**row, "status": "missing_in_archive"})
                continue
            destination = output_dir / "assets" / identifier
            destination.parent.mkdir(parents=True, exist_ok=True)
            digest = hashlib.sha256()
            with handle.open(member) as source, destination.open("wb") as target:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    target.write(chunk)
            actual = digest.hexdigest()
            status = "extracted" if actual == row["sha256"] else "sha256_mismatch"
            records.append(
                {
                    **row,
                    "status": status,
                    "local_path": str(destination.resolve()),
                    "actual_sha256": actual,
                }
            )
    return records


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Select ten distinct-category Toys4K rows and optionally extract only "
            "those members from an authorized official archive."
        )
    )
    parser.add_argument("--metadata", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--count", type=int, default=10)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--archive", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_dir = args.output_dir.expanduser().resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    with args.metadata.expanduser().resolve().open(
        newline="", encoding="utf-8"
    ) as handle:
        rows = list(csv.DictReader(handle))
    selected = select_rows(rows, args.count, args.seed)

    csv_path = output_dir / "subset.csv"
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(selected[0]))
        writer.writeheader()
        writer.writerows(selected)

    records = selected
    archive_record = None
    if args.archive is not None:
        archive = args.archive.expanduser().resolve()
        records = extract_selected(archive, output_dir, selected)
        archive_record = {
            "path": str(archive),
            "sha256": sha256(archive),
            "note": "Only the ten selected members were extracted.",
        }

    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "source_metadata": str(args.metadata.expanduser().resolve()),
        "source_metadata_sha256": sha256(args.metadata),
        "selection_seed": args.seed,
        "count": args.count,
        "distinct_categories": True,
        "archive": archive_record,
        "records": records,
        "limitation": (
            "The official raw Toys4K archive is access-controlled. Metadata alone "
            "does not provide conditioning images or ground-truth geometry."
        ),
    }
    write_json(output_dir / "subset.json", payload)
    print(output_dir / "subset.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
