#!/usr/bin/env python3
"""Create paired vanilla/Ours render sheets without changing model artifacts."""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
from PIL import Image, ImageDraw

REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from experiments.standing_fea_30.run_batch import read_json


def render_images(workspace: Path) -> list[Path]:
    roots = [workspace / "render"]
    roots += sorted(workspace.glob("rounds/round_[0-9][0-9]/render"), reverse=True)
    roots += sorted(
        workspace.glob("rounds/round_[0-9][0-9]/candidates/*/render"),
        reverse=True,
    )
    for root in roots:
        images = sorted(root.glob("*.png"))
        if images:
            return images
    return []


def tile(path: Path | None, size: int = 360) -> Image.Image:
    canvas = Image.new("RGB", (size, size), "white")
    if path is None:
        ImageDraw.Draw(canvas).text((16, 16), "render unavailable", fill="black")
        return canvas
    with Image.open(path) as source:
        source = source.convert("RGB")
        source.thumbnail((size, size))
        canvas.paste(source, ((size - source.width) // 2, (size - source.height) // 2))
    return canvas


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--views", type=int, default=3)
    args = parser.parse_args()
    manifest = read_json(args.manifest.resolve())
    root = args.output_root.resolve()
    output = root / "contact_sheets"
    output.mkdir(parents=True, exist_ok=True)
    for case in manifest["cases"]:
        canvas = Image.new("RGB", (360 * args.views, 760), "white")
        draw = ImageDraw.Draw(canvas)
        for row, arm in enumerate(("adsl", "ours")):
            images = render_images(root / "workspaces" / arm / case["case_id"])
            draw.text((8, row * 380 + 4), f"{case['case_id']} {arm}", fill="black")
            for index in range(args.views):
                path = images[index] if index < len(images) else None
                canvas.paste(tile(path), (index * 360, row * 380 + 20))
        canvas.save(output / f"{case['case_id']}.png")
    print(output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
