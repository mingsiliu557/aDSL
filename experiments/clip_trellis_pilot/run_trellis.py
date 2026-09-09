from __future__ import annotations

import argparse
import gc
import os
from pathlib import Path
import sys
import traceback
from typing import Any, Sequence

from experiments.clip_trellis_pilot.common import load_json, sha256, utc_now, write_json


def _load_runtime(source_root: Path) -> tuple[Any, Any, Any]:
    os.environ.setdefault("ATTN_BACKEND", "xformers")
    os.environ.setdefault("SPCONV_ALGO", "native")
    sys.path.insert(0, str(source_root))
    from trellis.pipelines import TrellisImageTo3DPipeline, TrellisTextTo3DPipeline
    from trellis.utils import postprocessing_utils

    return TrellisTextTo3DPipeline, TrellisImageTo3DPipeline, postprocessing_utils


def _status_path(case: dict) -> Path:
    return Path(case["trellis"]["glb"]).parent / "status.json"


def _case_complete(case: dict) -> bool:
    glb = Path(case["trellis"]["glb"])
    return glb.is_file() and glb.stat().st_size > 0


def _run_case(
    pipeline: Any,
    postprocessing_utils: Any,
    case: dict,
    manifest: dict,
) -> bool:
    output = Path(case["trellis"]["glb"]).parent
    output.mkdir(parents=True, exist_ok=True)
    status = {
        "schema_version": 1,
        "case_id": case["id"],
        "track": case["track"],
        "seed": case["trellis"]["seed"],
        "started_at": utc_now(),
        "status": "running",
        "retry_policy": "none",
    }
    write_json(_status_path(case), status)
    try:
        if case["track"] == "text":
            model_input: Any = case["prompt"]
        else:
            from PIL import Image

            with Image.open(case["input_image"]) as image:
                model_input = image.convert("RGBA").copy()
        outputs = pipeline.run(model_input, seed=int(case["trellis"]["seed"]))
        glb = postprocessing_utils.to_glb(
            outputs["gaussian"][0],
            outputs["mesh"][0],
            simplify=float(manifest["trellis"]["simplify"]),
            texture_size=int(manifest["trellis"]["texture_size"]),
        )
        destination = Path(case["trellis"]["glb"])
        glb.export(destination)
        if not destination.is_file() or destination.stat().st_size == 0:
            raise RuntimeError("TRELLIS exported an empty or missing GLB")
        status.update(
            {
                "status": "completed",
                "completed_at": utc_now(),
                "glb": str(destination),
                "glb_bytes": destination.stat().st_size,
                "glb_sha256": sha256(destination),
            }
        )
        return True
    except Exception as exc:
        status.update(
            {
                "status": "failed",
                "failed_at": utc_now(),
                "error": f"{type(exc).__name__}: {exc}",
                "traceback": traceback.format_exc(),
            }
        )
        return False
    finally:
        write_json(_status_path(case), status)


def _release_pipeline(pipeline: Any) -> None:
    pipeline.cpu()
    gc.collect()
    import torch

    torch.cuda.empty_cache()
    torch.cuda.synchronize()


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run clean TRELLIS baselines.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument(
        "--model-root",
        type=Path,
        default=Path("/jiigan-hp/TRELLIS"),
        help="Directory containing the local pretrained/ directory.",
    )
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_json(args.manifest)
    source_root = args.source_root.expanduser().resolve()
    model_root = args.model_root.expanduser().resolve()
    if not source_root.is_dir():
        raise FileNotFoundError(source_root)
    os.chdir(model_root)
    text_cls, image_cls, postprocessing_utils = _load_runtime(source_root)
    failures: list[str] = []

    tracks = (
        ("text", text_cls, Path(manifest["trellis"]["text_model"])),
        ("image", image_cls, Path(manifest["trellis"]["image_model"])),
    )
    for track, pipeline_class, model_path in tracks:
        cases = [case for case in manifest["cases"] if case["track"] == track]
        pending = [
            case for case in cases if not (args.resume and _case_complete(case))
        ]
        if not pending:
            continue
        print(f"Loading {track} pipeline from {model_path}", flush=True)
        pipeline = pipeline_class.from_pretrained(str(model_path))
        pipeline.cuda()
        try:
            for case in pending:
                print(f"TRELLIS {track} case {case['id']}", flush=True)
                if not _run_case(pipeline, postprocessing_utils, case, manifest):
                    failures.append(case["id"])
        finally:
            _release_pipeline(pipeline)

    summary = {
        "schema_version": 1,
        "completed_at": utc_now(),
        "source_root": str(source_root),
        "source_commit": manifest["trellis"]["source_commit"],
        "failed_cases": failures,
        "status": "completed" if not failures else "partial",
    }
    write_json(args.manifest.parent / "trellis_run.json", summary)
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
