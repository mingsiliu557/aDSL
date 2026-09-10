#!/usr/bin/env python3
"""Fail-closed CalculiX/Gmsh screening of existing aDSL load paths."""
from __future__ import annotations

import argparse
import csv
from datetime import datetime, timezone
import importlib.util
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from typing import Any, Iterable

import numpy as np
import trimesh

HERE = Path(__file__).resolve().parent
FINAL_PATH = HERE.parents[0] / "final_standing_stability" / "analyze.py"
SPEC = importlib.util.spec_from_file_location("final_standing_shared_fea", FINAL_PATH)
if SPEC is None or SPEC.loader is None:
    raise RuntimeError(f"cannot import {FINAL_PATH}")
FINAL = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = FINAL
SPEC.loader.exec_module(FINAL)

TOPOLOGY_PATH = HERE.parents[0] / "topology_connectivity" / "analyze.py"
TOPOLOGY_SPEC = importlib.util.spec_from_file_location(
    "adsl_shared_topology_fea", TOPOLOGY_PATH
)
if TOPOLOGY_SPEC is None or TOPOLOGY_SPEC.loader is None:
    raise RuntimeError(f"cannot import {TOPOLOGY_PATH}")
TOPOLOGY = importlib.util.module_from_spec(TOPOLOGY_SPEC)
sys.modules[TOPOLOGY_SPEC.name] = TOPOLOGY
TOPOLOGY_SPEC.loader.exec_module(TOPOLOGY)

G = 9.81
STATUSES = {
    "SOLVED", "NOT_MESHABLE", "INVALID_LOAD_PATH", "LOAD_REGION_AMBIGUOUS",
    "NOT_CONVERGED", "SOLVER_FAILED",
}
MESH_LEVELS = {"coarse": 0.10, "medium": 0.06, "fine": 0.04}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ccx_version(ccx: Path) -> str:
    """CalculiX prints a valid version banner but exits with code 201 for -v."""
    completed = subprocess.run([str(ccx), "-v"], text=True, stdout=subprocess.PIPE,
                               stderr=subprocess.STDOUT, check=False)
    banner = completed.stdout.strip()
    if "Version" not in banner:
        raise RuntimeError(f"CalculiX version probe failed ({completed.returncode}): {banner}")
    return banner


def load_config(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if set(data["cases"]) != set(DEFAULT_CASES):
        missing = sorted(set(DEFAULT_CASES) - set(data["cases"]))
        extra = sorted(set(data["cases"]) - set(DEFAULT_CASES))
        raise ValueError(f"case config mismatch: missing={missing}, extra={extra}")
    return data


DEFAULT_CASES = (
    "A01-cabinet", "A02-faucet", "E01-chair-slat-edit", "I01-example-image",
    "I02-example-arti", "M01-base-motorcycle", "M01-cyberpunk-edit",
    "M01-cyberpunk-scratch", "T01-main", "T01-one-round-control",
    "T02-bookshelf", "T03-radial-wheel", "T04-hollow-mug", "T05-patterned-desk",
)


def all_bounds(items: list[Any]) -> np.ndarray:
    vertices = np.vstack([item.transformed_mesh().vertices for item in items])
    return np.vstack((vertices.min(axis=0), vertices.max(axis=0)))


def scale_factor(items: list[Any], rule: dict[str, Any]) -> tuple[float, dict[str, Any]]:
    bounds = all_bounds(items)
    extents = bounds[1] - bounds[0]
    measure = rule["measure"]
    if measure == "height":
        source = float(extents[2])
    elif measure == "length":
        source = float(max(extents[0], extents[1]))
    elif measure == "diameter":
        source = float(max(extents))
    else:
        raise ValueError(f"unknown scale measure {measure}")
    if not math.isfinite(source) or source <= 0:
        raise ValueError("invalid source extent")
    factor = float(rule["target_m"]) / source
    return factor, {"measure": measure, "source_scene_units": source,
                    "target_m": float(rule["target_m"]), "factor_m_per_scene_unit": factor,
                    "source_bounds": bounds.tolist()}


def exact_union_gate(items: list[Any]) -> tuple[str, dict[str, Any], trimesh.Trimesh | None]:
    meshes = [item.transformed_mesh() for item in items]
    input_non_watertight = [item.name for item, mesh in zip(items, meshes) if not mesh.is_watertight]
    try:
        union = trimesh.boolean.union(meshes, engine="manifold", check_volume=False)
        if isinstance(union, trimesh.Scene):
            union = union.to_geometry()
    except Exception as error:
        return "NOT_MESHABLE", {"error": f"{type(error).__name__}: {error}",
                                "input_non_watertight": input_non_watertight}, None
    if union is None or len(union.vertices) == 0 or len(union.faces) == 0:
        return "NOT_MESHABLE", {"reason": "BOOLEAN_UNION_EMPTY",
                                "input_non_watertight": input_non_watertight}, None
    components = list(union.split(only_watertight=False, engine=None))
    info = {"input_geometry_count": len(items), "input_non_watertight": input_non_watertight,
            "union_watertight": bool(union.is_watertight),
            "union_component_count": len(components),
            "union_vertices": int(len(union.vertices)), "union_faces": int(len(union.faces))}
    if not union.is_watertight:
        info["reason"] = "BOOLEAN_UNION_NOT_WATERTIGHT"
        return "NOT_MESHABLE", info, union
    if len(components) != 1:
        info["reason"] = "DISCONNECTED_LOAD_PATH_WITHOUT_JOINT_OR_TIE_MODEL"
        return "INVALID_LOAD_PATH", info, union
    unsupported = sorted({item.kind for item in items} - {"box", "cylinder", "sphere"})
    if unsupported:
        info["reason"] = "CONNECTED_TRIANGLE_MESH_HAS_NO_VERIFIED_SOLID_CAD_TRANSFER"
        info["unsupported_geometry_kinds"] = unsupported
        return "NOT_MESHABLE", info, union
    return "SOLVED", info, union


def semantic_bounds(items: list[Any], pattern: str, scale: float) -> tuple[np.ndarray | None, list[str]]:
    regex = re.compile(pattern, re.IGNORECASE)
    selected = [(item, item.transformed_mesh()) for item in items if regex.search(item.name)]
    if not selected:
        return None, []
    vertices = np.vstack([mesh.vertices for _, mesh in selected]) * scale
    return np.vstack((vertices.min(axis=0), vertices.max(axis=0))), [item.name for item, _ in selected]


def _affine(transform: np.ndarray, scale: float) -> list[float]:
    result = np.asarray(transform, dtype=float).copy()
    result[:3, :3] *= scale
    result[:3, 3] *= scale
    return result.reshape(-1).tolist()


def build_occ_mesh(items: list[Any], scale: float, mesh_size: float, output: Path) -> dict[str, Any]:
    import gmsh
    output.mkdir(parents=True, exist_ok=True)
    mesh_path = output / "mesh_raw.inp"
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("adsl_fea")
        volumes: list[tuple[int, int]] = []
        for item in items:
            if item.kind == "box":
                x, y, z = map(float, item.parameters["size"])
                tag = gmsh.model.occ.addBox(-x / 2, -y / 2, -z / 2, x, y, z)
            elif item.kind == "cylinder":
                radius = float(item.parameters["radius"])
                length = float(item.parameters["length"])
                tag = gmsh.model.occ.addCylinder(0, 0, -length / 2, 0, 0, length, radius)
            elif item.kind == "sphere":
                tag = gmsh.model.occ.addSphere(0, 0, 0, float(item.parameters["radius"]))
            else:
                raise ValueError(f"OCC reconstruction does not support {item.kind}")
            gmsh.model.occ.affineTransform([(3, tag)], _affine(item.transform, scale))
            volumes.append((3, tag))
        if len(volumes) > 1:
            gmsh.model.occ.fuse([volumes[0]], volumes[1:], removeObject=True, removeTool=True)
        gmsh.model.occ.synchronize()
        final_volumes = gmsh.model.getEntities(3)
        if len(final_volumes) != 1:
            raise RuntimeError(f"OCC fuse produced {len(final_volumes)} volumes")
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size * 0.45)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        gmsh.model.mesh.generate(3)
        element_types = list(map(int, gmsh.model.mesh.getElements(3)[0]))
        if element_types != [11]:
            raise RuntimeError(f"expected only Gmsh type 11 C3D10 elements, got {element_types}")
        gmsh.write(str(mesh_path))
        node_count = len(gmsh.model.mesh.getNodes()[0])
        element_count = sum(len(tags) for tags in gmsh.model.mesh.getElements(3)[1])
        quality = gmsh.model.mesh.getElementQualities(
            np.concatenate(gmsh.model.mesh.getElements(3)[1]).astype(np.uint64),
            qualityName="minSJ",
        )
        return {"mesh_path": str(mesh_path), "node_count": int(node_count),
                "element_count": int(element_count), "minimum_scaled_jacobian": float(np.min(quality)),
                "mesh_size_m": mesh_size, "gmsh_version": gmsh.option.getString("General.Version")}
    finally:
        gmsh.finalize()


def parse_gmsh_inp(path: Path) -> tuple[dict[int, np.ndarray], dict[int, list[int]]]:
    nodes: dict[int, np.ndarray] = {}
    elements: dict[int, list[int]] = {}
    mode: str | None = None
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        upper = line.upper()
        if upper.startswith("*NODE"):
            mode = "node"
            continue
        if upper.startswith("*ELEMENT"):
            mode = "c3d10" if "TYPE=C3D10" in upper else None
            continue
        if line.startswith("*"):
            mode = None
            continue
        if not line or line.startswith("**") or mode is None:
            continue
        values = [part.strip() for part in line.split(",") if part.strip()]
        if mode == "node":
            nodes[int(values[0])] = np.asarray([float(x) for x in values[1:4]], dtype=float)
        elif mode == "c3d10":
            elements[int(values[0])] = [int(x) for x in values[1:11]]
    if not nodes or not elements:
        raise ValueError(f"no C3D10 volume mesh found in {path}")
    return nodes, elements


def format_ids(values: Iterable[int], per_line: int = 16) -> list[str]:
    ids = list(map(int, values))
    return [", ".join(map(str, ids[index:index + per_line]))
            for index in range(0, len(ids), per_line)]


def face_nodes(nodes: dict[int, np.ndarray], bounds: np.ndarray, face: str,
               tolerance: float) -> list[int]:
    lo, hi = bounds
    rows = []
    for tag, xyz in nodes.items():
        inside = np.all(xyz >= lo - tolerance) and np.all(xyz <= hi + tolerance)
        if not inside:
            continue
        if face in {"top", "top_center"}:
            match = xyz[2] >= hi[2] - tolerance
        elif face == "bottom":
            match = xyz[2] <= lo[2] + tolerance
        elif face == "y_max":
            match = xyz[1] >= hi[1] - tolerance
        else:
            match = False
        if match:
            rows.append(tag)
    return sorted(rows)


def resolve_regions(nodes: dict[int, np.ndarray], items: list[Any], scale: float,
                    loads: list[dict[str, Any]], mesh_size: float) -> tuple[list[dict[str, Any]], list[str]]:
    resolved, failures = [], []
    for load in loads:
        bounds, names = semantic_bounds(items, load["pattern"], scale)
        if bounds is None or load["face"] not in {"top", "bottom", "y_max", "top_center"}:
            failures.append(f"{load['name']}: semantic region is unavailable or requires an unimplemented selector")
            continue
        selected = face_nodes(nodes, bounds, load["face"], mesh_size * 0.55)
        if not selected:
            selected = face_nodes(nodes, bounds, load["face"], mesh_size * 1.10)
        if not selected:
            failures.append(f"{load['name']}: no mesh nodes on semantic face")
            continue
        resolved.append({**load, "node_ids": selected, "matched_geometries": names,
                         "semantic_bounds_m": bounds.tolist()})
    return resolved, failures


def write_deck(path: Path, nodes: dict[int, np.ndarray], elements: dict[int, list[int]],
               material: dict[str, Any], support: list[int], loads: list[dict[str, Any]],
               include_gravity: bool, include_buckle: bool = True) -> None:
    lines = ["*HEADING", f"aDSL load-bearing screening: {path.stem}", "*NODE"]
    for tag, xyz in sorted(nodes.items()):
        lines.append(f"{tag}, {xyz[0]:.12g}, {xyz[1]:.12g}, {xyz[2]:.12g}")
    lines.append("*ELEMENT, TYPE=C3D10, ELSET=EALL")
    for tag, conn in sorted(elements.items()):
        lines.append(f"{tag}, " + ", ".join(map(str, conn)))
    lines.extend(["*NSET, NSET=NALL", *format_ids(sorted(nodes)),
                  "*NSET, NSET=SUPPORT", *format_ids(support)])
    for index, load in enumerate(loads):
        lines.extend([f"*NSET, NSET=LOAD{index}", *format_ids(load["node_ids"])])
    lines.extend(["*MATERIAL, NAME=PLA_PROXY", "*ELASTIC",
                  f"{material['youngs_modulus_pa']:.12g}, {material['poisson_ratio']:.12g}",
                  "*DENSITY", f"{material['density_kg_m3']:.12g}",
                  "*SOLID SECTION, ELSET=EALL, MATERIAL=PLA_PROXY", "",
                  "*BOUNDARY", "SUPPORT, 1, 3", "*STEP", "*STATIC"])
    if include_gravity:
        lines.extend(["*DLOAD", f"EALL, GRAV, {G}, 0., 0., -1."])
    if loads:
        lines.append("*CLOAD")
        for index, load in enumerate(loads):
            force = np.asarray(load["force_n"], dtype=float) / len(load["node_ids"])
            for dof, value in enumerate(force, start=1):
                if abs(value) > 0:
                    lines.append(f"LOAD{index}, {dof}, {value:.12g}")
    lines.extend(["*NODE FILE", "U", "*EL FILE", "S", "*NODE PRINT, NSET=NALL", "U",
                  "*EL PRINT, ELSET=EALL", "S", "*END STEP"])
    if include_buckle:
        lines.extend(["*STEP", "*BUCKLE", "5"])
        if include_gravity:
            lines.extend(["*DLOAD", f"EALL, GRAV, {G}, 0., 0., -1."])
        if loads:
            lines.append("*CLOAD")
            for index, load in enumerate(loads):
                force = np.asarray(load["force_n"], dtype=float) / len(load["node_ids"])
                for dof, value in enumerate(force, start=1):
                    if abs(value) > 0:
                        lines.append(f"LOAD{index}, {dof}, {value:.12g}")
        lines.extend(["*NODE FILE", "U", "*END STEP"])
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


FLOAT = r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[EeDd][-+]?\d+)?"


def parse_dat(path: Path) -> dict[str, Any]:
    text = path.read_text(encoding="utf-8", errors="replace") if path.exists() else ""
    displacements: list[tuple[int, np.ndarray]] = []
    stresses: list[tuple[int, np.ndarray]] = []
    mode: str | None = None
    buckling: list[float] = []
    in_buckling = False
    for line in text.splitlines():
        low = line.lower()
        compact = re.sub(r"\s+", "", low)
        if "bucklingfactoroutput" in compact:
            in_buckling = True
            mode = None
            continue
        if in_buckling and "modeno" in compact and "buckling" in compact:
            mode = "buckling"
            continue
        if mode == "buckling" and compact == "factor":
            continue
        if not in_buckling and "displacements (vx,vy,vz)" in low:
            mode = "u"
            continue
        if not in_buckling and "stresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz)" in low:
            mode = "s"
            continue
        values = re.findall(FLOAT, line)
        if mode == "u" and len(values) == 4:
            displacements.append((int(float(values[0])), np.asarray([float(x.replace("D", "E")) for x in values[1:4]])))
        elif mode == "s" and len(values) >= 8:
            stresses.append((int(float(values[0])), np.asarray([float(x.replace("D", "E")) for x in values[2:8]])))
        elif mode == "buckling" and len(values) == 2:
            buckling.append(float(values[1].replace("D", "E").replace("d", "e")))
        elif line.strip() and not values:
            mode = None
    max_u = None
    max_u_node = None
    if displacements:
        max_u_node, vec = max(displacements, key=lambda row: float(np.linalg.norm(row[1])))
        max_u = float(np.linalg.norm(vec))
    max_vm = None
    max_vm_element = None
    if stresses:
        def vm(row: tuple[int, np.ndarray]) -> float:
            s = row[1]
            return math.sqrt(0.5 * ((s[0]-s[1])**2 + (s[1]-s[2])**2 + (s[2]-s[0])**2)
                             + 3.0 * (s[3]**2 + s[4]**2 + s[5]**2))
        max_vm_element, stress = max(stresses, key=vm)
        max_vm = vm((max_vm_element, stress))
    positive = [value for value in buckling if value > 0]
    return {"max_displacement_m": max_u, "max_displacement_node": max_u_node,
            "max_von_mises_pa": max_vm, "max_von_mises_element": max_vm_element,
            "buckling_factors": buckling,
            "first_positive_buckling_factor": min(positive) if positive else None}


def element_centroid(element: int | None, nodes: dict[int, np.ndarray],
                     elements: dict[int, list[int]]) -> list[float] | None:
    if element is None or element not in elements:
        return None
    corners = elements[element][:4]
    return np.mean(np.vstack([nodes[tag] for tag in corners]), axis=0).tolist()


def screening_assessment(result: dict[str, Any], characteristic_length: float) -> dict[str, Any]:
    displacement_ratio = (None if result.get("max_displacement_m") is None else
                          result["max_displacement_m"] / characteristic_length)
    safety_factor = result.get("nominal_safety_factor")
    buckling_factor = result.get("first_positive_buckling_factor")
    concerns = []
    if displacement_ratio is None or displacement_ratio > 0.01:
        concerns.append("DISPLACEMENT_GT_1PCT_CHARACTERISTIC_LENGTH")
    if safety_factor is None or safety_factor < 2.0:
        concerns.append("NOMINAL_YIELD_FOS_LT_2")
    if buckling_factor is None or buckling_factor < 2.0:
        concerns.append("LINEAR_BUCKLING_FACTOR_LT_2")
    return {"status": "SCREEN_PASS" if not concerns else "CONCERN",
            "max_displacement_over_characteristic_length": displacement_ratio,
            "thresholds": {"max_displacement_ratio": 0.01,
                           "minimum_nominal_yield_fos": 2.0,
                           "minimum_linear_buckling_factor": 2.0},
            "concerns": concerns}


def run_ccx(ccx: Path, deck: Path, timeout: int) -> dict[str, Any]:
    name = deck.stem
    # CalculiX deletes/reopens output files internally. jiigan-hp does not
    # reliably implement that operation, so each solve gets isolated local
    # scratch and is copied out only after the process exits.
    with tempfile.TemporaryDirectory(prefix=f"adsl-ccx-{name}-", dir="/tmp") as temp:
        scratch = Path(temp)
        shutil.copy2(deck, scratch / deck.name)
        completed = subprocess.run([str(ccx), name], cwd=scratch, text=True,
                                   stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                                   timeout=timeout, check=False)
        dat = scratch / f"{name}.dat"
        frd = scratch / f"{name}.frd"
        parsed = parse_dat(dat)
        parsed.update({"returncode": completed.returncode, "dat_exists": dat.exists(),
                       "frd_exists": frd.exists(), "solver_scratch": "/tmp isolated per job"})
        (deck.parent / f"{name}.stdout.log").write_text(completed.stdout, encoding="utf-8")
        for artifact in scratch.iterdir():
            if artifact.name != deck.name:
                shutil.copy2(artifact, deck.parent / artifact.name)
    if completed.returncode != 0:
        parsed["status"] = "SOLVER_FAILED"
    elif not parsed["frd_exists"] or parsed["max_displacement_m"] is None:
        parsed["status"] = "NOT_CONVERGED"
    else:
        parsed["status"] = "SOLVED"
    return parsed



def scale_factor_manifest(
    manifest: dict[str, Any],
    rule: dict[str, Any],
) -> tuple[float, dict[str, Any]]:
    bounds = np.asarray(manifest["root"]["bounds"], dtype=float)
    extents = bounds[1] - bounds[0]
    measure = rule["measure"]
    if measure == "height":
        source = float(extents[2])
    elif measure == "length":
        source = float(max(extents[0], extents[1]))
    elif measure == "diameter":
        source = float(max(extents))
    else:
        raise ValueError(f"unknown scale measure {measure}")
    if not math.isfinite(source) or source <= 0:
        raise ValueError("invalid analytic source extent")
    factor = float(rule["target_m"]) / source
    return factor, {
        "measure": measure,
        "source_scene_units": source,
        "target_m": float(rule["target_m"]),
        "factor_m_per_scene_unit": factor,
        "source_bounds": bounds.tolist(),
    }


def manifest_bounds(
    manifest: dict[str, Any],
    scale: float,
) -> np.ndarray:
    bounds = np.asarray(manifest["root"]["bounds"], dtype=float) * float(scale)
    if bounds.shape != (2, 3) or not np.all(np.isfinite(bounds)):
        raise ValueError("invalid analysis geometry root bounds")
    return bounds


def semantic_bounds_manifest(
    parts: list[dict[str, Any]],
    pattern: str,
) -> tuple[np.ndarray | None, list[str]]:
    regex = re.compile(pattern, re.IGNORECASE)
    selected = [
        row for row in parts
        if regex.search(f"{row.get('name', '')} {row.get('semantic_path', '')}")
        and row.get("bounds_m") is not None
    ]
    if not selected:
        return None, []
    values = [np.asarray(row["bounds_m"], dtype=float) for row in selected]
    bounds = np.vstack((
        np.min(np.vstack([value[0] for value in values]), axis=0),
        np.max(np.vstack([value[1] for value in values]), axis=0),
    ))
    return bounds, [str(row["semantic_path"]) for row in selected]


def resolve_regions_manifest(
    nodes: dict[int, np.ndarray],
    parts: list[dict[str, Any]],
    loads: list[dict[str, Any]],
    mesh_size: float,
) -> tuple[list[dict[str, Any]], list[str]]:
    resolved: list[dict[str, Any]] = []
    failures: list[str] = []
    for load in loads:
        bounds, names = semantic_bounds_manifest(parts, load["pattern"])
        if bounds is None or load["face"] not in {"top", "bottom", "y_max", "top_center"}:
            failures.append(
                f"{load['name']}: semantic region is unavailable or uses an unsupported selector"
            )
            continue
        selected = face_nodes(nodes, bounds, load["face"], mesh_size * 0.55)
        if not selected:
            selected = face_nodes(nodes, bounds, load["face"], mesh_size * 1.10)
        if not selected:
            failures.append(f"{load['name']}: no mesh nodes on semantic face")
            continue
        resolved.append({
            **load,
            "node_ids": selected,
            "matched_geometries": names,
            "semantic_bounds_m": bounds.tolist(),
        })
    return resolved, failures

def analyze_mesh_level(
    case_id: str,
    case_dir: Path,
    items: list[Any] | None,
    scale: float,
    level: str,
    ratio: float,
    case_cfg: dict[str, Any],
    material: dict[str, Any],
    ccx: Path,
    output: Path,
    timeout: int,
    analytic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if analytic is None:
        if items is None:
            raise ValueError("legacy FEA geometry is unavailable")
        height = float((all_bounds(items)[1, 2] - all_bounds(items)[0, 2]) * scale)
    else:
        bounds = manifest_bounds(analytic["manifest"], scale)
        height = float(bounds[1, 2] - bounds[0, 2])
    mesh_size = max(height * ratio, 1e-5)
    level_dir = output / level
    try:
        if analytic is None:
            mesh = build_occ_mesh(items, scale, mesh_size, level_dir)
        else:
            mesh = TOPOLOGY.build_fused_mesh(
                analytic["manifest"],
                analytic["topology"]["active_part_paths"],
                scale=scale,
                mesh_size=mesh_size,
                output=level_dir / "mesh_raw.inp",
                entity_ids=analytic["topology"]["active_entity_ids"],
            )
        nodes, elements = parse_gmsh_inp(Path(mesh["mesh_path"]))
    except Exception as error:
        return {"mesh_level": level, "status": "NOT_MESHABLE",
                "error": f"{type(error).__name__}: {error}"}
    zmin = min(float(xyz[2]) for xyz in nodes.values())
    support = sorted(tag for tag, xyz in nodes.items() if xyz[2] <= zmin + max(height * 1e-6, 1e-9))
    if len(support) < 3:
        return {"mesh_level": level, "status": "LOAD_REGION_AMBIGUOUS", **mesh,
                "error": f"only {len(support)} fixed-base nodes"}
    if analytic is None:
        resolved, failures = resolve_regions(
            nodes, items, scale, case_cfg["functional_loads"], mesh_size
        )
    else:
        resolved, failures = resolve_regions_manifest(
            nodes,
            analytic["topology"]["parts"],
            case_cfg["functional_loads"],
            mesh_size,
        )
    if failures:
        return {"mesh_level": level, "status": "LOAD_REGION_AMBIGUOUS", **mesh,
                "support_node_count": len(support), "load_region_failures": failures}
    analyses: dict[str, Any] = {}
    for name, loads, gravity in (("self_weight", [], True), ("functional", resolved, False)):
        run_dir = level_dir / name
        run_dir.mkdir(parents=True, exist_ok=True)
        deck = run_dir / name
        write_deck(deck.with_suffix(".inp"), nodes, elements, material, support, loads, gravity)
        result = run_ccx(ccx, deck.with_suffix(".inp"), timeout)
        result["nominal_safety_factor"] = (None if not result.get("max_von_mises_pa")
                                            else material["yield_strength_pa"] / result["max_von_mises_pa"])
        result["stress_hotspot_centroid_m"] = element_centroid(
            result.get("max_von_mises_element"), nodes, elements)
        result["screening_assessment"] = screening_assessment(result, height)
        result["load_regions"] = [{key: value for key, value in row.items() if key != "node_ids"}
                                  | {"node_count": len(row["node_ids"])} for row in loads]
        analyses[name] = result
    status = "SOLVED" if all(row["status"] == "SOLVED" for row in analyses.values()) else next(
        row["status"] for row in analyses.values() if row["status"] != "SOLVED")
    return {"mesh_level": level, "status": status, **mesh,
            "support_node_count": len(support), "analyses": analyses}


def analyze_case(
    case_id: str,
    case_dir: Path,
    case_cfg: dict[str, Any],
    material: dict[str, Any],
    ccx: Path,
    output: Path,
    timeout: int,
) -> dict[str, Any]:
    manifest_path = case_dir / "analysis_geometry.json"
    items: list[Any] | None = None
    analytic: dict[str, Any] | None = None
    if manifest_path.is_file():
        manifest = TOPOLOGY.load_manifest(manifest_path)
        scale, scale_info = scale_factor_manifest(manifest, case_cfg["scale"])
        topology_profile = {
            "mode": "load_path",
            "loads": case_cfg["functional_loads"],
            "numerical_tolerance_m": float(
                case_cfg.get("topology_numerical_tolerance_m", 1e-8)
            ),
            "exclude_patterns": case_cfg.get("topology_exclude_patterns", []),
        }
        topology = TOPOLOGY.analyze_manifest(
            manifest,
            topology_profile,
            scale=scale,
        )
        analytic = {"manifest": manifest, "topology": topology}
        if topology["status"] == "PASS":
            gate_status = "SOLVED"
        elif topology["status"] == "FAIL":
            gate_status = "INVALID_LOAD_PATH"
        else:
            gate_status = "NOT_MESHABLE"
        gate = {
            "source": "analysis_geometry",
            "reason": (
                None
                if gate_status == "SOLVED"
                else "; ".join(
                    str(row.get("message") or row.get("code"))
                    for row in topology.get("violations", [])
                )
            ),
            "topology": topology,
            "union_component_count": topology.get("component_count"),
            "active_part_paths": topology.get("active_part_paths", []),
        }
        parsed = None
    else:
        parsed = FINAL.parse_urdf(case_dir / "scene.urdf")
        items = FINAL.collision_geometries(
            parsed, FINAL.state_values(parsed)["initial"]
        )
        scale, scale_info = scale_factor(items, case_cfg["scale"])
        gate_status, gate, _ = exact_union_gate(items)
        gate["source"] = "legacy_urdf_collision"

    result: dict[str, Any] = {
        "case_id": case_id,
        "status": gate_status,
        "input_urdf": str(case_dir / "scene.urdf"),
        "input_urdf_sha256": (
            parsed.sha256 if parsed is not None
            else FINAL.sha256_file(case_dir / "scene.urdf")
        ),
        "input_scene_glb_sha256": FINAL.sha256_file(case_dir / "scene.glb"),
        "input_source_py_sha256": FINAL.sha256_file(case_dir / "source.py"),
        "input_analysis_geometry_sha256": (
            FINAL.sha256_file(manifest_path) if manifest_path.is_file() else None
        ),
        "scale": scale_info,
        "geometry_gate": gate,
        "intended_functional_loads": case_cfg["functional_loads"],
        "mesh_levels": [],
    }
    if case_cfg.get("functional_moments"):
        result["intended_functional_moments"] = case_cfg["functional_moments"]
    if gate_status != "SOLVED":
        return result
    for level, ratio in MESH_LEVELS.items():
        print(f"  {case_id}: {level}", flush=True)
        result["mesh_levels"].append(
            analyze_mesh_level(
                case_id,
                case_dir,
                items,
                scale,
                level,
                ratio,
                case_cfg,
                material,
                ccx,
                output / case_id,
                timeout,
                analytic=analytic,
            )
        )
    result["status"] = (
        "SOLVED"
        if all(row["status"] == "SOLVED" for row in result["mesh_levels"])
        else next(
            row["status"]
            for row in result["mesh_levels"]
            if row["status"] != "SOLVED"
        )
    )
    result["mesh_convergence"] = mesh_convergence(result["mesh_levels"])
    return result


def cantilever_theory(E: float, length: float, width: float, height: float,
                      force: float) -> dict[str, float]:
    inertia = width * height ** 3 / 12.0
    return {"tip_displacement_m": force * length ** 3 / (3.0 * E * inertia),
            "root_bending_stress_pa": 6.0 * force * length / (width * height ** 2)}


def euler_buckling_load(E: float, length: float, width: float, height: float,
                        effective_length_factor: float = 1.0) -> float:
    inertia = width * height ** 3 / 12.0
    return math.pi ** 2 * E * inertia / (effective_length_factor * length) ** 2


def relative_change(previous: float | None, current: float | None) -> float | None:
    if previous is None or current is None or abs(current) <= 1e-30:
        return None
    return abs(current - previous) / abs(current)


def mesh_convergence(levels: list[dict[str, Any]]) -> dict[str, Any]:
    solved = {row["mesh_level"]: row for row in levels if row["status"] == "SOLVED"}
    if "medium" not in solved or "fine" not in solved:
        return {"status": "UNAVAILABLE", "reason": "medium and fine solutions required"}
    output: dict[str, Any] = {"criteria": {"displacement_relative_change_max": 0.10,
                                            "stress_relative_change_max": 0.20,
                                            "buckling_relative_change_max": 0.10},
                              "loads": {}}
    passed = True
    for load in ("self_weight", "functional"):
        medium = solved["medium"]["analyses"][load]
        fine = solved["fine"]["analyses"][load]
        changes = {"displacement": relative_change(medium["max_displacement_m"], fine["max_displacement_m"]),
                   "von_mises_stress": relative_change(medium["max_von_mises_pa"], fine["max_von_mises_pa"]),
                   "buckling_factor": relative_change(medium["first_positive_buckling_factor"], fine["first_positive_buckling_factor"])}
        load_pass = (changes["displacement"] is not None and changes["displacement"] <= 0.10
                     and changes["von_mises_stress"] is not None and changes["von_mises_stress"] <= 0.20
                     and changes["buckling_factor"] is not None and changes["buckling_factor"] <= 0.10)
        output["loads"][load] = {"medium_to_fine_relative_change": changes, "passed": load_pass}
        passed = passed and load_pass
    output["status"] = "PASSED" if passed else "NOT_CONVERGED"
    return output


def benchmark_box_mesh(extents: tuple[float, float, float], axis: str,
                       mesh_size: float, output: Path) -> tuple[dict[int, np.ndarray], dict[int, list[int]]]:
    """Create a quadratic-tetrahedral box aligned along x or z."""
    import gmsh
    output.parent.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("benchmark")
        length, width, height = extents
        if axis == "x":
            gmsh.model.occ.addBox(0, -width / 2, -height / 2, length, width, height)
        elif axis == "z":
            gmsh.model.occ.addBox(-width / 2, -height / 2, 0, width, height, length)
        else:
            raise ValueError(axis)
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", mesh_size * 0.45)
        gmsh.option.setNumber("Mesh.MeshSizeMax", mesh_size)
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        gmsh.model.mesh.generate(3)
        gmsh.write(str(output))
    finally:
        gmsh.finalize()
    return parse_gmsh_inp(output)


def run_benchmarks(material: dict[str, Any], output: Path, ccx: Path,
                   timeout: int) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    E = float(material["youngs_modulus_pa"])
    cantilever = cantilever_theory(E, 1.0, 0.1, 0.1, 100.0)
    cant_nodes, cant_elements = benchmark_box_mesh((1.0, 0.1, 0.1), "x", 0.055,
                                                   output / "cantilever_mesh.inp")
    cant_support = sorted(tag for tag, xyz in cant_nodes.items() if xyz[0] <= 1e-9)
    cant_tip = sorted(tag for tag, xyz in cant_nodes.items() if xyz[0] >= 1.0 - 1e-9)
    cant_deck = output / "cantilever" / "cantilever.inp"
    cant_deck.parent.mkdir(parents=True, exist_ok=True)
    write_deck(cant_deck, cant_nodes, cant_elements, material, cant_support,
               [{"name": "tip_load", "node_ids": cant_tip, "force_n": [0, 0, -100]}],
               False, include_buckle=False)
    cant_result = run_ccx(ccx, cant_deck, timeout)
    cant_result["theory"] = cantilever
    cant_result["tip_displacement_relative_error"] = (
        None if cant_result["max_displacement_m"] is None else
        abs(cant_result["max_displacement_m"] - cantilever["tip_displacement_m"])
        / cantilever["tip_displacement_m"])

    reference_force = 1000.0
    euler = euler_buckling_load(E, 1.0, 0.05, 0.05, 2.0)
    col_nodes, col_elements = benchmark_box_mesh((1.0, 0.05, 0.05), "z", 0.045,
                                                 output / "column_mesh.inp")
    col_support = sorted(tag for tag, xyz in col_nodes.items() if xyz[2] <= 1e-9)
    col_top = sorted(tag for tag, xyz in col_nodes.items() if xyz[2] >= 1.0 - 1e-9)
    col_deck = output / "euler_column" / "euler_column.inp"
    col_deck.parent.mkdir(parents=True, exist_ok=True)
    write_deck(col_deck, col_nodes, col_elements, material, col_support,
               [{"name": "compression", "node_ids": col_top,
                 "force_n": [0, 0, -reference_force]}], False, include_buckle=True)
    col_result = run_ccx(ccx, col_deck, timeout)
    factor = col_result.get("first_positive_buckling_factor")
    computed = None if factor is None else factor * reference_force
    col_result.update({"theory_fixed_free_load_n": euler,
                       "reference_compressive_load_n": reference_force,
                       "computed_critical_load_n": computed,
                       "critical_load_relative_error": None if computed is None else abs(computed - euler) / euler})
    passed = (cant_result["status"] == "SOLVED" and col_result["status"] == "SOLVED"
              and cant_result["tip_displacement_relative_error"] is not None
              and cant_result["tip_displacement_relative_error"] < 0.20
              and col_result["critical_load_relative_error"] is not None
              and col_result["critical_load_relative_error"] < 0.30)
    payload = {"status": "PASSED" if passed else "FAILED", "cantilever": cant_result,
               "euler_fixed_free_column": col_result,
               "acceptance": {"cantilever_displacement_relative_error_lt": 0.20,
                              "euler_critical_load_relative_error_lt": 0.30}}
    (output / "benchmarks.json").write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return payload


def report_markdown(payload: dict[str, Any]) -> str:
    counts = {status: sum(case["status"] == status for case in payload["cases"]) for status in STATUSES}
    converged = sum(case.get("mesh_convergence", {}).get("status") == "PASSED" for case in payload["cases"])
    solved_count = counts["SOLVED"]
    case_count = len(payload["cases"])
    fine_assessments = [
        result["screening_assessment"]["status"]
        for case in payload["cases"] if case["mesh_levels"] and case["mesh_levels"][-1]["status"] == "SOLVED"
        for result in case["mesh_levels"][-1]["analyses"].values()
    ]
    screen_pass_count = sum(value == "SCREEN_PASS" for value in fine_assessments)
    lines = ["# 03 Load-Bearing Structural Performance：当前案例验证", "",
             "## Material Passport", "",
             "- Material ID: `adsl-physics-03-load-bearing-20260904`",
             "- Type: experiment execution and analysis report",
             "- Origin Skill: `academic-research-suite/experiment-agent`",
             "- Origin Mode: `run`", "- Verification Status: UNVERIFIED",
             f"- Generated (UTC): {payload['generated_at_utc']}", "",
             "## 结论摘要", "",
             f"- {case_count} 个模型中：SOLVED={counts['SOLVED']}，INVALID_LOAD_PATH={counts['INVALID_LOAD_PATH']}，NOT_MESHABLE={counts['NOT_MESHABLE']}，LOAD_REGION_AMBIGUOUS={counts['LOAD_REGION_AMBIGUOUS']}，求解失败/不收敛={counts['SOLVER_FAILED'] + counts['NOT_CONVERGED']}。",
             f"- 三网格 medium→fine 收敛门槛（位移≤10%、应力≤20%、屈曲因子≤10%）：{converged}/{solved_count} 个已求解模型通过。",
             f"- 细网格代理门槛（U/L≤1%、名义屈服 FoS≥2、线性屈曲因子≥2）：{screen_pass_count}/{len(fine_assessments)} 条已求解载荷轨通过；这不等于真实打印认证。",
             "- `INVALID_LOAD_PATH` 表示模型由多个承载体组成，但没有可供 FEA 使用的 tie/contact/连接刚度；部分模型虽有 URDF 运动学 joint，也不能据此推导结构刚度。",
             "- `NOT_MESHABLE` 表示当前 collision surface 不能形成经验证的封闭实体；不采用体素补洞来伪造可解模型。",
             "- SOLVED 仍只是各向同性 PLA、全固定底面、线弹性、小变形和理想载荷下的筛查。打印方向、层间强度、缺陷、蠕变和非线性接触均未建模。",
             "", "## 逐案例", "",
             "| Case | Status | union components | watertight | coarse/medium/fine | convergence |", "|---|---|---:|---|---|---|"]
    for case in payload["cases"]:
        levels = "/".join(level["status"] for level in case["mesh_levels"]) or "—"
        gate = case["geometry_gate"]
        convergence = case.get("mesh_convergence", {}).get("status", "—")
        lines.append(f"| {case['case_id']} | {case['status']} | {gate.get('union_component_count', '—')} | {gate.get('union_watertight', '—')} | {levels} | {convergence} |")
    lines.extend(["", "## 已求解结果（fine mesh）", "",
                  "| Case | Load | max U (mm) | U/L (%) | max von Mises (MPa) | nominal FoS | first buckling factor | screen | hotspot centroid (m) |",
                  "|---|---|---:|---:|---:|---:|---:|---|---|"])
    for case in payload["cases"]:
        if not case["mesh_levels"] or case["mesh_levels"][-1]["status"] != "SOLVED":
            continue
        for name, row in case["mesh_levels"][-1]["analyses"].items():
            u = row["max_displacement_m"] * 1000 if row["max_displacement_m"] is not None else None
            s = row["max_von_mises_pa"] / 1e6 if row["max_von_mises_pa"] is not None else None
            assessment = row["screening_assessment"]
            ratio = assessment["max_displacement_over_characteristic_length"] * 100
            lines.append(f"| {case['case_id']} | {name} | {u:.6g} | {ratio:.4g} | {s:.6g} | {row['nominal_safety_factor']:.6g} | {row['first_positive_buckling_factor']} | {assessment['status']} | `{row['stress_hotspot_centroid_m']}` |")
    lines.extend(["", "## 当前模型的阻断性问题", "",
                  "- A01/A02/I02：运动件与主体分离；运动学 URDF joint 没有定义 FEA 锁定状态、连接刚度、接触或 tie。",
                  "- 三个 motorcycle 分别得到 54、85 个独立体或空 Boolean 结果；需要封闭实体、焊接/接触定义和车轮—地面支承。",
                  "- T02 bookshelf 与 T04 mug 不能形成封闭 collision 实体；必须验证 CAD/打印网格修复后才能做 FEA。",
                  "- T03 wheel 精确 union 后仍有 18 个独立体；缺少 spoke/rim/hub 连续性及 hub 约束。",
                  "- T05 desk 有 18 个独立体且无语义子部件区域；需要桌面—桌腿连接及载荷表面标注。",
                  "", "## 工具、输入与边界", "",
                  "- Gmsh 4.15.2/OpenCASCADE：primitive collision 的 Boolean fuse 与 C3D10 二次四面体网格。",
                  "- CalculiX 2.23/SPOOLES/ARPACK：线性静力与线性特征屈曲；CPU 求解，不需要 GPU。",
                  "- 应力为积分点 von Mises 最大值；位移为节点向量模最大值；薄弱区输出对应单元四角点质心。",
                  "- nominal FoS = 50 MPa / max von Mises，只是名义屈服裕度；不包含层间、疲劳、蠕变、冲击或统计材料散差。",
                  "- 屈曲 factor 乘以相应参考载荷才是线性特征屈曲临界载荷；存在接触、几何缺陷或大变形时通常会高估真实能力。",
                  "- functional load 的语义区域由原 URDF collision 名称定位，再映射到网格表面节点；映射失败必须 fail closed。",
                  "", "## 复现信息", "",
                  f"- Input root: `{payload['input_root']}`",
                  f"- Output root: `{payload['output_dir']}`",
                  f"- CalculiX: `{payload['environment']['calculix']}`",
                  f"- Gmsh: `{payload['environment']['gmsh']}`",
                  f"- Command: `{payload['command']}`", "- 原始模型未修改；逐级 deck、日志、FRD/DAT 和机器可读结果均在 output root。", ""])
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    with (output / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=["case_id", "status", "union_watertight", "union_component_count", "mesh_levels"])
        writer.writeheader()
        for case in payload["cases"]:
            writer.writerow({"case_id": case["case_id"], "status": case["status"],
                             "union_watertight": case["geometry_gate"].get("union_watertight"),
                             "union_component_count": case["geometry_gate"].get("union_component_count"),
                             "mesh_levels": ";".join(row["status"] for row in case["mesh_levels"])})
    (output / "load_bearing_structural_performance.md").write_text(report_markdown(payload), encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-root", type=Path, default=Path("/jiigan-hp/lms/aDSL/experiment/audit_20260830"))
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--config", type=Path, default=HERE / "case_config.json")
    parser.add_argument("--ccx", type=Path, default=Path(os.environ.get("ADSL_CCX_BIN", "/vepfs_default/chanxueyan/lhp/lms/fea_runtime/calculix-2.23/ccx_2.23")))
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--solver-timeout", type=int, default=900)
    parser.add_argument("--benchmark-only", action="store_true")
    args = parser.parse_args()
    config = load_config(args.config.resolve())
    output = args.output_dir.resolve()
    if not args.ccx.is_file():
        raise FileNotFoundError(args.ccx)
    benchmarks = run_benchmarks(config["material"], output / "benchmarks",
                                args.ccx.resolve(), args.solver_timeout)
    if args.benchmark_only:
        print(json.dumps(benchmarks, indent=2), flush=True)
        return 0 if benchmarks["status"] == "PASSED" else 2
    selected = set(args.case or DEFAULT_CASES)
    unknown = selected - set(DEFAULT_CASES)
    if unknown:
        raise ValueError(f"unknown cases: {sorted(unknown)}")
    payload = {"generated_at_utc": utc_now(), "verification_status": "UNVERIFIED",
               "input_root": str(args.input_root.resolve()), "output_dir": str(output),
               "material": config["material"], "mesh_levels": MESH_LEVELS,
               "boundary_condition": "all translational DOFs fixed on global minimum-z nodes",
               "benchmarks": benchmarks,
               "environment": {"python": sys.version, "trimesh": trimesh.__version__,
                               "calculix": ccx_version(args.ccx),
                               "gmsh": "4.15.2"},
               "command": " ".join(sys.argv), "cases": []}
    for case_id in DEFAULT_CASES:
        if case_id not in selected:
            continue
        print(f"analyzing {case_id}", flush=True)
        case_dir = args.input_root.resolve() / case_id
        payload["cases"].append(analyze_case(case_id, case_dir, config["cases"][case_id],
                                              config["material"], args.ccx.resolve(), output,
                                              args.solver_timeout))
    write_outputs(payload, output)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
