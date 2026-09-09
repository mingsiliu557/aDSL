from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import runpy
from typing import Sequence

# Blender must initialize before trimesh-backed modules on Windows.
from adsl.core import Asset, export_glb, export_urdf
from adsl.agents.source_index import build_source_index
from adsl.tools.gpu_render_queue import submit_render_job
from adsl.tools.render import render_video


def _positive_env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _positive_env_float(name: str, default: float) -> float:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = float(raw)
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive number") from exc
    if value <= 0:
        raise ValueError(f"{name} must be a positive number")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--render", action="store_true")
    parser.add_argument("--render-view-count", type=int, default=8)
    parser.add_argument("--render-elevation", type=float, default=15.0)
    parser.add_argument("--urdf", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    namespace = runpy.run_path(str(source), run_name="__adsl_generated__")
    scene = namespace.get("scene")
    if not isinstance(scene, Asset):
        raise TypeError("Generated source must assign an adsl Asset to `scene`")
    source_index_path = output / "source_index.json"
    source_index_path.parent.mkdir(parents=True, exist_ok=True)
    source_index_path.write_text(
        build_source_index(source, scene).model_dump_json(indent=2),
        encoding="utf-8",
    )
    render_root = output / "render"
    render_root.mkdir(parents=True, exist_ok=True)
    glb_path = render_root / "scene.glb"
    export_glb(
        scene,
        filepath=glb_path,
        clear_scene=True,
        apply_modifiers=True,
    )

    urdf_path = None
    if args.urdf:
        urdf_path = render_root / "scene.urdf"
        export_urdf(
            scene,
            filepath=urdf_path,
            mesh_backend="blender",
            visual_mesh_format="glb",
        )

    render_backend = "none"
    render_job: dict[str, object] | None = None
    if args.render:
        queue_value = os.environ.get("ADSL_GPU_RENDER_QUEUE", "").strip()
        if queue_value:
            render_backend = "gpu_queue"
            render_job = submit_render_job(
                queue_root=queue_value,
                glb_path=glb_path,
                output_dir=render_root,
                width=_positive_env_int("ADSL_RENDER_WIDTH", 1024),
                height=_positive_env_int("ADSL_RENDER_HEIGHT", 1024),
                render_samples=_positive_env_int("ADSL_RENDER_SAMPLES", 256),
                elevations=(args.render_elevation,),
                num_camera_per_layer=args.render_view_count,
                wait_timeout=_positive_env_float(
                    "ADSL_GPU_RENDER_WAIT_TIMEOUT_SECONDS",
                    3600.0,
                ),
            )
        else:
            render_backend = "local"
            render_video(
                output_dir=render_root,
                glb_path=glb_path,
                elevations=(args.render_elevation,),
                num_camera_per_layer=args.render_view_count,
            )

    manifest = {
        "result_path": str(output),
        "render_root": str(render_root),
        "glb_path": str(glb_path),
        "urdf_path": None if urdf_path is None else str(urdf_path),
        "render_backend": render_backend,
        "render_job_id": None if render_job is None else render_job["job_id"],
        "source_index_path": str(source_index_path),
    }
    (output / "execution.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
