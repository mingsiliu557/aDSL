from __future__ import annotations

import argparse
import os
from pathlib import Path
import subprocess
import traceback
from typing import Sequence

from experiments.clip_trellis_pilot.common import (
    completed_render,
    load_json,
    utc_now,
    write_json,
)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Render all pilot GLBs serially.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--repo-root", type=Path, required=True)
    parser.add_argument("--resume", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    manifest = load_json(args.manifest)
    config = manifest["render"]
    failures: list[dict] = []
    records: list[dict] = []
    env = os.environ.copy()
    pythonpath = [
        str((args.repo_root / "adsl-core").resolve()),
        str((args.repo_root / "adsl-agents").resolve()),
    ]
    if env.get("PYTHONPATH"):
        pythonpath.append(env["PYTHONPATH"])
    env.update(
        {
            "PYTHONPATH": os.pathsep.join(pythonpath),
            "ADSL_RENDER_ENGINE": "BLENDER_EEVEE",
            "PYTHONUNBUFFERED": "1",
        }
    )

    for case in manifest["cases"]:
        for method in ("adsl", "trellis"):
            glb = Path(case[method]["glb"])
            output = Path(case[method]["render_dir"])
            record = {
                "case_id": case["id"],
                "track": case["track"],
                "method": method,
                "glb": str(glb),
                "output": str(output),
                "started_at": utc_now(),
            }
            if args.resume and completed_render(output, config["views"]):
                record.update({"status": "skipped_complete", "completed_at": utc_now()})
                records.append(record)
                continue
            if not glb.is_file() or glb.stat().st_size == 0:
                record.update({"status": "failed", "error": "missing_or_empty_glb"})
                failures.append(record)
                records.append(record)
                continue
            if output.exists() and any(output.iterdir()):
                record.update({"status": "failed", "error": "nonempty_render_directory"})
                failures.append(record)
                records.append(record)
                continue
            output.mkdir(parents=True, exist_ok=True)
            command = [
                args.python,
                "-u",
                "-m",
                "adsl.tools.render",
                "--glb-path",
                str(glb),
                "--output-dir",
                str(output),
                "--width",
                str(config["width"]),
                "--height",
                str(config["height"]),
                "--elevations",
                *[str(value) for value in config["elevations_degrees"]],
                "--num-camera-per-layer",
                str(config["views"]),
                "--render-samples",
                str(config["samples"]),
                "--background",
                config["background"],
                "--material-mode",
                config["material_mode"],
            ]
            try:
                result = subprocess.run(
                    command,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    env=env,
                    check=False,
                )
                (output / "renderer.log").write_text(result.stdout, encoding="utf-8")
                if result.returncode != 0:
                    raise RuntimeError(f"renderer exited with {result.returncode}")
                if not completed_render(output, config["views"]):
                    raise RuntimeError("renderer output did not pass completeness check")
                record.update({"status": "completed", "completed_at": utc_now()})
            except Exception as exc:
                record.update(
                    {
                        "status": "failed",
                        "error": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(),
                    }
                )
                failures.append(record)
            records.append(record)
            write_json(args.manifest.parent / "render_run.json", {"records": records})

    write_json(
        args.manifest.parent / "render_run.json",
        {
            "schema_version": 1,
            "completed_at": utc_now(),
            "status": "completed" if not failures else "partial",
            "failures": failures,
            "records": records,
        },
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
