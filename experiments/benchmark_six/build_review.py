"""Small report/contact-image builder; never selects or changes a candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from PIL import Image


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def shown(value):
    if value is None:
        return "unknown"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def build(root):
    root = Path(root)
    output = root / "review"
    output.mkdir(parents=True, exist_ok=True)
    configured = read(root / "config/cases.json", {"cases": []})
    cases = configured.get("cases", []) if isinstance(configured, dict) else configured
    rows, pictures = [], []
    for case in cases:
        cid = case.get("case_id", case.get("id"))
        views = {}
        for arm in ("official", "ours"):
            directory = root / "jobs" / cid / arm
            job = read(directory / "job.json", {})
            measured = read(directory / "evaluation/result.json", {})
            review = read(directory / "review/image_critic.json", {})
            metrics = measured.get("metrics", {})
            dapper = measured.get("native_dapper", {}).get("partition_objective", {})
            rows.append(dict(case_id=cid, arm=arm,
                generation=job.get("generation", {}).get("status", "PENDING"),
                native_approved=job.get("approved"),
                common_appearance_approved=review.get("decision", {}).get("approved"),
                topology=measured.get("topology", {}).get("status", "NOT_MEASURED"),
                standing=measured.get("standing", {}).get("status", "NA" if not case.get("standing_applicable", True) else "NOT_MEASURED"),
                min_overhang_area_mm2=metrics.get("min_overhang_area_mm2"),
                G=metrics.get("G"), N=metrics.get("N"),
                voxel_pitch_mm=case.get("voxel_pitch_mm"),
                internal_dapper_score=dapper.get("score"),
                cross_method_dapper_comparable=False,
                source_repairs=job.get("generation", {}).get("actual_source_repairs"),
                generation_requests=job.get("usage", {}).get("requests"),
                generation_tokens=job.get("usage", {}).get("total_tokens"),
                final_review_requests=review.get("usage", {}).get("requests"),
                final_review_tokens=review.get("usage", {}).get("total_tokens"),
                generation_seconds=job.get("generation", {}).get("elapsed_seconds"),
                native_selected_source=job.get("source"),
                evaluation=str(directory / "evaluation/result.json")))
            views[arm] = measured.get("render_paths", {}).get("native_views", [])
        if len(views["official"]) == len(views["ours"]) == 8:
            pair_dir = output / cid
            pair_dir.mkdir(exist_ok=True)
            for index, (a, b) in enumerate(zip(views["official"], views["ours"]), 1):
                with Image.open(a) as official, Image.open(b) as ours:
                    # Same-sized images, no independent object crop or scaling.
                    if official.size != ours.size:
                        raise ValueError(f"different common render dimensions: {cid}")
                    canvas = Image.new("RGBA", (official.width * 2, official.height), "white")
                    canvas.alpha_composite(official.convert("RGBA"), (0, 0))
                    canvas.alpha_composite(ours.convert("RGBA"), (official.width, 0))
                    destination = pair_dir / f"comparison_{index:04d}.png"
                    canvas.convert("RGB").save(destination)
                    pictures.append(dict(case_id=cid, view=index, path=str(destination),
                                         left="official", right="ours"))
    summary = dict(status=read(root / "checkpoint.json", {}).get("status", "PREPARING"),
                   denominators={"appearance": 6, "printing": 6, "standing": 5},
                   rows=rows, pictures=pictures)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Six-case official aDSL / ours comparison", "",
        f"Batch status: {summary['status']}. Each case/method has one initial generation and at most nine source repairs.", "",
        "Comparison images: left = official aDSL, right = ours; common native-material views, without independent crops or text overlays.", "",
        "Minimum geometric overhang area and minimum gap use separately selected print orientations. Neither is slicer support usage.",
        "Dapper scores are internal ours records and cannot rank methods with different reference quantities.",
        "Failures remain in the six-case denominators; standing has five cases because the flying dragon is not applicable.", "",
        "| Case | Method | Generation | Native approved | Common appearance | Topology | Standing | Min area mm2 | G | N | Internal O | Repairs | Gen requests | Gen tokens | Final review requests | Final review tokens | Gen seconds |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    keys = ("case_id", "arm", "generation", "native_approved", "common_appearance_approved", "topology", "standing",
            "min_overhang_area_mm2", "G", "N", "internal_dapper_score", "source_repairs", "generation_requests",
            "generation_tokens", "final_review_requests", "final_review_tokens", "generation_seconds")
    lines += ["| " + " | ".join(shown(row.get(k)) for k in keys) + " |" for row in rows]
    lines += ["", "Images and full per-job evidence:", ""]
    for case in cases:
        cid = case.get("case_id", case.get("id"))
        first = output / cid / "comparison_0001.png"
        if first.is_file():
            lines.append(f"- {cid}: [paired views]({cid}/comparison_0001.png); all eight views are in the same directory.")
        else:
            lines.append(f"- {cid}: paired common views are not yet available.")
    (output / "README.md").write_text("\n".join(lines) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    build(parser.parse_args().run_root)
