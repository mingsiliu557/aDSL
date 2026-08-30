from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import runpy
from typing import Any, Iterator, Sequence

from adsl.core import Asset, export_glb
from adsl.tools.render import render_video


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _iter_joints(
    asset: Asset,
    path: str = "scene",
) -> Iterator[tuple[str, Any]]:
    for name, joint in asset._joints.items():
        joint_path = f"{path}/{name}"
        yield joint_path, joint
        yield from _iter_joints(asset._joint_children[name], joint_path)
    for name, child in asset._children.items():
        yield from _iter_joints(child, f"{path}/{name}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Bake movable joints to a non-initial pose and render it."
    )
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--fraction", type=float, default=0.85)
    parser.add_argument("--render-view-count", type=int, default=8)
    parser.add_argument("--render-elevation", type=float, default=15.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if not 0.0 <= args.fraction <= 1.0:
        raise ValueError("--fraction must be within [0, 1]")

    source = args.source.expanduser().resolve()
    output = args.output.expanduser().resolve()
    output.mkdir(parents=True, exist_ok=True)

    namespace = runpy.run_path(str(source), run_name="__adsl_pose_probe__")
    scene = namespace.get("scene")
    if not isinstance(scene, Asset):
        raise TypeError("Generated source must assign an adsl Asset to scene")

    states: list[dict[str, Any]] = []
    for path, joint in _iter_joints(scene):
        if joint.joint_type not in {"revolute", "prismatic"}:
            continue
        if joint.limit is None:
            target = float(joint.initial) + 1.5707963267948966
        else:
            lower, upper = joint.limit
            target = float(lower) + args.fraction * (float(upper) - float(lower))
        states.append(
            {
                "path": path,
                "name": joint.name,
                "type": joint.joint_type,
                "axis": [float(value) for value in joint.axis],
                "limit": None
                if joint.limit is None
                else [float(joint.limit[0]), float(joint.limit[1])],
                "original": float(joint.initial),
                "target": target,
            }
        )
        joint.initial = target

    if not states:
        raise RuntimeError("No movable joints were found")

    glb_path = output / "scene.pose.glb"
    export_glb(
        scene,
        filepath=glb_path,
        clear_scene=True,
        apply_modifiers=True,
    )
    render_video(
        output_dir=output / "render",
        glb_path=glb_path,
        elevations=(args.render_elevation,),
        num_camera_per_layer=args.render_view_count,
    )
    payload = {
        "schema_version": 1,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "source": str(source),
        "source_sha256": _sha256(source),
        "fraction": args.fraction,
        "render_view_count": args.render_view_count,
        "render_elevation": args.render_elevation,
        "glb": str(glb_path),
        "glb_sha256": _sha256(glb_path),
        "states": states,
    }
    (output / "pose_probe.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(output / "pose_probe.json")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
