#!/usr/bin/env python3
"""Read-only FFF support audit for frozen aDSL URDF assets.

The source models are never edited. Derived printable meshes, G-code, overlays,
and reports are written only below ``--output-dir``.
"""

from __future__ import annotations

import argparse
import bisect
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import numpy as np
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union
import trimesh


HERE = Path(__file__).resolve().parent
REPO = HERE.parents[1]
FINAL_PATH = REPO / "experiments" / "final_standing_stability" / "analyze.py"
SPEC = importlib.util.spec_from_file_location("adsl_final_standing_for_support", FINAL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {FINAL_PATH}")
FINAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FINAL
SPEC.loader.exec_module(FINAL)

GCODE_NUMBER = re.compile(r"([XYZE])(-?(?:\d+(?:\.\d*)?|\.\d+))")
SOURCE_SUFFIXES = {".urdf", ".glb", ".stl", ".obj", ".py"}


@dataclass
class TaggedMesh:
    name: str
    mesh: trimesh.Trimesh
    face_start: int
    face_stop: int


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def source_hashes(root: Path) -> dict[str, str]:
    result: dict[str, str] = {}
    for path in sorted(root.rglob("*")):
        if path.is_file() and (path.name == "source.py" or path.suffix.lower() in SOURCE_SUFFIXES):
            result[str(path.relative_to(root))] = sha256_file(path)
    return result


def load_config(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def scalar_for_measure(bounds: np.ndarray, semantic_scale: dict[str, Any]) -> float:
    extent = bounds[1] - bounds[0]
    measure = semantic_scale["measure"]
    if measure == "height":
        source_value = extent[2]
    elif measure in {"length", "diameter"}:
        source_value = max(extent[0], extent[1])
    else:
        raise ValueError(f"unsupported scale measure: {measure}")
    if source_value <= 0:
        raise ValueError("zero source extent for semantic scale")
    return float(semantic_scale["target_m"]) * 1000.0 / float(source_value)


def load_print_meshes(case_dir: Path, case_cfg: dict[str, Any], profile: dict[str, Any]) -> tuple[list[TaggedMesh], dict[str, Any]]:
    parsed = FINAL.parse_urdf(case_dir / "scene.urdf")
    states = FINAL.state_values(parsed)
    geometries = FINAL.collision_geometries(parsed, states["initial"])
    raw = [(geometry.name, geometry.transformed_mesh()) for geometry in geometries]
    all_vertices = np.vstack([mesh.vertices for _, mesh in raw])
    raw_bounds = np.vstack((all_vertices.min(axis=0), all_vertices.max(axis=0)))
    semantic_scalar = scalar_for_measure(raw_bounds, case_cfg["semantic_scale"])
    semantic_extent = (raw_bounds[1] - raw_bounds[0]) * semantic_scalar
    print_scalar = min(
        semantic_scalar,
        float(profile["max_print_extent_mm"]) / float(np.max(raw_bounds[1] - raw_bounds[0])),
    )
    scaled_bounds = raw_bounds * print_scalar
    margin = float(profile["bed_margin_mm"])
    translation = np.array([margin - scaled_bounds[0, 0], margin - scaled_bounds[0, 1], -scaled_bounds[0, 2]])
    tagged: list[TaggedMesh] = []
    face_offset = 0
    for name, source in raw:
        mesh = source.copy()
        mesh.apply_scale(print_scalar)
        mesh.apply_translation(translation)
        mesh.remove_unreferenced_vertices()
        tagged.append(TaggedMesh(name, mesh, face_offset, face_offset + len(mesh.faces)))
        face_offset += len(mesh.faces)
    combined = trimesh.util.concatenate([row.mesh for row in tagged])
    return tagged, {
        "robot_name": parsed.robot_name,
        "joint_state": "initial",
        "raw_bounds": raw_bounds.tolist(),
        "semantic_scale_factor_mm_per_source_unit": semantic_scalar,
        "semantic_extent_mm": semantic_extent.tolist(),
        "print_scale_factor_mm_per_source_unit": print_scalar,
        "print_extent_mm": (combined.bounds[1] - combined.bounds[0]).tolist(),
        "print_translation_mm": translation.tolist(),
        "downscaled_from_semantic_size": bool(print_scalar < semantic_scalar - 1e-12),
    }


def combined_mesh(tagged: list[TaggedMesh]) -> trimesh.Trimesh:
    return trimesh.util.concatenate([row.mesh for row in tagged])


def mesh_quality(tagged: list[TaggedMesh]) -> dict[str, Any]:
    rows = []
    totals = {"faces": 0, "vertices": 0, "watertight_parts": 0, "non_watertight_parts": 0,
              "winding_inconsistent_parts": 0, "degenerate_faces": 0, "components": 0}
    for row in tagged:
        mesh = row.mesh
        areas = np.asarray(mesh.area_faces)
        diagonal = float(np.linalg.norm(mesh.extents))
        epsilon = max(1e-12, diagonal * diagonal * 1e-14)
        degenerate = int(np.count_nonzero(areas <= epsilon))
        try:
            components = len(mesh.split(only_watertight=False, engine=None))
        except Exception:
            components = 1
        item = {"name": row.name, "faces": int(len(mesh.faces)), "vertices": int(len(mesh.vertices)),
                "watertight": bool(mesh.is_watertight), "winding_consistent": bool(mesh.is_winding_consistent),
                "degenerate_faces": degenerate, "components": components}
        rows.append(item)
        totals["faces"] += item["faces"]
        totals["vertices"] += item["vertices"]
        totals["watertight_parts" if item["watertight"] else "non_watertight_parts"] += 1
        totals["winding_inconsistent_parts"] += int(not item["winding_consistent"])
        totals["degenerate_faces"] += degenerate
        totals["components"] += components
    return {"summary": totals, "parts": rows}


def overhang_mask(mesh: trimesh.Trimesh, threshold_from_horizontal_deg: float,
                  layer_height_mm: float) -> np.ndarray:
    # A downward horizontal face has 0 degrees from horizontal; vertical is 90.
    limit = -math.cos(math.radians(threshold_from_horizontal_deg))
    mask = np.asarray(mesh.face_normals[:, 2] <= limit)
    centers = np.asarray(mesh.triangles_center)
    mask &= centers[:, 2] > layer_height_mm + 1e-8
    return mask


def face_selector(mesh: trimesh.Trimesh, selector: str) -> np.ndarray:
    normals = np.asarray(mesh.face_normals)
    centers = np.asarray(mesh.triangles_center)
    zmin, zmax = float(mesh.bounds[0, 2]), float(mesh.bounds[1, 2])
    upper = centers[:, 2] >= zmin + 0.45 * max(zmax - zmin, 1e-12)
    if selector == "all":
        return np.ones(len(mesh.faces), dtype=bool)
    if selector in {"top", "upward"}:
        return normals[:, 2] >= 0.7
    if selector == "bottom":
        return normals[:, 2] <= -0.7
    if selector == "front":
        return normals[:, 1] <= -0.7
    if selector == "side":
        return np.abs(normals[:, 2]) <= 0.35
    if selector == "upper_side":
        return upper & (np.abs(normals[:, 2]) <= 0.7)
    if selector == "upper_or_front":
        return (upper & (normals[:, 2] >= 0.7)) | (normals[:, 1] <= -0.7)
    raise ValueError(f"unknown face selector: {selector}")


def critical_mask(tagged: list[TaggedMesh], rules: list[dict[str, Any]]) -> tuple[np.ndarray, list[dict[str, Any]]]:
    mask = np.zeros(sum(len(row.mesh.faces) for row in tagged), dtype=bool)
    evidence: list[dict[str, Any]] = []
    for rule in rules:
        pattern = re.compile(rule["collision_regex"], re.IGNORECASE)
        matched_names = []
        face_count = 0
        area = 0.0
        for row in tagged:
            if not pattern.search(row.name):
                continue
            local = face_selector(row.mesh, rule["face"])
            mask[row.face_start:row.face_stop] |= local
            matched_names.append(row.name)
            face_count += int(np.count_nonzero(local))
            area += float(np.sum(row.mesh.area_faces[local]))
        evidence.append({"label": rule["label"], "collision_regex": rule["collision_regex"],
                         "face": rule["face"], "matched_collisions": matched_names,
                         "face_count": face_count, "area_mm2": area})
    return mask, evidence


def mask_regions(tagged: list[TaggedMesh], mask: np.ndarray, areas: np.ndarray) -> list[dict[str, Any]]:
    regions = []
    for row in tagged:
        local = np.flatnonzero(mask[row.face_start:row.face_stop])
        if not len(local):
            continue
        global_ids = local + row.face_start
        centers = np.asarray(row.mesh.triangles_center)[local]
        weights = np.asarray(areas)[global_ids]
        if float(weights.sum()) > 0:
            centroid = np.average(centers, axis=0, weights=weights)
        else:
            centroid = centers.mean(axis=0)
        regions.append({"collision": row.name, "global_face_ids": global_ids.tolist(),
                        "local_face_ids": local.tolist(), "face_count": int(len(local)),
                        "area_mm2": float(weights.sum()), "centroid_mm": centroid.tolist(),
                        "bounds_mm": [centers.min(axis=0).tolist(), centers.max(axis=0).tolist()]})
    return regions


def parse_gcode(path: Path) -> dict[str, Any]:
    x = y = z = e = 0.0
    absolute_e = True
    role = ""
    segments: list[dict[str, Any]] = []
    type_counts: dict[str, int] = {}
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for raw in handle:
            line = raw.strip()
            if line.startswith(";TYPE:"):
                role = line[6:].strip()
                type_counts[role] = type_counts.get(role, 0) + 1
                continue
            if line.startswith(";Z:"):
                try:
                    z = float(line[3:])
                except ValueError:
                    pass
                continue
            command = line.split(";", 1)[0].strip()
            if command == "M82":
                absolute_e = True
                continue
            if command == "M83":
                absolute_e = False
                continue
            if command.startswith("G92"):
                values = {key: float(value) for key, value in GCODE_NUMBER.findall(command)}
                if "E" in values:
                    e = values["E"]
                continue
            if not (command.startswith("G0 ") or command.startswith("G1 ")):
                continue
            values = {key: float(value) for key, value in GCODE_NUMBER.findall(command)}
            nx, ny, nz = values.get("X", x), values.get("Y", y), values.get("Z", z)
            if "E" in values:
                extrusion = values["E"] - e if absolute_e else values["E"]
                if extrusion > 1e-9 and math.hypot(nx - x, ny - y) > 1e-9:
                    segments.append({"role": role, "z": nz, "start": [x, y], "end": [nx, ny],
                                     "length_mm": math.hypot(nx - x, ny - y), "extrusion": extrusion})
                e = values["E"] if absolute_e else e + values["E"]
            x, y, z = nx, ny, nz
    role_lengths: dict[str, float] = {}
    for segment in segments:
        role_lengths[segment["role"]] = role_lengths.get(segment["role"], 0.0) + segment["length_mm"]
    return {"segments": segments, "role_lengths_mm": role_lengths, "type_layer_counts": type_counts}


def role_segments(parsed: dict[str, Any], needle: str, exclude: str | None = None) -> list[dict[str, Any]]:
    result = []
    for row in parsed["segments"]:
        name = row["role"].lower()
        if needle.lower() in name and (exclude is None or exclude.lower() not in name):
            result.append(row)
    return result


def interface_polygons(segments: list[dict[str, Any]], extrusion_width_mm: float) -> dict[float, Any]:
    by_z: dict[float, list[Any]] = {}
    for row in segments:
        line = LineString([row["start"], row["end"]])
        by_z.setdefault(round(float(row["z"]), 6), []).append(
            line.buffer(extrusion_width_mm / 2.0, cap_style=2, join_style=2)
        )
    return {z: unary_union(polygons) for z, polygons in by_z.items()}


def _plane_z(triangle: np.ndarray, normal: np.ndarray, x: float, y: float) -> float | None:
    if abs(float(normal[2])) < 1e-12:
        return None
    point = triangle[0]
    return float(point[2] - (normal[0] * (x - point[0]) + normal[1] * (y - point[1])) / normal[2])


def map_nominal_contact(mesh: trimesh.Trimesh, candidate_mask: np.ndarray,
                        layers: dict[float, Any], layer_height_mm: float,
                        contact_distance_mm: float) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    contact = np.zeros(len(mesh.faces), dtype=bool)
    areas = np.zeros(len(mesh.faces), dtype=float)
    z_values = sorted(layers)
    window = contact_distance_mm + 1.5 * layer_height_mm + 1e-6
    for index in np.flatnonzero(candidate_mask):
        triangle = np.asarray(mesh.triangles[index])
        polygon = Polygon(triangle[:, :2])
        if not polygon.is_valid or polygon.area <= 1e-12:
            continue
        low = float(np.min(triangle[:, 2])) - window
        high = float(np.max(triangle[:, 2])) + 1e-6
        start = bisect.bisect_left(z_values, low)
        stop = bisect.bisect_right(z_values, high)
        projected_area = 0.0
        for layer_z in z_values[start:stop]:
            interface = layers[layer_z]
            if not polygon.intersects(interface):
                continue
            intersection = polygon.intersection(interface)
            if intersection.is_empty or intersection.area <= 1e-12:
                continue
            probe = intersection.representative_point()
            model_z = _plane_z(triangle, mesh.face_normals[index], probe.x, probe.y)
            if model_z is None:
                continue
            gap = model_z - layer_z
            if -1e-6 <= gap <= window:
                projected_area += float(intersection.area)
        if projected_area > 0:
            contact[index] = True
            areas[index] = min(float(mesh.area_faces[index]), projected_area / max(-float(mesh.face_normals[index, 2]), 1e-9))
    return contact, areas, {"definition": "support-interface extrusion footprint below a downward model face within top contact gap plus 1.5 layers",
                            "z_window_mm": window, "interface_layer_count": len(layers)}


def map_nominal_bottom_contact(mesh: trimesh.Trimesh, support_layers: dict[float, Any],
                               layer_height_mm: float, contact_distance_mm: float) -> tuple[np.ndarray, np.ndarray, dict[str, Any]]:
    """Map support paths that start above an upward-facing model surface."""
    contact = np.zeros(len(mesh.faces), dtype=bool)
    areas = np.zeros(len(mesh.faces), dtype=float)
    z_values = sorted(support_layers)
    window = contact_distance_mm + 1.5 * layer_height_mm + 1e-6
    upward = np.asarray(mesh.face_normals[:, 2] >= 0.7)
    for index in np.flatnonzero(upward):
        triangle = np.asarray(mesh.triangles[index])
        polygon = Polygon(triangle[:, :2])
        if not polygon.is_valid or polygon.area <= 1e-12:
            continue
        low = float(np.min(triangle[:, 2])) - 1e-6
        high = float(np.max(triangle[:, 2])) + window
        start = bisect.bisect_left(z_values, low)
        stop = bisect.bisect_right(z_values, high)
        projected_area = 0.0
        for layer_z in z_values[start:stop]:
            support = support_layers[layer_z]
            if not polygon.intersects(support):
                continue
            intersection = polygon.intersection(support)
            if intersection.is_empty or intersection.area <= 1e-12:
                continue
            probe = intersection.representative_point()
            model_z = _plane_z(triangle, mesh.face_normals[index], probe.x, probe.y)
            if model_z is None:
                continue
            gap = layer_z - model_z
            if -1e-6 <= gap <= window:
                projected_area += float(intersection.area)
        if projected_area > 0:
            contact[index] = True
            areas[index] = min(float(mesh.area_faces[index]), projected_area / max(float(mesh.face_normals[index, 2]), 1e-9))
    return contact, areas, {"definition": "support extrusion footprint starting above an upward model face within bottom contact gap plus 1.5 layers",
                            "z_window_mm": window, "support_layer_count": len(support_layers)}


def export_overlay(mesh: trimesh.Trimesh, path: Path, overhang: np.ndarray,
                   critical: np.ndarray, contact: np.ndarray) -> None:
    colors = np.tile(np.array([155, 155, 155, 255], dtype=np.uint8), (len(mesh.faces), 1))
    colors[overhang] = [244, 159, 54, 255]
    colors[critical] = [55, 126, 184, 255]
    colors[contact] = [220, 45, 45, 255]
    colors[critical & contact] = [180, 40, 180, 255]
    result = mesh.copy()
    # Textured GLB inputs retain TextureVisuals when copied. Assigning a
    # face_colors attribute to that object does not serialize PLY colors, so
    # explicitly replace it with ColorVisuals before export.
    result.visual = trimesh.visual.ColorVisuals(mesh=result, face_colors=colors)
    result.export(path)


def slicer_environment(slicer: Path) -> dict[str, str]:
    env = dict(os.environ)
    root = slicer.parents[2]
    libraries = [root / "usr/lib/x86_64-linux-gnu", root / "lib/x86_64-linux-gnu"]
    existing = env.get("LD_LIBRARY_PATH")
    env["LD_LIBRARY_PATH"] = ":".join(str(path) for path in libraries) + ((":" + existing) if existing else "")
    return env


def run_slicer(slicer: Path, profile_path: Path, stl_path: Path, gcode_path: Path,
               timeout_seconds: int) -> dict[str, Any]:
    command = [str(slicer), "--load", str(profile_path), "--dont-arrange", "--export-gcode",
               "--output", str(gcode_path), str(stl_path)]
    completed = subprocess.run(command, text=True, capture_output=True,
                               env=slicer_environment(slicer), timeout=timeout_seconds)
    (gcode_path.parent / "slicer.stdout.log").write_text(completed.stdout, encoding="utf-8")
    (gcode_path.parent / "slicer.stderr.log").write_text(completed.stderr, encoding="utf-8")
    warnings = [line.strip() for line in (completed.stdout + "\n" + completed.stderr).splitlines()
                if "warn" in line.lower() or "repair" in line.lower() or "error" in line.lower()]
    return {"command": command, "returncode": completed.returncode, "warnings": warnings,
            "gcode_exists": gcode_path.is_file(), "gcode_bytes": gcode_path.stat().st_size if gcode_path.is_file() else 0}


def slicer_identity(slicer: Path) -> str:
    completed = subprocess.run([str(slicer), "--help"], text=True, capture_output=True,
                               env=slicer_environment(slicer), timeout=30)
    lines = (completed.stdout + "\n" + completed.stderr).splitlines()
    return next((line.strip() for line in lines if line.startswith("PrusaSlicer-")), "unknown")


def analyze_mesh(mesh: trimesh.Trimesh, tagged: list[TaggedMesh], case_cfg: dict[str, Any],
                 profile: dict[str, Any], gcode: dict[str, Any]) -> dict[str, Any]:
    overhang = overhang_mask(mesh, float(profile["overhang_threshold_from_horizontal_deg"]),
                             float(profile["layer_height_mm"]))
    critical, critical_evidence = critical_mask(tagged, case_cfg.get("critical_surfaces", []))
    interfaces = role_segments(gcode, "support material interface")
    support_base = role_segments(gcode, "support material", exclude="interface")
    bridges = role_segments(gcode, "bridge infill")
    layers = interface_polygons(interfaces, float(profile["extrusion_width_mm"]))
    top_contact, top_areas, top_method = map_nominal_contact(
        mesh, overhang, layers, float(profile["layer_height_mm"]),
        float(profile["top_contact_z_distance_mm"]),
    )
    all_support_layers = interface_polygons(support_base + interfaces, float(profile["extrusion_width_mm"]))
    bottom_contact, bottom_areas, bottom_method = map_nominal_bottom_contact(
        mesh, all_support_layers, float(profile["layer_height_mm"]),
        float(profile["top_contact_z_distance_mm"]),
    )
    contact = top_contact | bottom_contact
    contact_areas = np.maximum(top_areas, bottom_areas)
    support_length = sum(row["length_mm"] for row in support_base) + sum(row["length_mm"] for row in interfaces)
    bridge_length = sum(row["length_mm"] for row in bridges)
    overlap_area = float(np.sum(contact_areas[critical]))
    critical_area = float(np.sum(mesh.area_faces[critical]))
    support_required = support_length > 1e-6
    reliable = bool(case_cfg.get("critical_reliable", False))
    epsilon = float(profile["critical_overlap_epsilon_mm2"])
    if not support_required:
        critical_verdict = "PASS"
        critical_reason = "slicer generated no support material"
    elif not reliable:
        critical_verdict = "INDETERMINATE"
        critical_reason = "single merged collision mesh lacks reliable semantic face provenance"
    elif overlap_area > epsilon:
        critical_verdict = "VIOLATION"
        critical_reason = f"nominal support contact overlap exceeds {epsilon} mm^2"
    else:
        critical_verdict = "PASS"
        critical_reason = "no effective nominal contact overlap with configured critical surfaces"
    bridge_spans = [row["length_mm"] for row in bridges]
    return {
        "overhang": {"face_count": int(np.count_nonzero(overhang)),
                     "area_mm2": float(np.sum(mesh.area_faces[overhang])),
                     "threshold_from_horizontal_deg": profile["overhang_threshold_from_horizontal_deg"]},
        "unsupported_region": {"definition": "downward model faces actually carried by slicer support interface",
                               "face_count": int(np.count_nonzero(top_contact)),
                               "area_mm2": float(np.sum(top_areas)),
                               "regions": mask_regions(tagged, top_contact, top_areas)},
        "slicer_support": {"required": support_required, "base_path_length_mm": sum(row["length_mm"] for row in support_base),
                           "interface_path_length_mm": sum(row["length_mm"] for row in interfaces),
                           "interface_layers": len(layers)},
        "bridge": {"detected": bool(bridges), "path_length_mm": bridge_length,
                   "path_area_proxy_mm2": bridge_length * float(profile["extrusion_width_mm"]),
                   "max_single_extrusion_span_mm": max(bridge_spans, default=0.0)},
        "nominal_contact": {"definition": "top and bottom nominal support contact; no zero-distance mesh intersection is claimed",
                            "top": {**top_method, "face_count": int(np.count_nonzero(top_contact)),
                                    "area_mm2": float(np.sum(top_areas))},
                            "bottom": {**bottom_method, "face_count": int(np.count_nonzero(bottom_contact)),
                                       "area_mm2": float(np.sum(bottom_areas))},
                            "face_count": int(np.count_nonzero(contact)), "area_mm2": float(np.sum(contact_areas)),
                            "regions": mask_regions(tagged, contact, contact_areas)},
        "critical_surfaces": {"reliable": reliable, "configured_face_count": int(np.count_nonzero(critical)),
                              "configured_area_mm2": critical_area, "rules": critical_evidence,
                              "contact_overlap_area_mm2": overlap_area,
                              "contact_overlap_fraction": (overlap_area / critical_area if critical_area > 0 else None),
                              "overlap_regions": mask_regions(tagged, critical & contact, contact_areas),
                              "verdict": critical_verdict, "reason": critical_reason},
        "masks": {"overhang": overhang, "critical": critical, "contact": contact},
    }


def analyze_case(case_id: str, case_dir: Path, output_root: Path, config: dict[str, Any],
                 slicer: Path, profile_path: Path, timeout: int) -> dict[str, Any]:
    case_output = output_root / "cases" / case_id
    case_output.mkdir(parents=True, exist_ok=True)
    case_cfg = config["cases"][case_id]
    try:
        tagged, scale = load_print_meshes(case_dir, case_cfg, config["profile"])
        mesh = combined_mesh(tagged)
        quality = mesh_quality(tagged)
        stl = case_output / "print_mesh.stl"
        gcode_path = case_output / "support_enabled.gcode"
        mesh.export(stl)
        slicer_run = run_slicer(slicer, profile_path, stl, gcode_path, timeout)
        if slicer_run["returncode"] != 0 or not slicer_run["gcode_exists"]:
            return {"case_id": case_id, "status": "SLICER_FAILED", "scale": scale,
                    "mesh_quality": quality, "slicer": slicer_run}
        gcode = parse_gcode(gcode_path)
        analysis = analyze_mesh(mesh, tagged, case_cfg, config["profile"], gcode)
        masks = analysis.pop("masks")
        export_overlay(mesh, case_output / "surface_classes.ply", masks["overhang"],
                       masks["critical"], masks["contact"])
        return {"case_id": case_id, "status": "ANALYZED", "scale": scale,
                "mesh_quality": quality, "slicer": slicer_run,
                "gcode_roles": {"role_lengths_mm": gcode["role_lengths_mm"],
                                "type_layer_counts": gcode["type_layer_counts"]}, **analysis}
    except subprocess.TimeoutExpired as exc:
        return {"case_id": case_id, "status": "SLICER_TIMEOUT", "error": str(exc)}
    except Exception as exc:
        return {"case_id": case_id, "status": "ANALYSIS_FAILED",
                "error": f"{type(exc).__name__}: {exc}"}


def fixture_mesh(kind: str) -> trimesh.Trimesh:
    if kind == "self_supported":
        mesh = trimesh.creation.cone(radius=12, height=24, sections=64)
        mesh.apply_translation([25, 25, 0])
        return mesh
    if kind == "cantilever":
        pillar = trimesh.creation.box([10, 10, 24]); pillar.apply_translation([25, 25, 12])
        arm = trimesh.creation.box([36, 10, 4]); arm.apply_translation([38, 25, 26])
        return trimesh.util.concatenate([pillar, arm])
    if kind == "bridge":
        left = trimesh.creation.box([8, 10, 24]); left.apply_translation([18, 25, 12])
        right = trimesh.creation.box([8, 10, 24]); right.apply_translation([62, 25, 12])
        beam = trimesh.creation.box([52, 10, 4]); beam.apply_translation([40, 25, 26])
        return trimesh.util.concatenate([left, right, beam])
    raise ValueError(kind)


def run_benchmarks(output: Path, slicer: Path, profile_path: Path, timeout: int,
                   profile: dict[str, Any]) -> dict[str, Any]:
    root = output / "benchmarks"
    root.mkdir(parents=True, exist_ok=True)
    rows = []
    for kind in ("self_supported", "cantilever", "bridge"):
        fixture = fixture_mesh(kind)
        stl, gcode_path = root / f"{kind}.stl", root / f"{kind}.gcode"
        fixture.export(stl)
        run = run_slicer(slicer, profile_path, stl, gcode_path, timeout)
        if run["returncode"] != 0 or not run["gcode_exists"]:
            rows.append({"fixture": kind, "status": "SLICER_FAILED", "slicer": run})
            continue
        parsed = parse_gcode(gcode_path)
        support = role_segments(parsed, "support material")
        bridges = role_segments(parsed, "bridge infill")
        rows.append({"fixture": kind, "status": "SLICED", "support_detected": bool(support),
                     "bridge_detected": bool(bridges),
                     "support_path_length_mm": sum(row["length_mm"] for row in support),
                     "bridge_path_length_mm": sum(row["length_mm"] for row in bridges)})
    by_name = {row["fixture"]: row for row in rows}
    passed = (by_name.get("self_supported", {}).get("status") == "SLICED"
              and not by_name["self_supported"].get("support_detected", True)
              and by_name.get("cantilever", {}).get("support_detected", False)
              and by_name.get("bridge", {}).get("bridge_detected", False))
    result = {"status": "PASSED" if passed else "FAILED", "fixtures": rows,
              "acceptance": {"self_supported_has_no_support": True,
                             "cantilever_has_support": True, "bridge_role_detected": True}}
    (root / "benchmarks.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result


def report_markdown(payload: dict[str, Any]) -> str:
    cases = payload["cases"]
    analyzed = [row for row in cases if row["status"] == "ANALYZED"]
    support_count = sum(row["slicer_support"]["required"] for row in analyzed)
    verdicts = {name: sum(row["critical_surfaces"]["verdict"] == name for row in analyzed)
                for name in ("PASS", "VIOLATION", "INDETERMINATE")}
    non_watertight = sum(row["mesh_quality"]["summary"]["non_watertight_parts"] > 0 for row in analyzed)
    lines = ["# 04 Support Requirement & Critical-Surface-Aware Support", "",
             "## Material Passport", "", "- Material ID: `adsl-physics-04-support-20260904`",
             "- Type: experiment execution and analysis report",
             "- Origin Skill: `academic-research-suite/experiment-agent`", "- Origin Mode: `run`",
             "- Verification Status: ANALYZED", f"- Generated (UTC): {payload['generated_at_utc']}", "",
             "## 结论摘要", "",
             f"- 合成基准：{payload['benchmarks']['status']}；当前案例成功分析 {len(analyzed)}/{len(cases)}。",
             f"- PrusaSlicer 实际生成 support：{support_count}/{len(analyzed)}；至少一个 non-watertight collision part：{non_watertight}/{len(analyzed)}。",
             f"- 关键表面结果：PASS={verdicts['PASS']}，VIOLATION={verdicts['VIOLATION']}，INDETERMINATE={verdicts['INDETERMINATE']}。",
             "- `nominal contact` 同时覆盖 interface 承托的模型下表面，以及 support 从已有模型顶面起长的底部接触；它不是两个网格零距离相交。",
             "- 单一合并 GLB 没有部件/三角面语义来源；有 support 时其关键表面结论 fail closed 为 INDETERMINATE。", "",
             "## 当前案例", "", "| Case | Status | support | overhang mm² | bridge mm | contact mm² | critical overlap mm² | critical verdict | mesh warning |",
             "|---|---|---|---:|---:|---:|---:|---|---|"]
    for row in cases:
        if row["status"] != "ANALYZED":
            lines.append(f"| {row['case_id']} | {row['status']} | — | — | — | — | — | — | `{row.get('error', '')}` |")
            continue
        quality = row["mesh_quality"]["summary"]
        warning = "none" if quality["non_watertight_parts"] == 0 and quality["degenerate_faces"] == 0 else f"open_parts={quality['non_watertight_parts']}; degenerate={quality['degenerate_faces']}"
        critical = row["critical_surfaces"]
        lines.append(f"| {row['case_id']} | ANALYZED | {row['slicer_support']['required']} | {row['overhang']['area_mm2']:.3f} | {row['bridge']['path_length_mm']:.3f} | {row['nominal_contact']['area_mm2']:.3f} | {critical['contact_overlap_area_mm2']:.3f} | {critical['verdict']} | {warning} |")
    lines.extend(["", "## 接触区域定位", ""])
    located = False
    for row in cases:
        if row.get("status") != "ANALYZED":
            continue
        critical = row["critical_surfaces"]
        for region in critical.get("overlap_regions", []):
            located = True
            centroid = [round(value, 3) for value in region["centroid_mm"]]
            lines.append("- {} [{}]: `{}`，{} faces，{:.3f} mm²，centroid={} mm。".format(row["case_id"], critical["verdict"], region["collision"], region["face_count"], region["area_mm2"], centroid))
    if not located:
        lines.append("- 没有检测到关键表面接触重叠。")
    lines.extend(["", "完整 global/local face IDs 与 bounds 位于 results.json；surface_classes.ply 提供逐三角面颜色证据。", ""])

    lines.extend(["", "## 方法与判据", "",
                  "- authored URDF Z-up、initial joint state；第 03 项语义实物尺度只作元数据，实际 FDM 网格等比限制为最长边 180 mm。",
                  "- 0.4 mm nozzle、0.2 mm layer、PLA、45°（从水平面计）overhang threshold、everywhere rectilinear support、3 层 interface、0.2 mm top Z gap。",
                  "- 几何 overhang 是高于首层且法向达到阈值的向下三角面；bridge 与 support 结论来自实际 G-code feature role。",
                  "- 关键面用 collision regex + face selector；有效名义接触重叠 >0.01 mm² 判 VIOLATION。面积和三角面覆盖图保留为证据。",
                  "- 橙=overhang、蓝=critical、红=contact、紫=critical/contact overlap。", "",
                  "## 证据边界", "",
                  "- 切片器会隐式 repair STL；原始模型的 open/non-manifold/断开体问题单独记录，不能因切片成功而视为模型正确。",
                  "- bridge 成功与否依赖材料、冷却、速度和校准；本报告只证明切片器将路径标为 bridge，不证明实物不会下垂。",
                  "- support 接触痕迹仍受温度、Z gap、界面密度和拆除操作影响；本报告不是打印认证。", "",
                  "## 复现", "", f"- Input: `{payload['input_root']}`", f"- Output: `{payload['output_dir']}`",
                  f"- Slicer: `{payload['environment']['slicer_identity']}`", f"- Profile: `{payload['profile_path']}`",
                  f"- Command: `{payload['command']}`", "- 全部 CPU 执行，无需 GPU；原始模型哈希前后必须一致。", ""])
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], output: Path) -> None:
    serializable = dict(payload)
    (output / "results.json").write_text(json.dumps(serializable, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    fields = ["case_id", "status", "support_required", "overhang_area_mm2", "bridge_path_length_mm",
              "contact_area_mm2", "critical_overlap_area_mm2", "critical_verdict"]
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields); writer.writeheader()
        for row in payload["cases"]:
            writer.writerow({"case_id": row["case_id"], "status": row["status"],
                             "support_required": row.get("slicer_support", {}).get("required"),
                             "overhang_area_mm2": row.get("overhang", {}).get("area_mm2"),
                             "bridge_path_length_mm": row.get("bridge", {}).get("path_length_mm"),
                             "contact_area_mm2": row.get("nominal_contact", {}).get("area_mm2"),
                             "critical_overlap_area_mm2": row.get("critical_surfaces", {}).get("contact_overlap_area_mm2"),
                             "critical_verdict": row.get("critical_surfaces", {}).get("verdict")})
    (output / "support_requirement_critical_surfaces.md").write_text(report_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=Path("/jiigan-hp/lms/aDSL/experiment/audit_20260830"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=HERE / "case_config.json")
    parser.add_argument("--profile", type=Path, default=HERE / "fff_profile.ini")
    parser.add_argument("--slicer", type=Path, default=Path(os.environ.get("ADSL_PRUSASLICER_BIN", "/vepfs_default/chanxueyan/lhp/lms/tools/prusaslicer/2.4.0/sysroot/usr/bin/prusa-slicer")))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--slicer-timeout", type=int, default=900)
    parser.add_argument("--benchmark-only", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    output = args.output_dir.resolve(); output.mkdir(parents=True, exist_ok=True)
    if not args.slicer.is_file():
        raise FileNotFoundError(args.slicer)
    benchmarks = run_benchmarks(output, args.slicer.resolve(), args.profile.resolve(),
                                args.slicer_timeout, config["profile"])
    if args.benchmark_only:
        return 0 if benchmarks["status"] == "PASSED" else 2
    before = source_hashes(args.input_root.resolve())
    selected = set(args.case or config["cases"])
    cases = []
    for case_id in sorted(selected):
        if case_id not in config["cases"]:
            raise KeyError(f"no config for case {case_id}")
        cases.append(analyze_case(case_id, args.input_root.resolve() / case_id, output,
                                  config, args.slicer.resolve(), args.profile.resolve(), args.slicer_timeout))
    after = source_hashes(args.input_root.resolve())
    payload = {"generated_at_utc": utc_now(), "input_root": str(args.input_root.resolve()),
               "output_dir": str(output), "profile_path": str(args.profile.resolve()),
               "config_path": str(args.config.resolve()), "command": " ".join(sys.argv),
               "environment": {"slicer": str(args.slicer.resolve()),
                               "slicer_identity": slicer_identity(args.slicer.resolve()),
                               "python": sys.version.split()[0], "trimesh": trimesh.__version__},
               "benchmarks": benchmarks, "profile": config["profile"], "cases": cases,
               "source_integrity": {"before": before, "after": after, "unchanged": before == after,
                                    "file_count": len(before)}}
    write_outputs(payload, output)
    success = benchmarks["status"] == "PASSED" and all(row["status"] == "ANALYZED" for row in cases) and before == after
    return 0 if success else 2


if __name__ == "__main__":
    raise SystemExit(main())
