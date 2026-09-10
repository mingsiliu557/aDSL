from __future__ import annotations

from dataclasses import dataclass
import json
import math
from pathlib import Path
import re
from typing import Any, Iterable

import numpy as np


SUPPORTED_PRIMITIVES = {"cube", "cylinder", "sphere"}
SUPPORTED_BOOLEANS = {"UNION", "DIFFERENCE", "INTERSECT"}


class TopologyAnalysisError(RuntimeError):
    pass


@dataclass
class _Part:
    path: str
    name: str
    feature_id: str | None
    source_ids: list[str]
    source_locations: list[str]
    bounds: list[list[float]] | None
    volumes: list[int]


def load_manifest(path: str | Path) -> dict[str, Any]:
    payload = json.loads(Path(path).expanduser().resolve().read_text(encoding="utf-8"))
    if payload.get("version") != 1 or not isinstance(payload.get("root"), dict):
        raise ValueError("unsupported or incomplete analysis geometry manifest")
    return payload


def _matrix(value: Any, scale: float) -> list[float]:
    transform = np.eye(4, dtype=float) if value is None else np.asarray(value, dtype=float)
    if transform.shape != (4, 4) or not np.all(np.isfinite(transform)):
        raise TopologyAnalysisError("primitive transform must be a finite 4x4 matrix")
    result = transform.copy()
    result[:3, :] *= float(scale)
    return result.reshape(-1).tolist()


def _primitive(gmsh: Any, primitive: dict[str, Any], scale: float) -> int:
    kind = str(primitive.get("type", "")).lower()
    params = primitive.get("params", {})
    if kind not in SUPPORTED_PRIMITIVES:
        raise TopologyAnalysisError(f"unsupported analytic primitive: {kind or '<missing>'}")
    if kind == "cube":
        size = np.asarray(params.get("scale"), dtype=float)
        center = np.asarray(params.get("center", [0, 0, 0]), dtype=float)
        if size.shape != (3,) or center.shape != (3,) or np.min(size) <= 0:
            raise TopologyAnalysisError("invalid cube parameters")
        tag = gmsh.model.occ.addBox(
            float(center[0] - size[0] / 2),
            float(center[1] - size[1] / 2),
            float(center[2] - size[2] / 2),
            *map(float, size),
        )
    elif kind == "cylinder":
        p0 = np.asarray(params.get("p0"), dtype=float)
        p1 = np.asarray(params.get("p1"), dtype=float)
        radius = float(params.get("radius", 0.0))
        delta = p1 - p0
        if p0.shape != (3,) or p1.shape != (3,) or radius <= 0 or np.linalg.norm(delta) <= 0:
            raise TopologyAnalysisError("invalid cylinder parameters")
        tag = gmsh.model.occ.addCylinder(*map(float, p0), *map(float, delta), radius)
    else:
        center = np.asarray(params.get("center", [0, 0, 0]), dtype=float)
        radius = float(params.get("radius", 0.0))
        if center.shape != (3,) or radius <= 0:
            raise TopologyAnalysisError("invalid sphere parameters")
        tag = gmsh.model.occ.addSphere(*map(float, center), radius)
    gmsh.model.occ.affineTransform([(3, tag)], _matrix(primitive.get("xform"), scale))
    return tag


def _volumes(values: Iterable[tuple[int, int]]) -> list[int]:
    return [int(tag) for dim, tag in values if int(dim) == 3]


def _fuse(gmsh: Any, tags: list[int]) -> list[int]:
    tags = list(dict.fromkeys(tags))
    if len(tags) < 2:
        return tags
    output, _ = gmsh.model.occ.fuse(
        [(3, tags[0])],
        [(3, tag) for tag in tags[1:]],
        removeObject=True,
        removeTool=True,
    )
    return _volumes(output)


def _build_node(gmsh: Any, node: dict[str, Any], scale: float) -> list[int]:
    primitives = [
        primitive
        for primitive in node.get("primitives", [])
        if str(primitive.get("type", "")).lower() != "boolean"
    ]
    local = [_primitive(gmsh, primitive, scale) for primitive in primitives]
    children = list(node.get("children", []))
    child_groups = [(str(child.get("name", "")), _build_node(gmsh, child, scale)) for child in children]
    mode = node.get("boolean_mode")
    if mode is None:
        return [*local, *(tag for _, group in child_groups for tag in group)]
    mode = str(mode).upper()
    if mode not in SUPPORTED_BOOLEANS:
        raise TopologyAnalysisError(f"unsupported boolean mode: {mode}")
    ordered = []
    base = next((group for name, group in child_groups if name == "base"), None)
    if base is not None:
        ordered.append(base)
    ordered.extend(group for name, group in child_groups if name.startswith("op_"))
    ordered.extend(group for name, group in child_groups if name != "base" and not name.startswith("op_"))
    if not ordered or not ordered[0]:
        raise TopologyAnalysisError(f"{mode} has no analytic operand")
    if mode == "UNION":
        return _fuse(gmsh, [tag for group in ordered for tag in group])
    current = _fuse(gmsh, ordered[0])
    if len(current) != 1:
        raise TopologyAnalysisError(f"{mode} base produced {len(current)} volumes")
    if mode == "DIFFERENCE":
        tools = _fuse(gmsh, [tag for group in ordered[1:] for tag in group])
        if not tools:
            return current
        output, _ = gmsh.model.occ.cut(
            [(3, current[0])],
            [(3, tag) for tag in tools],
            removeObject=True,
            removeTool=True,
        )
        result = _volumes(output)
    else:
        result = current
        for group in ordered[1:]:
            other = _fuse(gmsh, group)
            output, _ = gmsh.model.occ.intersect(
                [(3, tag) for tag in result],
                [(3, tag) for tag in other],
                removeObject=True,
                removeTool=True,
            )
            result = _volumes(output)
    if not result:
        raise TopologyAnalysisError(f"{mode} produced no solid volume")
    return result


def _root_parts(root: dict[str, Any]) -> list[dict[str, Any]]:
    has_geometry = any(
        str(value.get("type", "")).lower() in SUPPORTED_PRIMITIVES | {"boolean"}
        for value in root.get("primitives", [])
    )
    return [root] if has_geometry else list(root.get("children", []))


def _scaled_bounds(node: dict[str, Any], scale: float) -> list[list[float]] | None:
    value = node.get("bounds")
    if not isinstance(value, list) or len(value) != 2:
        return None
    bounds = np.asarray(value, dtype=float) * float(scale)
    if bounds.shape != (2, 3) or not np.all(np.isfinite(bounds)):
        return None
    return bounds.tolist()


def _part(gmsh: Any, node: dict[str, Any], scale: float) -> _Part:
    volumes = _fuse(gmsh, _build_node(gmsh, node, scale))
    if not volumes:
        raise TopologyAnalysisError(f"{node.get('semantic_path')} produced no solid volume")
    return _Part(
        path=str(node.get("semantic_path")),
        name=str(node.get("name") or node.get("label") or node.get("semantic_path")),
        feature_id=node.get("feature_id"),
        source_ids=[str(value) for value in node.get("source_ids", [])],
        source_locations=[str(value) for value in node.get("source_locations", [])],
        bounds=_scaled_bounds(node, scale),
        volumes=volumes,
    )


def _probe(gmsh: Any, left: _Part, right: _Part, tolerance: float) -> dict[str, Any]:
    best: tuple[float, tuple[float, ...], int, int] | None = None
    connected = False
    for left_tag in left.volumes:
        for right_tag in right.volumes:
            distance = tuple(map(float, gmsh.model.occ.getDistance(3, left_tag, 3, right_tag)))
            if distance[0] < 0:
                continue
            if best is None or distance[0] < best[0]:
                best = (distance[0], distance, left_tag, right_tag)
            if distance[0] <= tolerance:
                copies_left = gmsh.model.occ.copy([(3, left_tag)])
                copies_right = gmsh.model.occ.copy([(3, right_tag)])
                fused, _ = gmsh.model.occ.fuse(
                    copies_left,
                    copies_right,
                    removeObject=True,
                    removeTool=True,
                )
                fused_volumes = _volumes(fused)
                if len(fused_volumes) == 1:
                    connected = True
                if fused:
                    gmsh.model.occ.remove(fused, recursive=True)
                if connected:
                    break
        if connected:
            break
    if best is None:
        return {
            "left_path": left.path,
            "right_path": right.path,
            "status": "distance_unavailable",
            "connected": False,
        }
    minimum, coordinates, _, _ = best
    weak = minimum <= tolerance and not connected
    return {
        "left_path": left.path,
        "right_path": right.path,
        "left_feature_id": left.feature_id,
        "right_feature_id": right.feature_id,
        "left_source_ids": left.source_ids,
        "right_source_ids": right.source_ids,
        "left_source_locations": left.source_locations,
        "right_source_locations": right.source_locations,
        "distance_m": minimum,
        "closest_points_m": [list(coordinates[1:4]), list(coordinates[4:7])],
        "connected": connected,
        "contact_kind": (
            "face_or_volume" if connected else "point_or_edge" if weak else "gap"
        ),
    }


def _components(paths: list[str], contacts: list[dict[str, Any]]) -> list[list[str]]:
    adjacency = {path: set() for path in paths}
    for contact in contacts:
        if contact.get("connected"):
            left, right = contact["left_path"], contact["right_path"]
            adjacency[left].add(right)
            adjacency[right].add(left)
    output: list[list[str]] = []
    remaining = set(paths)
    while remaining:
        start = min(remaining)
        seen = {start}
        pending = [start]
        while pending:
            current = pending.pop()
            for neighbor in adjacency[current] - seen:
                seen.add(neighbor)
                pending.append(neighbor)
        output.append(sorted(seen))
        remaining -= seen
    return sorted(output, key=lambda row: (-len(row), row))


def _matches(value: str, patterns: list[str]) -> bool:
    return any(re.search(pattern, value, re.IGNORECASE) for pattern in patterns)


def _nearest_between(
    contacts: list[dict[str, Any]],
    left_paths: set[str],
    right_paths: set[str],
) -> dict[str, Any] | None:
    rows = [
        row
        for row in contacts
        if (
            row.get("left_path") in left_paths and row.get("right_path") in right_paths
        ) or (
            row.get("right_path") in left_paths and row.get("left_path") in right_paths
        )
    ]
    rows = [row for row in rows if isinstance(row.get("distance_m"), (int, float))]
    return min(rows, key=lambda row: float(row["distance_m"]), default=None)


def analyze_manifest(
    manifest: dict[str, Any],
    profile: dict[str, Any],
    *,
    scale: float = 1.0,
) -> dict[str, Any]:
    import gmsh

    mode = str(profile.get("mode", "load_path"))
    if mode not in {"load_path", "one_piece"}:
        raise ValueError("topology mode must be load_path or one_piece")
    tolerance = float(profile.get("numerical_tolerance_m", 1e-8))
    if not math.isfinite(tolerance) or tolerance < 0:
        raise ValueError("numerical_tolerance_m must be finite and non-negative")
    excludes = [str(value) for value in profile.get("exclude_patterns", [])]

    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("adsl_topology")
        nodes = [
            node
            for node in _root_parts(manifest["root"])
            if not _matches(str(node.get("semantic_path", "")), excludes)
            and str(node.get("attach_mode", "part")) != "joint"
        ]
        parts = [_part(gmsh, node, scale) for node in nodes]
        gmsh.model.occ.synchronize()
        contacts = [
            _probe(gmsh, left, right, tolerance)
            for index, left in enumerate(parts)
            for right in parts[index + 1 :]
        ]
        paths = [part.path for part in parts]
        components = _components(paths, contacts)
        component_for = {
            path: set(component) for component in components for path in component
        }
        violations: list[dict[str, Any]] = []

        active_paths: list[str] = []
        missing_inputs: list[str] = []
        load_paths: list[str] = []
        support_paths: list[str] = []
        if mode == "one_piece":
            if len(components) > 1:
                anchor = set(components[0])
                for component in components[1:]:
                    relation = _nearest_between(contacts, set(component), anchor)
                    violations.append(
                        {
                            "code": "ONE_PIECE_DISCONNECTED",
                            "message": "A required one-piece component is disconnected",
                            "part_paths": sorted(component),
                            "relation": relation,
                        }
                    )
            active_paths = paths
        else:
            load_patterns = [
                str(value.get("pattern"))
                for value in profile.get("loads", [])
                if value.get("pattern")
            ]
            if not load_patterns:
                missing_inputs.append("load selectors")
            load_paths = [
                part.path
                for part in parts
                if _matches(f"{part.name} {part.path}", load_patterns)
            ]
            if not load_paths:
                missing_inputs.append("matched load parts")
            bounds_rows = [
                (part.path, np.asarray(part.bounds, dtype=float))
                for part in parts
                if part.bounds is not None
            ]
            if bounds_rows:
                minimum_z = min(float(bounds[0, 2]) for _, bounds in bounds_rows)
                height = max(
                    max(float(bounds[1, 2]) for _, bounds in bounds_rows) - minimum_z,
                    1e-9,
                )
                support_tolerance = max(height * 1e-6, tolerance)
                support_paths = [
                    path
                    for path, bounds in bounds_rows
                    if float(bounds[0, 2]) <= minimum_z + support_tolerance
                ]
            if not support_paths:
                missing_inputs.append("support parts")

            if not missing_inputs:
                common_components = [
                    component
                    for component in components
                    if set(load_paths).issubset(component)
                    and bool(set(support_paths) & set(component))
                ]
                if common_components:
                    active_paths = list(common_components[0])
                else:
                    support_components = {
                        path
                        for support in support_paths
                        for path in component_for[support]
                    }
                    disconnected_components = {
                        tuple(sorted(component_for[load_path]))
                        for load_path in load_paths
                        if not (component_for[load_path] & set(support_paths))
                    }
                    for component in sorted(disconnected_components):
                        relation = _nearest_between(
                            contacts,
                            set(component),
                            support_components,
                        )
                        affected_loads = sorted(set(component) & set(load_paths))
                        violations.append(
                            {
                                "code": "LOAD_PATH_DISCONNECTED",
                                "message": (
                                    f"{len(affected_loads)} load part(s) have no bonded "
                                    "path to support"
                                ),
                                "load_part_paths": affected_loads,
                                "disconnected_component_paths": list(component),
                                "support_part_paths": support_paths,
                                "relation": relation,
                            }
                        )
                    if not disconnected_components:
                        violations.append(
                            {
                                "code": "LOAD_PATH_SPLIT_ACROSS_COMPONENTS",
                                "message": "configured load parts do not share one support-connected solid",
                                "load_part_paths": load_paths,
                                "support_part_paths": support_paths,
                            }
                        )

        checked_paths = (
            set(paths)
            if mode == "one_piece"
            else set(active_paths) | set(load_paths) | set(support_paths)
        )
        for part in parts:
            if part.path not in checked_paths or len(part.volumes) == 1:
                continue
            violations.append(
                {
                    "code": "INTERNAL_PART_DISCONNECTED",
                    "message": f"{part.path} contains {len(part.volumes)} disconnected solids",
                    "part_paths": [part.path],
                    "feature_ids": [part.feature_id] if part.feature_id else [],
                    "source_ids": part.source_ids,
                    "source_locations": part.source_locations,
                    "component_count": len(part.volumes),
                }
            )

        weak_contacts = [
            row for row in contacts if row.get("contact_kind") == "point_or_edge"
        ]
        status = "INDETERMINATE" if missing_inputs else "FAIL" if violations else "PASS"
        return {
            "version": 1,
            "status": status,
            "mode": mode,
            "geometry_sha256": manifest.get("geometry_sha256"),
            "source_sha256": manifest.get("source_sha256"),
            "scale_m_per_scene_unit": float(scale),
            "numerical_tolerance_m": tolerance,
            "part_count": len(parts),
            "parts": [
                {
                    "semantic_path": part.path,
                    "name": part.name,
                    "feature_id": part.feature_id,
                    "source_ids": part.source_ids,
                    "source_locations": part.source_locations,
                    "bounds_m": part.bounds,
                    "internal_volume_count": len(part.volumes),
                }
                for part in parts
            ],
            "contacts": contacts,
            "weak_contacts": weak_contacts,
            "components": components,
            "component_count": len(components),
            "active_part_paths": sorted(active_paths),
            "missing_inputs": missing_inputs,
            "violations": violations,
            "assumptions": {
                "connection_rule": "OCC fuse must produce one volume; point/edge contact is insufficient",
                "joint_policy": "joint children are excluded from bonded-solid connectivity",
                "aabb_policy": "bounds select load/support candidates but never prove connection",
            },
        }
    except TopologyAnalysisError as error:
        return {
            "version": 1,
            "status": "INDETERMINATE",
            "mode": mode,
            "geometry_sha256": manifest.get("geometry_sha256"),
            "source_sha256": manifest.get("source_sha256"),
            "scale_m_per_scene_unit": float(scale),
            "missing_inputs": [],
            "violations": [
                {
                    "code": "ANALYTIC_RECONSTRUCTION_UNAVAILABLE",
                    "message": str(error),
                }
            ],
            "error": str(error),
        }
    finally:
        gmsh.finalize()


def build_fused_mesh(
    manifest: dict[str, Any],
    part_paths: Iterable[str],
    *,
    scale: float,
    mesh_size: float,
    output: str | Path,
) -> dict[str, Any]:
    import gmsh

    selected = set(map(str, part_paths))
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("adsl_fea")
        nodes = [
            node
            for node in _root_parts(manifest["root"])
            if str(node.get("semantic_path")) in selected
        ]
        parts = [_part(gmsh, node, scale) for node in nodes]
        fused = _fuse(gmsh, [tag for part in parts for tag in part.volumes])
        if len(fused) != 1:
            raise TopologyAnalysisError(
                f"FEA active load path produced {len(fused)} OCC volumes"
            )
        gmsh.model.occ.synchronize()
        gmsh.option.setNumber("Mesh.MeshSizeMin", float(mesh_size) * 0.45)
        gmsh.option.setNumber("Mesh.MeshSizeMax", float(mesh_size))
        gmsh.option.setNumber("Mesh.ElementOrder", 2)
        gmsh.option.setNumber("Mesh.SecondOrderLinear", 1)
        gmsh.model.mesh.generate(3)
        element_types = list(map(int, gmsh.model.mesh.getElements(3)[0]))
        if element_types != [11]:
            raise TopologyAnalysisError(
                f"expected only Gmsh type 11 C3D10 elements, got {element_types}"
            )
        gmsh.write(str(destination))
        element_tags = gmsh.model.mesh.getElements(3)[1]
        quality = gmsh.model.mesh.getElementQualities(
            np.concatenate(element_tags).astype(np.uint64),
            qualityName="minSJ",
        )
        return {
            "mesh_path": str(destination),
            "node_count": int(len(gmsh.model.mesh.getNodes()[0])),
            "element_count": int(sum(len(tags) for tags in element_tags)),
            "minimum_scaled_jacobian": float(np.min(quality)),
            "mesh_size_m": float(mesh_size),
            "gmsh_version": gmsh.option.getString("General.Version"),
            "active_part_paths": sorted(selected),
        }
    finally:
        gmsh.finalize()


__all__ = [
    "TopologyAnalysisError",
    "analyze_manifest",
    "build_fused_mesh",
    "load_manifest",
]
