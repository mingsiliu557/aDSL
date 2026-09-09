from __future__ import annotations

import argparse
import csv
from pathlib import Path
from statistics import mean, median
from typing import Any, Sequence

from experiments.clip_trellis_pilot.common import (
    completed_render,
    load_json,
    sha256,
    utc_now,
    write_json,
)


def summarize(rows: list[dict[str, Any]], margin: float) -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for track in ("text", "image"):
        track_rows = [row for row in rows if row["track"] == track]
        case_ids = sorted({row["case_id"] for row in track_rows})
        by_method = {
            method: {
                row["case_id"]: row
                for row in track_rows
                if row["method"] == method
            }
            for method in ("adsl", "trellis")
        }
        paired: list[dict[str, float | str]] = []
        for case_id in case_ids:
            if case_id not in by_method["adsl"] or case_id not in by_method["trellis"]:
                continue
            a = float(by_method["adsl"][case_id]["mean_clip"])
            t = float(by_method["trellis"][case_id]["mean_clip"])
            paired.append(
                {"case_id": case_id, "adsl": a, "trellis": t, "delta": a - t}
            )
        if not paired:
            continue
        deltas = [float(item["delta"]) for item in paired]
        delta_mean = mean(deltas)
        summaries.append(
            {
                "track": track,
                "n": len(paired),
                "adsl_mean_failure_zero": mean(
                    float(item["adsl"]) for item in paired
                ),
                "trellis_mean_failure_zero": mean(
                    float(item["trellis"]) for item in paired
                ),
                "mean_delta_adsl_minus_trellis": delta_mean,
                "median_delta_adsl_minus_trellis": median(deltas),
                "engineering_margin": margin,
                "rough_effect": (
                    "close_within_margin"
                    if abs(delta_mean) <= margin
                    else ("adsl_higher" if delta_mean > 0 else "trellis_higher")
                ),
                "paired_cases": paired,
            }
        )
    return summaries


def _white_rgb(image: Any) -> Any:
    from PIL import Image

    rgba = image.convert("RGBA")
    background = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
    background.alpha_composite(rgba)
    return background.convert("RGB")


def _load_image(path: str | Path) -> Any:
    from PIL import Image

    with Image.open(path) as image:
        return _white_rgb(image).copy()


def _normalize(features: Any) -> Any:
    return features / features.norm(dim=-1, keepdim=True)


def _image_features(model: Any, processor: Any, images: list[Any], device: str) -> Any:
    import torch

    inputs = processor(images=images, return_tensors="pt")
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.no_grad():
        return _normalize(model.get_image_features(**inputs))


def _text_features(model: Any, processor: Any, prompts: list[str], device: str) -> Any:
    import torch

    inputs = processor(
        text=prompts,
        return_tensors="pt",
        padding=True,
        truncation=True,
    )
    inputs = {key: value.to(device) for key, value in inputs.items()}
    with torch.no_grad():
        return _normalize(model.get_text_features(**inputs))


def _reference_features(
    model: Any,
    processor: Any,
    case: dict,
    device: str,
) -> Any:
    if case["track"] == "text":
        return _text_features(model, processor, [case["prompt"]], device)
    return _image_features(
        model,
        processor,
        [_load_image(case["input_image"])],
        device,
    )


def _score_pair(reference: Any, rendered: Any) -> list[float]:
    values = (reference @ rendered.T).squeeze(0).detach().cpu().tolist()
    return [float(value) * 100.0 for value in values]


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Score the CLIP pilot.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--device", default="cuda")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_json(args.manifest)
    output = args.output_dir.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)
    from transformers import CLIPModel, CLIPProcessor

    model_path = Path(manifest["clip"]["local_path"])
    processor = CLIPProcessor.from_pretrained(model_path, local_files_only=True)
    model = CLIPModel.from_pretrained(model_path, local_files_only=True)
    model.eval().to(args.device)

    references = {
        case["id"]: _reference_features(model, processor, case, args.device)
        for case in manifest["cases"]
    }
    rows: list[dict[str, Any]] = []
    render_features: dict[tuple[str, str], Any] = {}
    for case in manifest["cases"]:
        for method in ("adsl", "trellis"):
            render_dir = Path(case[method]["render_dir"])
            base = {
                "case_id": case["id"],
                "track": case["track"],
                "method": method,
            }
            if not completed_render(render_dir, manifest["render"]["views"]):
                rows.append(
                    {
                        **base,
                        "status": "failed",
                        "mean_clip": 0.0,
                        "max_clip": 0.0,
                        "view_scores": [],
                        "failure_zero": True,
                    }
                )
                continue
            paths = sorted(render_dir.glob("render_*.png"))
            images = [_load_image(path) for path in paths]
            features = _image_features(model, processor, images, args.device)
            render_features[(case["id"], method)] = features
            scores = _score_pair(references[case["id"]], features)
            rows.append(
                {
                    **base,
                    "status": "completed",
                    "mean_clip": mean(scores),
                    "max_clip": max(scores),
                    "view_scores": scores,
                    "render_paths": [str(path) for path in paths],
                    "failure_zero": False,
                }
            )

    shuffled: list[dict[str, Any]] = []
    for track in ("text", "image"):
        cases = [case for case in manifest["cases"] if case["track"] == track]
        if len(cases) < 2:
            continue
        for index, case in enumerate(cases):
            wrong = cases[(index + 1) % len(cases)]
            for method in ("adsl", "trellis"):
                features = render_features.get((case["id"], method))
                if features is None:
                    continue
                scores = _score_pair(references[wrong["id"]], features)
                shuffled.append(
                    {
                        "case_id": case["id"],
                        "wrong_reference_case": wrong["id"],
                        "track": track,
                        "method": method,
                        "mean_clip": mean(scores),
                    }
                )

    self_controls = []
    for case in manifest["cases"]:
        if case["track"] != "image":
            continue
        score = _score_pair(references[case["id"]], references[case["id"]])[0]
        self_controls.append({"case_id": case["id"], "self_clip": score})

    summaries = summarize(
        rows, float(manifest["equivalence_margin_clip_points"])
    )
    payload = {
        "schema_version": 1,
        "generated_at": utc_now(),
        "verification_status": "ANALYZED",
        "clip_model_path": str(model_path),
        "clip_model_config_sha256": sha256(model_path / "config.json"),
        "rows": rows,
        "summaries": summaries,
        "shuffled_controls": shuffled,
        "self_controls": self_controls,
    }
    write_json(output / "scores.json", payload)

    with (output / "scores.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=(
                "case_id", "track", "method", "status", "mean_clip",
                "max_clip", "failure_zero",
            ),
        )
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in writer.fieldnames})

    lines = [
        "# aDSL vs TRELLIS CLIP pilot",
        "",
        f"Generated: {payload['generated_at']}",
        "",
        "> This is a rough 7-case local pilot, not a reproduction of the paper's "
        "30-case Toys4K benchmark. Text-image and image-image CLIP scores are "
        "reported separately.",
        "",
        "| Track | n | aDSL | TRELLIS | Δ aDSL−TRELLIS | Rough effect |",
        "|---|---:|---:|---:|---:|---|",
    ]
    for item in summaries:
        lines.append(
            f"| {item['track']} | {item['n']} | "
            f"{item['adsl_mean_failure_zero']:.3f} | "
            f"{item['trellis_mean_failure_zero']:.3f} | "
            f"{item['mean_delta_adsl_minus_trellis']:+.3f} | "
            f"{item['rough_effect']} |"
        )
    lines.extend(
        [
            "",
            "## Paired cases",
            "",
            "| Case | Track | aDSL | TRELLIS | Delta |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for item in summaries:
        for paired in item["paired_cases"]:
            lines.append(
                f"| {paired['case_id']} | {item['track']} | "
                f"{paired['adsl']:.3f} | {paired['trellis']:.3f} | "
                f"{paired['delta']:+.3f} |"
            )

    lines.extend(
        [
            "",
            "## Shuffled-reference controls",
            "",
            "| Track | Method | Matched mean | Shuffled mean | Gap |",
            "|---|---|---:|---:|---:|",
        ]
    )
    for item in summaries:
        for method in ("adsl", "trellis"):
            matched = float(item[f"{method}_mean_failure_zero"])
            values = [
                float(row["mean_clip"])
                for row in shuffled
                if row["track"] == item["track"] and row["method"] == method
            ]
            shuffled_mean = mean(values) if values else 0.0
            lines.append(
                f"| {item['track']} | {method} | {matched:.3f} | "
                f"{shuffled_mean:.3f} | {matched - shuffled_mean:+.3f} |"
            )

    lines.extend(
        [
            "",
            "## Interpretation limits",
            "",
            "- The +/-2 point margin is an engineering heuristic, not a "
            "statistical equivalence test.",
            "- Exact counts, contact, topology, articulation, edit locality, and "
            "printability are not measured by CLIP.",
            "- The text and image tracks use different references and score "
            "scales; they must not be pooled.",
            "- The renderer uses the project color-management defaults. A white "
            "world setting is not guaranteed to encode as pure-white output "
            "pixels.",
            "- This report is ANALYZED for this seven-case pilot only; it does "
            "not validate the paper's full benchmark claim.",
        ]
    )
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output / "report.md")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
