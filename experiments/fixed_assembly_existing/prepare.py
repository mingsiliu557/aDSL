"""Re-execute only the three preselected historical aDSL assets; no API/checkers."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import time
import traceback
import zipfile

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "local_experiment/fixed_assembly_existing_20260919"
ARCHIVE = ROOT / "adsl_only_historical_8cases_3d_assets_20260915.zip"
SELECTION = {
    "SF07": "Round pedestal table: two main semantic groups, tabletop and integrated pedestal; first minimal historical-asset conversion.",
    "SF03": "Existing wooden high-back chair: seat, four repeated legs and backrest test semantic grouping without replacing its shape.",
    "SF13": "Existing five-shelf bookshelf: repeated boards and side panels test grouping of real semantic subparts, including original wood-grain details.",
}


def save(path, value):
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")


def digest(path):
    value = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()


def main():
    os.environ.pop("ADSL_GPU_RENDER_QUEUE", None)
    os.environ.update(ADSL_RENDER_ENGINE="CYCLES", ADSL_RENDER_WIDTH="512",
                      ADSL_RENDER_HEIGHT="512", ADSL_RENDER_SAMPLES="32",
                      OMP_NUM_THREADS="4", OPENBLAS_NUM_THREADS="1")
    import numpy as np
    import trimesh
    from adsl.agents.overhang_edit import version_record
    from adsl.agents.utils.execution import execute_asset_source

    OUTPUT.mkdir(parents=True, exist_ok=True)
    archive_hash = digest(ARCHIVE)
    summary = {"case_order": list(SELECTION), "api_calls": 0, "checkers": [], "cases": []}
    with zipfile.ZipFile(ARCHIVE) as archive:
        manifest = json.loads(archive.read("assets_manifest.json"))
        for case_id, reason in SELECTION.items():
            case_dir = OUTPUT / case_id
            original = case_dir / "original"
            original.mkdir(parents=True, exist_ok=False)
            record = next(g for g in manifest["asset_groups"]
                          if g["case_id"] == case_id and g["arm"] == "adsl")
            source = original / "source.py"
            source.write_bytes(archive.read(f"{case_id}/adsl/source.py"))
            if digest(source) != record["source_sha256"]:
                raise ValueError(f"{case_id}: archived source SHA256 differs from manifest")
            prefix = f"{case_id}/adsl/"
            extracted = []
            for name in archive.namelist():
                if not name.startswith(prefix):
                    continue
                relative = name[len(prefix):]
                if relative != "generated/scene.glb" and not (
                        relative.startswith("renders/") and relative.endswith(".png")):
                    continue
                target = original / "archive" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(archive.read(name))
                extracted.append(target)
            save(original / "provenance.json", {
                "case_id": case_id, "arm": "adsl", "prompt": record["prompt"],
                "selection_reason": reason, "archive": str(ARCHIVE),
                "archive_sha256": archive_hash, "source_sha256": digest(source),
                "historical_batch": manifest["historical_batch"],
                "archived_group": record,
                "selected_before_execution": True,
            })
            started = time.monotonic()
            row = {"case_id": case_id, "status": "RUNNING", "source": str(source)}
            summary["cases"].append(row)
            save(OUTPUT / "prepared.json", summary)
            print(f"{case_id}: original execution started (300 s existing timeout)", flush=True)
            execution = None
            try:
                execution = execute_asset_source(source, original / "execution", render=True,
                                                 export_urdf=False, timeout=300)
                model = trimesh.load(execution.glb_path, force="scene", process=False)
                y_up = np.asarray(model.bounds)
                # glTF Y-up -> authored Z-up: (x, y, z) -> (x, -z, y).
                z_up = np.array([[y_up[0, 0], -y_up[1, 2], y_up[0, 1]],
                                 [y_up[1, 0], -y_up[0, 2], y_up[1, 1]]])
                frozen = {"mm_per_unit": 40.0, "fit_offset_mm": 0.2,
                          "final_size_mm": ((z_up[1] - z_up[0]) * 40).tolist()}
                save(case_dir / "frozen.json", frozen)
                (original / "stdout.log").write_text(execution.stdout)
                (original / "stderr.log").write_text(execution.stderr)
                row.update(status="READY", frozen=frozen,
                           original_bounds_scene_units_z_up=z_up.tolist(),
                           glb=str(execution.glb_path), render_count=len(execution.render_paths))
            except Exception as error:
                (original / "error.log").write_text(traceback.format_exc())
                for field in ("stdout", "stderr"):
                    value = getattr(error, field, None)
                    if value is not None:
                        (original / f"{field}.log").write_text(
                            value.decode(errors="replace") if isinstance(value, bytes) else str(value))
                row.update(status="ORIGINAL_EXECUTION_FAILED", error_type=type(error).__name__,
                           reason=str(error)[:800], evidence=str(original / "error.log"))
            row["elapsed_seconds"] = time.monotonic() - started
            save(original / "version.json", version_record(
                "original", source, execution, extra_files=extracted))
            save(case_dir / "prepared.json", row)
            save(OUTPUT / "prepared.json", summary)
            print(json.dumps(row, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
