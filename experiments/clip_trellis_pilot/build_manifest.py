from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence

from experiments.clip_trellis_pilot.common import (
    assert_data_mount,
    sha256,
    utc_now,
    write_json,
)


DEFAULT_AUDIT_ROOT = Path("/jiigan-hp/lms/aDSL/experiment/audit_20260830")
TEXT_CASES = (
    ("T01", "T01-main"),
    ("T02", "T02-bookshelf"),
    ("T03", "T03-radial-wheel"),
    ("T04", "T04-hollow-mug"),
    ("T05", "T05-patterned-desk"),
)
IMAGE_CASES = (
    ("I01", "I01-example-image", "01_example.png"),
    ("I02", "I02-example-arti", "01_example-arti.png"),
)


def _case_base(
    case_id: str,
    invocation: str,
    track: str,
    workspace: Path,
    output_root: Path,
) -> dict:
    glb = workspace / "scene.glb"
    if not glb.is_file():
        raise FileNotFoundError(glb)
    return {
        "id": case_id,
        "invocation": invocation,
        "track": track,
        "adsl": {
            "glb": str(glb.resolve()),
            "glb_sha256": sha256(glb),
            "render_dir": str((output_root / "renders" / "adsl" / case_id).resolve()),
        },
        "trellis": {
            "seed": 1,
            "glb": str((output_root / "trellis" / case_id / "scene.glb").resolve()),
            "render_dir": str(
                (output_root / "renders" / "trellis" / case_id).resolve()
            ),
        },
    }


def build_manifest(audit_root: Path, output_root: Path) -> dict:
    cases: list[dict] = []
    for case_id, invocation in TEXT_CASES:
        workspace = audit_root / invocation
        prompt_path = workspace / "user_input.txt"
        if not prompt_path.is_file():
            raise FileNotFoundError(prompt_path)
        case = _case_base(case_id, invocation, "text", workspace, output_root)
        case["prompt"] = prompt_path.read_text(encoding="utf-8").strip()
        case["prompt_file"] = str(prompt_path.resolve())
        case["prompt_sha256"] = sha256(prompt_path)
        cases.append(case)

    for case_id, invocation, image_name in IMAGE_CASES:
        workspace = audit_root / invocation
        image_path = workspace / "user_input_images" / image_name
        if not image_path.is_file():
            raise FileNotFoundError(image_path)
        case = _case_base(case_id, invocation, "image", workspace, output_root)
        case["input_image"] = str(image_path.resolve())
        case["input_image_sha256"] = sha256(image_path)
        cases.append(case)

    return {
        "schema_version": 1,
        "study_id": output_root.name,
        "generated_at": utc_now(),
        "verification_status": "UNVERIFIED",
        "scope": (
            "Rough local CLIP pilot. Five existing static text cases and two "
            "existing image-conditioned cases; not the paper's 30-case benchmark."
        ),
        "paper": "https://arxiv.org/abs/2608.17975",
        "equivalence_margin_clip_points": 2.0,
        "case_selection": {
            "policy": (
                "All existing independent static text creation cases T01-T05, "
                "plus both existing image-conditioned cases I01-I02. Editing, "
                "memory, scene, and articulation semantics are excluded."
            ),
            "text_count": len(TEXT_CASES),
            "image_count": len(IMAGE_CASES),
        },
        "render": {
            "engine": "BLENDER_EEVEE",
            "width": 1024,
            "height": 1024,
            "samples": 256,
            "elevations_degrees": [15.0],
            "views": 8,
            "azimuth_step_degrees": 45.0,
            "background": "white",
            "material_mode": "neutral",
            "normalization": "AABB center and uniform max-extent scale to 0.8",
        },
        "clip": {
            "model": "openai/clip-vit-large-patch14",
            "local_path": "/jiigan-hp/TRELLIS/pretrained/clip-vit-large-patch14",
            "aggregation": "mean cosine across eight views, multiplied by 100",
        },
        "trellis": {
            "source_commit": "6b0d64751ad54d9c32d7b05fec482eb29178f56f",
            "text_model": "/jiigan-hp/TRELLIS/pretrained/TRELLIS-text-xlarge-original",
            "image_model": "/jiigan-hp/TRELLIS/pretrained/TRELLIS-image-large",
            "simplify": 0.95,
            "texture_size": 1024,
            "retry_policy": "none",
        },
        "cases": cases,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Freeze the local CLIP pilot cases.")
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--audit-root", type=Path, default=DEFAULT_AUDIT_ROOT)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    output_root = args.output_root.expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=False)
    assert_data_mount(output_root)
    manifest = build_manifest(args.audit_root.expanduser().resolve(), output_root)
    write_json(output_root / "manifest.json", manifest)
    print(output_root / "manifest.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
