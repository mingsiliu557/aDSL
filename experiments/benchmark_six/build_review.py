"""Small report/contact-image builder; never selects or changes a candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PIL import Image
from importlib.util import find_spec
sys.path.insert(0, str(Path(find_spec('adsl.agents').origin).resolve().parents[1]))
from benchmark.metrics.physical import STANDING_PROTOCOL, summarize_physical_methods


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
    environments = read(root / 'config/envs.json', {})
    arms = tuple(environments.get('arms', {})) or ('official', 'ours')
    physical_records = {arm:[] for arm in arms}
    rows, pictures = [], []
    for case in cases:
        cid = case.get("case_id", case.get("id"))
        applicable = bool(case.get('standing_applicable', True)) and cid not in (
            'dragon_007','Toys4K_dragon_007')
        views = {}
        for arm in arms:
            directory = root / "jobs" / cid / arm
            job = read(directory / "job.json", {})
            measured = read(directory / "evaluation/result.json", {})
            review = read(directory / "review/image_critic.json", {})
            metrics = measured.get("metrics", {})
            record = measured.get('physical_evaluation_record')
            if record is None:
                if measured:
                    raise ValueError('legacy evaluation has no independent standing protocol record; do not mix reports')
                generation = str(job.get('generation', {}).get('status', '')).upper()
                terminal_failure = generation in {'FAIL','FAILED','ERROR','GENERATION_FAILED','TIMEOUT','API_INTERRUPTED'}
                record = dict(case_id=cid, applicable=applicable,
                    protocol_id=STANDING_PROTOCOL, state='TERMINAL' if terminal_failure else 'PENDING',
                    generation_status=generation, checker={},
                    source_sha256=job.get('selected_source_sha256'), asset_sha256=job.get('selected_glb_sha256'))
            if record.get('case_id') != cid or record.get('applicable') is not applicable:
                raise ValueError('evaluation identity/applicability differs from frozen case')
            physical_records[arm].append(record)
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
        if len(views.get("official", [])) == len(views.get("ours", [])) == 8:
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
    physical = summarize_physical_methods(physical_records, protocol_id=STANDING_PROTOCOL)
    (output/'physical_metrics.json').write_text(json.dumps(physical, ensure_ascii=False, indent=2)+'\n')
    summary = dict(status=read(root / "checkpoint.json", {}).get("status", "PREPARING"),
                   physical=physical,
                   denominators={"appearance": len(cases), "printing": len(cases),
                       "standing":sum(bool(c.get('standing_applicable',True))
                           and c.get('case_id',c.get('id')) not in ('dragon_007','Toys4K_dragon_007') for c in cases)},
                   rows=rows, pictures=pictures)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Physical method comparison", "",
        f"Batch status: {summary['status']}; physical summary: {physical['status']}.", "",
        "| Method | Standing success rate ↑ | Structural connectivity ↑ | NSV ↓ | N |",
        "|---|---:|---:|---:|---:|"]
    lines += ["| " + " | ".join(shown(row[k]) for k in
        ('method','standing_success_rate','structural_connectivity_rate','nsv','N')) + " |" for row in physical['table']]
    lines += ["",
        "Standing uses the frozen applicable denominator, including terminal generation/simulation failures. Pending records mean the experiment is incomplete.",
        "Structural connectivity requires internal continuity of every predeclared print part AND a connected interpart distance graph. Missing offline measurements are terminal non-successes, not observed disconnections.",
        "NSV24 and N use the same all-method, same-protocol common measurable case list in physical_metrics.json; those subsets are not all-task success rates.",
        "Raw IVR remains a separate historical diagnostic. Legacy support without support_volume_min24_v1 is not measured NSV24. Missing measurements remain null. NSV uses the minimum ray S among the same 24 rotations for each final part, not a continuous global minimum or slicer consumption.",
        "Contact warnings do not change standing observations; topology failures still matter for final product acceptance.",
        "", "## Per-case evidence appendix", "",
        "Comparison images: left = official aDSL, right = ours; common native-material views, without independent crops or text overlays.", "",
        "Minimum geometric overhang area and minimum gap use separately selected print orientations. Neither is slicer support usage.",
        "Dapper scores are internal ours records and cannot rank methods with different reference quantities.",
        "Failures remain in the frozen denominators; applicability and missing reasons are retained in physical_metrics.json.", "",
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
