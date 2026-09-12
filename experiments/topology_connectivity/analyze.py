from __future__ import annotations

from dataclasses import dataclass
from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
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


@dataclass
class _Entity:
    entity_id: str
    part: _Part
    index: int
    tag: int
    bounds: list[list[float]]


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


@contextmanager
def _operation(part: str, operation: str, operand_count: int):
    """Persist both boundaries so a killed native call remains identifiable."""
    path = os.environ.get("ADSL_GEOMETRY_PROGRESS_LOG")
    started_at = datetime.now(timezone.utc).isoformat()
    def record(event: str) -> None:
        if path:
            with Path(path).open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({
                    "event": event, "part": part, "operation": operation,
                    "operand_count": operand_count, "started_at": started_at,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                }) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
    record("start")
    try:
        yield
    except BaseException:
        record("error")
        raise
    else:
        record("complete")


def _fuse(gmsh: Any, tags: list[int], *, part: str = "<unknown>") -> list[int]:
    tags = list(dict.fromkeys(tags))
    if len(tags) < 2:
        return tags
    with _operation(part, "OCC fuse", len(tags)):
        output, _ = gmsh.model.occ.fuse(
            [(3, tags[0])], [(3, tag) for tag in tags[1:]],
            removeObject=True, removeTool=True,
        )
    return _volumes(output)


def _build_node(gmsh: Any, node: dict[str, Any], scale: float) -> list[int]:
    part = str(node.get("semantic_path", "<unknown>"))
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
        return _fuse(gmsh, [tag for group in ordered for tag in group], part=part)
    current = _fuse(gmsh, ordered[0], part=part)
    if len(current) != 1:
        raise TopologyAnalysisError(f"{mode} base produced {len(current)} volumes")
    if mode == "DIFFERENCE":
        tools = _fuse(gmsh, [tag for group in ordered[1:] for tag in group], part=part)
        if not tools:
            return current
        with _operation(part, "OCC cut", 1 + len(tools)):
            output, _ = gmsh.model.occ.cut(
                [(3, current[0])], [(3, tag) for tag in tools],
                removeObject=True, removeTool=True,
            )
        result = _volumes(output)
    else:
        result = current
        for group in ordered[1:]:
            other = _fuse(gmsh, group, part=part)
            with _operation(part, "OCC intersect", len(result) + len(other)):
                output, _ = gmsh.model.occ.intersect(
                    [(3, tag) for tag in result], [(3, tag) for tag in other],
                    removeObject=True, removeTool=True,
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
    volumes = _fuse(gmsh, _build_node(gmsh, node, scale), part=str(node.get("semantic_path")))
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



def _entities(gmsh: Any, parts: list[_Part]) -> list[_Entity]:
    output: list[_Entity] = []
    for part in parts:
        for index, tag in enumerate(part.volumes):
            raw = np.asarray(gmsh.model.occ.getBoundingBox(3, tag), dtype=float)
            bounds = raw.reshape(2, 3)
            if not np.all(np.isfinite(bounds)):
                raise TopologyAnalysisError("solid bounds are unavailable")
            output.append(
                _Entity(
                    entity_id=f"{part.path}#solid:{index}",
                    part=part,
                    index=index,
                    tag=tag,
                    bounds=bounds.tolist(),
                )
            )
    return output


def _probe(gmsh: Any, left: _Entity, right: _Entity, tolerance: float) -> dict[str, Any]:
    distance = tuple(map(float, gmsh.model.occ.getDistance(3, left.tag, 3, right.tag)))
    if distance[0] < 0:
        return {
            "left_entity_id": left.entity_id,
            "right_entity_id": right.entity_id,
            "left_path": left.part.path,
            "right_path": right.part.path,
            "status": "distance_unavailable",
            "connected": False,
        }
    connected = False
    if distance[0] <= tolerance:
        copies_left = gmsh.model.occ.copy([(3, left.tag)])
        copies_right = gmsh.model.occ.copy([(3, right.tag)])
        with _operation(f"{left.entity_id} <-> {right.entity_id}", "OCC fuse", 2):
            fused, _ = gmsh.model.occ.fuse(
                copies_left, copies_right, removeObject=True, removeTool=True,
            )
        connected = len(_volumes(fused)) == 1
        if fused:
            gmsh.model.occ.remove(fused, recursive=True)
    minimum = distance[0]
    weak = minimum <= tolerance and not connected
    return {
        "left_entity_id": left.entity_id,
        "right_entity_id": right.entity_id,
        "left_path": left.part.path,
        "right_path": right.part.path,
        "left_feature_id": left.part.feature_id,
        "right_feature_id": right.part.feature_id,
        "left_source_ids": left.part.source_ids,
        "right_source_ids": right.part.source_ids,
        "left_source_locations": left.part.source_locations,
        "right_source_locations": right.part.source_locations,
        "distance_m": minimum,
        "closest_points_m": [list(distance[1:4]), list(distance[4:7])],
        "connected": connected,
        "contact_kind": (
            "face_or_volume" if connected else "point_or_edge" if weak else "gap"
        ),
    }


def _components(entity_ids: list[str], contacts: list[dict[str, Any]]) -> list[list[str]]:
    adjacency = {entity_id: set() for entity_id in entity_ids}
    for contact in contacts:
        if contact.get("connected"):
            left = contact["left_entity_id"]
            right = contact["right_entity_id"]
            adjacency[left].add(right)
            adjacency[right].add(left)
    output: list[list[str]] = []
    remaining = set(entity_ids)
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
    left_entities: set[str],
    right_entities: set[str],
) -> dict[str, Any] | None:
    rows = [
        row
        for row in contacts
        if (
            row.get("left_entity_id") in left_entities
            and row.get("right_entity_id") in right_entities
        )
        or (
            row.get("right_entity_id") in left_entities
            and row.get("left_entity_id") in right_entities
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
        entities = _entities(gmsh, parts)
        if not entities:
            raise TopologyAnalysisError("assembly produced no solid entities")
        contacts = [
            _probe(gmsh, left, right, tolerance)
            for index, left in enumerate(entities)
            for right in entities[index + 1 :]
        ]
        if any(
            contact.get("status") == "distance_unavailable" for contact in contacts
        ):
            raise TopologyAnalysisError("OCC solid distance query is unavailable")
        entity_ids = [entity.entity_id for entity in entities]
        entity_by_id = {entity.entity_id: entity for entity in entities}
        entity_components = _components(entity_ids, contacts)
        component_for = {
            entity_id: set(component)
            for component in entity_components
            for entity_id in component
        }
        components = [
            sorted({entity_by_id[entity_id].part.path for entity_id in component})
            for component in entity_components
        ]
        paths = [part.path for part in parts]
        violations: list[dict[str, Any]] = []

        active_paths: list[str] = []
        active_entity_ids: list[str] = []
        missing_inputs: list[str] = []
        load_paths: list[str] = []
        support_paths: list[str] = []
        support_entity_ids: list[str] = []
        load_requirements: list[dict[str, Any]] = []
        if mode == "one_piece":
            if len(entity_components) > 1:
                anchor = set(entity_components[0])
                for component in entity_components[1:]:
                    relation = _nearest_between(contacts, set(component), anchor)
                    violations.append(
                        {
                            "code": "ONE_PIECE_DISCONNECTED",
                            "message": "A required final solid component is disconnected",
                            "part_paths": sorted(
                                {
                                    entity_by_id[entity_id].part.path
                                    for entity_id in component
                                }
                            ),
                            "entity_ids": sorted(component),
                            "relation": relation,
                        }
                    )
            active_paths = paths
            active_entity_ids = entity_ids
        else:
            raw_loads = profile.get("loads", [])
            if not isinstance(raw_loads, list) or not raw_loads:
                missing_inputs.append("load selectors")
            else:
                for index, value in enumerate(raw_loads):
                    if not isinstance(value, dict) or not value.get("pattern"):
                        missing_inputs.append(f"load[{index}]: pattern")
                        continue
                    pattern = str(value["pattern"])
                    name = str(value.get("name") or pattern)
                    matched_parts = [
                        part
                        for part in parts
                        if _matches(f"{part.name} {part.path}", [pattern])
                    ]
                    if not matched_parts:
                        missing_inputs.append(f"{name}: matched load parts")
                        continue
                    matched_paths = sorted({part.path for part in matched_parts})
                    matched_entities = sorted(
                        entity.entity_id
                        for entity in entities
                        if entity.part.path in matched_paths
                    )
                    load_paths.extend(matched_paths)
                    load_requirements.append(
                        {
                            "name": name,
                            "pattern": pattern,
                            "part_paths": matched_paths,
                            "entity_ids": matched_entities,
                        }
                    )

            if entities:
                minimum_z = min(entity.bounds[0][2] for entity in entities)
                maximum_z = max(entity.bounds[1][2] for entity in entities)
                height = max(maximum_z - minimum_z, 1e-9)
                support_tolerance = max(height * 1e-6, tolerance)
                support_entity_ids = sorted(
                    entity.entity_id
                    for entity in entities
                    if entity.bounds[0][2] <= minimum_z + support_tolerance
                )
                support_paths = sorted(
                    {entity_by_id[entity_id].part.path for entity_id in support_entity_ids}
                )
            if not support_entity_ids:
                missing_inputs.append("support solids")

            if support_entity_ids and load_requirements:
                support_entities = {
                    entity_id
                    for support_entity_id in support_entity_ids
                    for entity_id in component_for[support_entity_id]
                }
                active_components: set[tuple[str, ...]] = set()
                for requirement in load_requirements:
                    disconnected = sorted(
                        entity_id
                        for entity_id in requirement["entity_ids"]
                        if not (component_for[entity_id] & set(support_entity_ids))
                    )
                    if disconnected:
                        disconnected_component = {
                            member
                            for entity_id in disconnected
                            for member in component_for[entity_id]
                        }
                        violations.append(
                            {
                                "code": "LOAD_PATH_DISCONNECTED",
                                "message": (
                                    f"{requirement['name']}: {len(disconnected)} load "
                                    "solid(s) have no bonded path to support"
                                ),
                                "load_name": requirement["name"],
                                "load_pattern": requirement["pattern"],
                                "load_part_paths": sorted(
                                    {
                                        entity_by_id[entity_id].part.path
                                        for entity_id in disconnected
                                    }
                                ),
                                "load_entity_ids": disconnected,
                                "disconnected_component_paths": sorted(
                                    {
                                        entity_by_id[entity_id].part.path
                                        for entity_id in disconnected_component
                                    }
                                ),
                                "disconnected_entity_ids": sorted(disconnected_component),
                                "support_part_paths": support_paths,
                                "support_entity_ids": support_entity_ids,
                                "relation": _nearest_between(
                                    contacts,
                                    set(disconnected),
                                    support_entities,
                                ),
                            }
                        )
                    else:
                        active_components.update(
                            tuple(sorted(component_for[entity_id]))
                            for entity_id in requirement["entity_ids"]
                        )

                if not violations and len(active_components) == 1:
                    active_entity_ids = list(next(iter(active_components)))
                    active_paths = sorted(
                        {
                            entity_by_id[entity_id].part.path
                            for entity_id in active_entity_ids
                        }
                    )
                elif not violations and len(active_components) > 1:
                    missing_inputs.append(
                        "multiple support-connected load-bearing components are "
                        "unsupported by the current FEA path"
                    )
                    violations.append(
                        {
                            "code": "UNSUPPORTED_MULTIPLE_LOAD_COMPONENTS",
                            "message": missing_inputs[-1],
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
            "entity_count": len(entities),
            "parts": [
                {
                    "semantic_path": part.path,
                    "name": part.name,
                    "feature_id": part.feature_id,
                    "source_ids": part.source_ids,
                    "source_locations": part.source_locations,
                    "bounds_m": part.bounds,
                    "internal_volume_count": len(part.volumes),
                    "entity_ids": [
                        f"{part.path}#solid:{index}"
                        for index in range(len(part.volumes))
                    ],
                }
                for part in parts
            ],
            "contacts": contacts,
            "weak_contacts": weak_contacts,
            "components": components,
            "entity_components": entity_components,
            "component_count": len(entity_components),
            "active_part_paths": sorted(active_paths),
            "active_entity_ids": sorted(active_entity_ids),
            "support_part_paths": support_paths,
            "support_entity_ids": support_entity_ids,
            "load_requirements": load_requirements,
            "missing_inputs": missing_inputs,
            "violations": violations,
            "assumptions": {
                "connection_rule": (
                    "final OCC solid entities are graph nodes; pairwise fuse must "
                    "produce one volume, while point/edge contact is insufficient"
                ),
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
    entity_ids: Iterable[str] | None = None,
) -> dict[str, Any]:
    import gmsh

    selected_parts = set(map(str, part_paths))
    selected_entities = (
        None if entity_ids is None else set(map(str, entity_ids))
    )
    destination = Path(output)
    destination.parent.mkdir(parents=True, exist_ok=True)
    gmsh.initialize()
    try:
        gmsh.option.setNumber("General.Terminal", 0)
        gmsh.model.add("adsl_fea")
        nodes = [
            node
            for node in _root_parts(manifest["root"])
            if str(node.get("semantic_path")) in selected_parts
        ]
        parts = [_part(gmsh, node, scale) for node in nodes]
        available = {
            f"{part.path}#solid:{index}": tag
            for part in parts
            for index, tag in enumerate(part.volumes)
        }
        if selected_entities is None:
            selected_entities = set(available)
        missing = selected_entities - set(available)
        if missing:
            raise TopologyAnalysisError(
                f"FEA entity selection is unavailable: {sorted(missing)}"
            )
        fused = _fuse(
            gmsh,
            [available[entity_id] for entity_id in sorted(selected_entities)],
            part=" + ".join(sorted(selected_entities)),
        )
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
            "invalid_jacobian_element_ids": np.concatenate(element_tags)[
                ~np.isfinite(quality) | (np.asarray(quality) <= 0)
            ].astype(int).tolist(),
            "mesh_size_m": float(mesh_size),
            "gmsh_version": gmsh.option.getString("General.Version"),
            "active_part_paths": sorted(selected_parts),
            "active_entity_ids": sorted(selected_entities),
        }
    finally:
        gmsh.finalize()


__all__ = [
    "TopologyAnalysisError",
    "analyze_manifest",
    "build_fused_mesh",
    "load_manifest",
]
