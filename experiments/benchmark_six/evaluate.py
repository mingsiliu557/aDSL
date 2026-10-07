"""Read-only evaluation of each method's native selected output.

The official GLB is one declared print body regardless of scene/material nodes.
All physical measurements use a separate 150-mm copy; no repair, source execution,
candidate selection, FEA, or model/API call occurs here.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
import trimesh

LONGEST_MM = 150.0
OVERHANG = {"overhang_threshold_from_horizontal_deg": 45.0, "layer_height_mm": 0.2}
STANDING = {"duration_seconds": 5.0, "timestep_seconds": 0.002,
            "friction": [0.5, 0.005, 0.0001], "tilt_threshold_deg": 25.0,
            "settle_window_seconds": 0.5, "settle_linear_m_s": 0.001,
            "settle_angular_rad_s": 0.01, "collision_backend": "rigid_flex"}
MATERIAL = {"density_kg_m3": 1240.0}
# The same glTF Y-up -> Blender/local Z-up conversion as export_assembly.
Z_UP = np.array([[1., 0., 0., 0.], [0., 0., -1., 0.],
                 [0., 1., 0., 0.], [0., 0., 0., 1.]])


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
                    encoding="utf-8")


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def resolve_path(value, directory):
    path = Path(value)
    return path.resolve() if path.is_absolute() else (Path(directory) / path).resolve()


def case_config(run_root, case_id):
    root = Path(run_root)
    path = root / "config" / "cases.json"
    if not path.is_file():
        path = root / "cases.json"
    payload = read_json(path)
    rows = payload if isinstance(payload, list) else payload["cases"]
    matches = [r for r in rows if r.get("case_id", r.get("id")) == case_id]
    if len(matches) != 1:
        raise ValueError("case must occur exactly once in frozen cases.json")
    case = matches[0]
    pitch = float(case["voxel_pitch_mm"])
    if not np.isfinite(pitch) or pitch <= 0:
        raise ValueError("case voxel_pitch_mm must be finite and positive")
    return case


def scale_for_bounds(bounds):
    bounds = np.asarray(bounds, dtype=float)
    if bounds.shape != (2, 3) or not np.isfinite(bounds).all():
        raise ValueError("invalid assembled bounds")
    extents = bounds[1] - bounds[0]
    if np.any(extents <= 0):
        raise ValueError("non-volumetric assembled bounds")
    return LONGEST_MM / float(extents.max())


def _write_manifest(directory, manifest, meshes):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    manifest["files_sha256"] = {}
    for part in manifest["parts"]:
        if part["id"] not in meshes:
            continue
        # ASCII avoids introducing another float32 loss before the checker.
        path = directory / part["stl"]
        meshes[part["id"]].export(path, file_type="stl_ascii")
        manifest["files_sha256"][path.name] = sha256(path)
    manifest["stl_vertex_unit"] = "mm"
    path = directory / "assembly_manifest.json"
    write_json(path, manifest)
    return path


def official_manifest(glb, source, directory):
    """Apply every scene transform, preserving raw node face groups inside N=1."""
    scene = trimesh.load(Path(glb), force="scene", process=False)
    chunks, groups, nodes, offset = [], [], [], 0
    for node in sorted(scene.graph.nodes_geometry):
        transform, key = scene.graph[node]
        mesh = scene.geometry[key].copy()
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError("non-triangle scene geometry")
        mesh.apply_transform(Z_UP @ np.asarray(transform))
        chunks.append(mesh)
        groups.append([offset, offset + len(mesh.faces)])
        offset += len(mesh.faces)
        nodes.append({"node": str(node), "geometry": str(key),
                      "face_count": len(mesh.faces), "world_bounds": mesh.bounds.tolist()})
    if not chunks:
        raise ValueError("official GLB contains no triangle geometry")
    whole = trimesh.util.concatenate(chunks)
    factor = scale_for_bounds(whole.bounds)
    whole.apply_scale(factor)
    identity = np.eye(4).tolist()
    part = {"id": "whole", "components": ["whole"], "stl": "whole.stl",
            "assembly_transform": identity, "print_transform_mm": identity,
            "mesh_face_groups": groups}
    manifest = {"source_sha256": sha256(source), "mm_per_unit": 1., "root_id": "whole",
                "part_declarations": [deepcopy(part)], "parts": [part], "connections": [],
                "evaluation_adapter": "official_glb_whole_material",
                "normalization_scale": factor, "target_longest_mm": LONGEST_MM,
                "glb_sha256": sha256(glb), "raw_scene_nodes": nodes,
                "axis_conversion": "glTF_Y_up_to_Z_up", "mesh_repair": "NONE"}
    return _write_manifest(directory, manifest, {"whole": whole})


def ours_manifest(original, source, directory):
    """Scale published parts and dimensional declarations without changing N."""
    from adsl.core.assembly_topology import mm_matrix, read_print_mesh
    from adsl.agents.assembly_topology import _input, _part_file
    original = Path(original)
    native = _input(original, Path(source))
    manifest = deepcopy(native)
    meshes, bounds, errors = {}, [], []
    declarations = manifest.get("part_declarations", manifest["parts"])
    ids = [p["id"] for p in declarations]
    if not ids or len(ids) != len(set(ids)):
        raise ValueError("empty or duplicate native print-part declarations")
    for part in native["parts"]:
        try:
            mesh = read_print_mesh(_part_file(original, native, part), part["print_transform_mm"])
            world = mesh.copy()
            world.apply_transform(mm_matrix(part["assembly_transform"], native["mm_per_unit"]))
            bounds.append(world.bounds)
            meshes[part["id"]] = mesh
        except (ValueError, OSError, KeyError, RuntimeError) as error:
            errors.append({"part_id": part["id"], "status": "INDETERMINATE", "reason": str(error)[:240]})
    # A missing part makes the overall normalization unknowable. Never use a
    # partial AABB to silently shrink the other pieces or omit a declared piece.
    if set(meshes) != set(ids):
        raise ValueError("complete native part bounds unavailable: " + json.dumps(errors)[:600])
    boxes = np.asarray(bounds)
    factor = scale_for_bounds([boxes[:, 0].min(axis=0), boxes[:, 1].max(axis=0)])
    manifest["mm_per_unit"] = float(native["mm_per_unit"]) * factor
    for mesh in meshes.values():
        mesh.apply_scale(factor)
    # STL now transports local-mm geometry. Assembly and interface translations
    # remain in original units and are scaled through mm_per_unit exactly once.
    for index, part in enumerate(manifest["parts"]):
        part["stl"] = f"part_{index:03d}.stl"
        part["print_transform_mm"] = np.eye(4).tolist()
    for connection in manifest["connections"]:
        connection["parameters"] = {k: float(v) * factor if k.endswith("_mm") else v
                                     for k, v in connection["parameters"].items()}
    manifest.pop("partition_reference_inputs", None)
    manifest["evaluation_adapter"] = "ours_native_declared_parts"
    manifest["native_manifest_sha256"] = sha256(original)
    manifest["normalization_scale"] = factor
    manifest["target_longest_mm"] = LONGEST_MM
    manifest["mesh_repair"] = "NONE"
    return _write_manifest(directory, manifest, meshes)


def unavailable(reason, status="INDETERMINATE"):
    return {"status": status, "reason": str(reason)[:800]}


def physical_checks(manifest, source, output, standing_applicable):
    from adsl.agents.assembly_physics import run_assembly_checks
    from adsl.agents.assembly_topology import checker_spec as topology_spec
    from adsl.agents.assembly_standing import checker_spec as standing_spec
    from adsl.agents.utils.execution import ExecutionResult
    specs = [topology_spec(timeout_seconds=900)]
    if standing_applicable:
        specs.append(standing_spec(timeout_seconds=900))
    execution = ExecutionResult(Path(manifest).parent, Path(manifest).parent / "scene.glb", None, (), "", "")
    runs = run_assembly_checks(specs, execution=execution, source=Path(source), root=Path(output),
                               physics={"standing": STANDING, "material": MATERIAL})
    result = {r.spec.name: r.result.model_dump() for r in runs}
    paths = {r.spec.name.removeprefix("assembly_"): str(r.output_dir / "result.json") for r in runs}
    if not standing_applicable:
        result["assembly_standing"] = unavailable("flying reference pose; standing is not applicable", "NA")
    return result, paths


def print_measurements(manifest, source, output, voxel_pitch_mm):
    from adsl.agents.assembly_physics import load_parts
    from adsl.agents.assembly_overhang import measure_part
    from adsl.agents.partition_score import best_print_pose, rotations24
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    args = SimpleNamespace(manifest=Path(manifest), source=Path(source), output=output,
                           topology_cache=output.parent / "checkers" / "assembly_topology")
    report, inputs, meshes = load_parts(args)
    rotations = rotations24()
    rows = []
    for input_row in inputs:
        name = input_row["part_id"]
        row = {"part_id": name, "geometry_status": input_row["status"],
               "status": "INDETERMINATE", "gap_voxels": None, "min_overhang_area_mm2": None,
               "material_volume_mm3": None}
        if name not in meshes:
            row["reason"] = input_row.get("reason", "solid unavailable")
        else:
            mesh = meshes[name]
            row["material_volume_mm3"] = float(mesh.volume)
            # Area and G have independently selected rotations. A topology FAIL
            # remains visible while a reliable disconnected material is measured.
            area_rows = []
            try:
                for index, rotation in enumerate(rotations):
                    transform = np.eye(4)
                    transform[:3, :3] = rotation
                    transform[:3, 3] = -(mesh.vertices @ rotation.T).min(axis=0)
                    measured, _ = measure_part(mesh, transform, OVERHANG)
                    area_rows.append({"rotation_id": index, "area_mm2": measured["area_mm2"],
                                      "uncertainty_mm2": measured["uncertainty"]["bound_mm2"],
                                      "print_transform_mm": transform.tolist()})
                best = min(area_rows, key=lambda r: (r["area_mm2"], r["rotation_id"]))
                row.update(min_overhang_area_mm2=best["area_mm2"], area_best_pose=best,
                           area_orientations=area_rows)
            except (ValueError, RuntimeError, OSError, ImportError) as error:
                row["area_error"] = f"{type(error).__name__}: {str(error)[:240]}"
            try:
                gap = best_print_pose(mesh, {"voxel_pitch_mm": voxel_pitch_mm}, rotations)
                row["gap_voxels"] = gap["gap_voxels"]
                row["gap_best_pose"] = gap
                row["gap_volume_mm3"] = gap["gap_voxels"] * voxel_pitch_mm ** 3
            except (ValueError, RuntimeError, OSError, ImportError) as error:
                row["gap_error"] = f"{type(error).__name__}: {str(error)[:240]}"
            if row["gap_voxels"] is not None and row["min_overhang_area_mm2"] is not None:
                row["status"] = "PASS"
        rows.append(row)
        write_json(output / "progress.json", {"parts": rows})
    count = len(report.get("part_declarations", report["parts"]))
    complete = len(rows) == count and count > 0
    area_complete = complete and all(r["min_overhang_area_mm2"] is not None for r in rows)
    gap_complete = complete and all(r["gap_voxels"] is not None for r in rows)
    volume_complete = complete and all(r["material_volume_mm3"] is not None for r in rows)
    gap = sum(r["gap_voxels"] for r in rows) if gap_complete else None
    return {"status": "PASS" if area_complete and gap_complete else "INDETERMINATE",
            "N": count, "G": gap, "gap_volume_mm3": gap * voxel_pitch_mm ** 3 if gap is not None else None,
            "min_overhang_area_mm2": sum(r["min_overhang_area_mm2"] for r in rows) if area_complete else None,
            "material_volume_mm3": sum(r["material_volume_mm3"] for r in rows) if volume_complete else None,
            "voxel_pitch_mm": voxel_pitch_mm, "parts": rows,
            "support_material": "NOT_EVALUATED", "score": "NOT_COMPUTED_FOR_COMMON_COMPARISON"}


def render_final(glb, output, render_python=None, skip=False):
    output = Path(output)
    config = {"engine": "CYCLES", "device": "CPU", "width": 512, "height": 512,
              "samples": 64, "view_layout": "review_eight", "background": "transparent"}
    result = {"config": config, "native_views": [], "neutral_views": [], "status": "INDETERMINATE"}
    if skip:
        result["reason"] = "render explicitly skipped; appearance remains unmeasured"
        return result
    env = dict(os.environ)
    env.update(ADSL_RENDER_ENGINE="CYCLES", ADSL_RENDER_WIDTH="512", ADSL_RENDER_HEIGHT="512",
               ADSL_RENDER_SAMPLES="64", CUDA_VISIBLE_DEVICES="", HIP_VISIBLE_DEVICES="",
               ROCR_VISIBLE_DEVICES="")
    for mode in ("native", "neutral"):
        directory = output / mode
        directory.mkdir(parents=True, exist_ok=True)
        cpu_cli = ("import bpy, runpy; bpy.context.scene.cycles.device = 'CPU'; "
                   "runpy.run_module('adsl.tools.render', run_name='__main__')")
        command = [str(render_python or sys.executable), "-c", cpu_cli, "--glb-path", str(glb),
                   "--output-dir", str(directory), "--width", "512", "--height", "512",
                   "--render-samples", "64", "--view-layout", "review_eight", "--material-mode", mode,
                   "--background", "transparent"]
        write_json(directory / "invocation.json", {"command": command, "config": config})
        try:
            with (directory / "stdout.log").open("w") as stdout, (directory / "stderr.log").open("w") as stderr:
                # The thin launcher explicitly fixes the device before invoking
                # the existing renderer CLI; its scene clear preserves settings.
                process = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, timeout=900, check=False)
            paths = [directory / f"render_{i:04d}.png" for i in range(1, 9)]
            if process.returncode != 0 or not all(p.is_file() and p.stat().st_size for p in paths):
                result[f"{mode}_error"] = "renderer failed or eight final views unavailable"
            else:
                result[f"{mode}_views"] = [str(p.resolve()) for p in paths]
        except (OSError, subprocess.TimeoutExpired) as error:
            result[f"{mode}_error"] = f"{type(error).__name__}: {str(error)[:240]}"
    if len(result["native_views"]) == len(result["neutral_views"]) == 8:
        result["status"] = "PASS"
    return result


def native_dapper(job, directory):
    if job["arm"] != "ours":
        return unavailable("internal ours objective only; excluded from cross-method comparison", "NA")
    if not all(job.get(k) for k in ("native_partition_result", "selected_manifest", "source")):
        return unavailable("no explicitly bound native Dapper report; no directory scan or recomputation", "UNAVAILABLE")
    try:
        path = resolve_path(job["native_partition_result"], directory)
        report = read_json(path)
        source_hash = sha256(resolve_path(job["source"], directory))
        expected_source = job.get("selected_source_sha256", source_hash)
        manifest_hash = sha256(resolve_path(job["selected_manifest"], directory))
        binding = job.get("native_partition_binding", {})
        assumptions = report.get("assumptions", {})
        if (source_hash != expected_source or assumptions.get("source_sha256") != expected_source
                or assumptions.get("manifest_sha256") != manifest_hash):
            return unavailable("native Dapper report does not match selected source/manifest hashes", "UNAVAILABLE")
        expected_binding = {"source_sha256": source_hash, "manifest_sha256": manifest_hash,
                            "report_sha256": sha256(path)}
        if binding and any(binding.get(k) != v for k, v in expected_binding.items()):
            return unavailable("native Dapper retained-version binding changed", "UNAVAILABLE")
        objective = report.get("metrics", {}).get("partition_objective")
        if objective is not None:
            return {"status": "AVAILABLE", "cross_method_comparable": False,
                    "native_result_path": str(path), "partition_objective": objective}
    except (OSError, ValueError, KeyError) as error:
        return unavailable(f"native Dapper report unavailable: {type(error).__name__}", "UNAVAILABLE")
    return unavailable("bound report has no native Dapper objective", "UNAVAILABLE")


def evaluate(run_root, case_id, arm, *, render_python=None, skip_render=False):
    root = Path(run_root).resolve()
    case = case_config(root, case_id)
    directory = root / "jobs" / case_id / arm
    job = read_json(directory / "job.json")
    if job.get("case_id") != case_id or job.get("arm") != arm:
        raise ValueError("job identity differs from requested case/arm")
    output = directory / "evaluation"
    output.mkdir(parents=True, exist_ok=True)
    flying = case_id in ("dragon_007", "Toys4K_dragon_007")
    standing_applicable = bool(case.get("standing_applicable", True)) and not flying
    result = {"case_id": case_id, "arm": arm, "status": "INDETERMINATE",
              "generation": job.get("generation", {}), "included_in_denominator": True,
              "denominators": {"appearance": True, "printing": True, "standing": standing_applicable},
              "selected_output_policy": "native_selection_frozen_before_offline_evaluation",
              "config": {"longest_mm": LONGEST_MM, "voxel_pitch_mm": float(case["voxel_pitch_mm"]),
                         "overhang": OVERHANG, "standing": STANDING, "material": MATERIAL,
                         "orientation_set": "axis_aligned_24", "fea": "DISABLED"},
              "metrics": {"N": 1 if arm == "official" else None, "G": None, "gap_volume_mm3": None,
                          "min_overhang_area_mm2": None, "material_volume_mm3": None, "parts": []},
              "physics_result": {}, "interface": unavailable("no baseline assembly interfaces", "NA") if arm == "official" else unavailable("not measured"),
              "standing": unavailable("flying reference pose", "NA") if not standing_applicable else unavailable("not measured"),
              "appearance": unavailable("common read-only image critic pending", "PENDING"),
              "render_paths": {"status": "INDETERMINATE", "native_views": [], "neutral_views": []}}
    write_json(output / "result.json", result)
    status = str(job.get("generation", {}).get("status", "")).lower()
    published = job.get("selected_glb") and resolve_path(job["selected_glb"], directory).is_file()
    failed = status in ("fail", "failed", "error", "generation_failed", "timeout", "api_interrupted") or not published
    if failed:
        reason = job.get("generation", {}).get("reason", "native generation did not publish selected GLB")
        interrupted = status == "api_interrupted"
        result.update(status="API_INTERRUPTED" if interrupted else "FAIL",
                      failure_stage="generation_api_interruption" if interrupted else "generation", reason=reason)
        result["appearance"] = unavailable(reason, "INDETERMINATE" if interrupted else "FAIL")
        if interrupted:
            result["topology"] = unavailable(reason)
    else:
        glb = resolve_path(job["selected_glb"], directory)
        result["selected_glb"] = str(glb)
        # Rendering is independent of solid validity; an open mesh is still
        # visible, but no geometric/physics verdict is fabricated for it.
        result["render_paths"] = render_final(glb, output / "final_views", render_python, skip_render)
        write_json(output / "result.json", result)
        try:
            source = resolve_path(job.get("source") or job["selected_glb"], directory)
            result["selected_output_sha256"] = sha256(glb)
            if arm == "official":
                manifest = official_manifest(glb, source, output / "material")
            else:
                original = resolve_path(job["selected_manifest"], directory)
                native = read_json(original)
                result["metrics"]["N"] = len(native.get("part_declarations", native["parts"]))
                manifest = ours_manifest(original, source, output / "material")
            result["evaluation_manifest"] = str(manifest)
            physics, paths = physical_checks(manifest, source, output, standing_applicable)
            result["physics_result"] = paths
            result["topology"] = physics["assembly_topology"]
            result["standing"] = physics["assembly_standing"]
            if arm == "ours":
                interfaces = [r for r in result["topology"].get("metrics", {}).get("items", []) if r.get("kind") == "interface"]
                interface_status = "NA" if not interfaces else "FAIL" if any(r["status"] == "FAIL" for r in interfaces) else "PASS" if all(r["status"] == "PASS" for r in interfaces) else "INDETERMINATE"
                result["interface"] = {"status": interface_status, "items": interfaces}
            write_json(output / "result.json", result)
            result["metrics"] = print_measurements(manifest, source, output / "printing", float(case["voxel_pitch_mm"]))
            required = [result["topology"]["status"], result["metrics"]["status"], result["render_paths"]["status"]]
            if standing_applicable:
                required.append(result["standing"]["status"])
            result["status"] = "FAIL" if "FAIL" in required else "PASS" if all(s == "PASS" for s in required) else "INDETERMINATE"
        except (ValueError, KeyError, OSError, RuntimeError, ImportError) as error:
            known_fail = any(result.get(k, {}).get("status") == "FAIL" for k in ("topology", "standing"))
            result.update(status="FAIL" if known_fail else "INDETERMINATE", failure_stage="geometry_or_physics",
                          reason=f"{type(error).__name__}: {str(error)[:600]}")
            result.setdefault("topology", unavailable(result["reason"]))
    result["native_dapper"] = native_dapper(job, directory)
    views = result["render_paths"].get("native_views", [])
    ready = len(views) == 8
    if not ready and not failed:
        result["appearance"] = unavailable("eight common final native views unavailable")
    request = {"profile": "six_method_readonly_v1", "status": "READY" if ready else "INDETERMINATE",
               "requirement": case["requirement"], "reference_image": str(resolve_path(case["input_image"], root)),
               "rendered_images": views if ready else [], "read_only": True, "no_candidate_selection": True}
    write_json(output / "image_critic_request.json", request)
    write_json(output / "result.json", result)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    parser.add_argument("--case", required=True)
    parser.add_argument("--arm", required=True, choices=("official", "ours"))
    parser.add_argument("--render-python", type=Path)
    parser.add_argument("--skip-render", action="store_true", help="diagnostic only; appearance stays INDETERMINATE")
    args = parser.parse_args(argv)
    result = evaluate(args.run_root, args.case, args.arm, render_python=args.render_python, skip_render=args.skip_render)
    print(json.dumps({"case_id": args.case, "arm": args.arm, "status": result["status"],
                      "result": str(args.run_root / "jobs" / args.case / args.arm / "evaluation" / "result.json")}))
    # Failed cases are data in the fixed denominator, not orchestration errors.
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
