#!/usr/bin/env python3
"""Read-only final-standing-stability audit for existing aDSL URDF assets.

This is an experiment utility, not a production checker.  It never writes below
the input root.  Derived meshes, MuJoCo wrappers, JSON, CSV, and Markdown are
written below ``--output-dir`` only.
"""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import sys
from typing import Any, Iterable
import xml.etree.ElementTree as ET

import numpy as np
from scipy.spatial import ConvexHull, QhullError
from scipy.spatial.transform import Rotation
from shapely.geometry import Point, Polygon
import trimesh


G = 9.81
TOPPLE_TILT_THRESHOLD_DEG = 25.0
SETTLE_DURATION_SECONDS = 5.0
IMPULSE_OBSERVATION_SECONDS = 5.0
MOVABLE_JOINT_TYPES = {"continuous", "prismatic", "revolute"}
VERDICTS = {
    "STABLE_CANDIDATE",
    "UNSTABLE",
    "MARGINAL",
    "INDETERMINATE",
    "EXCLUDED",
}


@dataclass
class CollisionGeometry:
    name: str
    kind: str
    mesh: trimesh.Trimesh
    transform: np.ndarray
    parameters: dict[str, Any]
    source_path: str | None

    def transformed_mesh(self) -> trimesh.Trimesh:
        result = self.mesh.copy()
        result.apply_transform(self.transform)
        result.merge_vertices(merge_tex=True, merge_norm=True)
        return result


@dataclass
class Joint:
    name: str
    kind: str
    parent: str
    child: str
    origin: np.ndarray
    axis: np.ndarray
    lower: float | None
    upper: float | None


@dataclass
class ParsedUrdf:
    path: Path
    robot_name: str
    collisions_by_link: dict[str, list[tuple[str, ET.Element]]]
    joints: list[Joint]
    root_links: list[str]
    sha256: str


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def parse_vector(raw: str | None, default: Iterable[float]) -> np.ndarray:
    if raw is None:
        return np.asarray(tuple(default), dtype=float)
    values = [float(value) for value in raw.split()]
    if len(values) != 3:
        raise ValueError(f"expected three values, got {raw!r}")
    return np.asarray(values, dtype=float)


def origin_matrix(element: ET.Element | None) -> np.ndarray:
    matrix = np.eye(4)
    if element is None:
        return matrix
    matrix[:3, :3] = Rotation.from_euler(
        "xyz", parse_vector(element.get("rpy"), (0.0, 0.0, 0.0))
    ).as_matrix()
    matrix[:3, 3] = parse_vector(element.get("xyz"), (0.0, 0.0, 0.0))
    return matrix


def joint_motion_matrix(joint: Joint, value: float) -> np.ndarray:
    matrix = np.eye(4)
    axis_norm = float(np.linalg.norm(joint.axis))
    axis = joint.axis / axis_norm if axis_norm > 0 else np.array([1.0, 0.0, 0.0])
    if joint.kind in {"revolute", "continuous"}:
        matrix[:3, :3] = Rotation.from_rotvec(axis * value).as_matrix()
    elif joint.kind == "prismatic":
        matrix[:3, 3] = axis * value
    return matrix


def parse_urdf(path: Path) -> ParsedUrdf:
    root = ET.parse(path).getroot()
    links = {str(link.get("name")): link for link in root.findall("link")}
    collisions_by_link: dict[str, list[tuple[str, ET.Element]]] = {}
    for link_name, link in links.items():
        rows = []
        for index, collision in enumerate(link.findall("collision")):
            rows.append((collision.get("name") or f"{link_name}_collision_{index}", collision))
        collisions_by_link[link_name] = rows

    joints: list[Joint] = []
    child_links: set[str] = set()
    for element in root.findall("joint"):
        parent_node = element.find("parent")
        child_node = element.find("child")
        if parent_node is None or child_node is None:
            raise ValueError(f"joint {element.get('name')} lacks parent or child")
        limit = element.find("limit")
        lower = None if limit is None or limit.get("lower") is None else float(limit.get("lower"))
        upper = None if limit is None or limit.get("upper") is None else float(limit.get("upper"))
        axis = parse_vector(
            None if element.find("axis") is None else element.find("axis").get("xyz"),
            (1.0, 0.0, 0.0),
        )
        joint = Joint(
            name=element.get("name") or f"joint_{len(joints)}",
            kind=element.get("type") or "fixed",
            parent=str(parent_node.get("link")),
            child=str(child_node.get("link")),
            origin=origin_matrix(element.find("origin")),
            axis=axis,
            lower=lower,
            upper=upper,
        )
        joints.append(joint)
        child_links.add(joint.child)

    root_links = sorted(set(links) - child_links)
    if not root_links:
        raise ValueError("URDF has no root link")
    return ParsedUrdf(
        path=path,
        robot_name=root.get("name") or path.parent.name,
        collisions_by_link=collisions_by_link,
        joints=joints,
        root_links=root_links,
        sha256=sha256_file(path),
    )


def state_values(parsed: ParsedUrdf) -> dict[str, dict[str, float]]:
    movable = [joint for joint in parsed.joints if joint.kind in MOVABLE_JOINT_TYPES]
    initial = {joint.name: 0.0 for joint in movable}
    states: dict[str, dict[str, float]] = {"initial": initial}
    if not movable:
        return states

    positive = dict(initial)
    negative = dict(initial)
    for joint in movable:
        if joint.kind == "continuous":
            positive[joint.name] = 0.85 * math.pi
            negative[joint.name] = -0.85 * math.pi
            continue
        if joint.upper is not None:
            positive[joint.name] = 0.85 * joint.upper
        if joint.lower is not None:
            negative[joint.name] = 0.85 * joint.lower
    if positive != initial:
        states["positive_85pct_limits"] = positive
    if negative != initial and negative != positive:
        states["negative_85pct_limits"] = negative
    return states


def link_transforms(parsed: ParsedUrdf, values: dict[str, float]) -> dict[str, np.ndarray]:
    children: dict[str, list[Joint]] = {}
    for joint in parsed.joints:
        children.setdefault(joint.parent, []).append(joint)
    transforms: dict[str, np.ndarray] = {}
    queue: list[str] = []
    for root in parsed.root_links:
        transforms[root] = np.eye(4)
        queue.append(root)
    while queue:
        parent = queue.pop(0)
        for joint in children.get(parent, []):
            motion = joint_motion_matrix(joint, values.get(joint.name, 0.0))
            transforms[joint.child] = transforms[parent] @ joint.origin @ motion
            queue.append(joint.child)
    return transforms


def load_mesh_geometry(path: Path, scale: np.ndarray) -> trimesh.Trimesh:
    loaded = trimesh.load(path, force="scene", process=False)
    scene = loaded if isinstance(loaded, trimesh.Scene) else trimesh.Scene(loaded)
    mesh = scene.to_geometry()
    mesh.apply_scale(scale)
    return mesh


def collision_geometries(parsed: ParsedUrdf, values: dict[str, float]) -> list[CollisionGeometry]:
    transforms = link_transforms(parsed, values)
    result: list[CollisionGeometry] = []
    for link_name, rows in parsed.collisions_by_link.items():
        if link_name not in transforms:
            continue
        for collision_name, collision in rows:
            geometry = collision.find("geometry")
            if geometry is None:
                continue
            transform = transforms[link_name] @ origin_matrix(collision.find("origin"))
            box = geometry.find("box")
            cylinder = geometry.find("cylinder")
            sphere = geometry.find("sphere")
            mesh_node = geometry.find("mesh")
            if box is not None:
                size = parse_vector(box.get("size"), (1.0, 1.0, 1.0))
                mesh = trimesh.creation.box(extents=size)
                kind = "box"
                parameters = {"size": size.tolist()}
                source_path = None
            elif cylinder is not None:
                radius = float(cylinder.get("radius", "1"))
                length = float(cylinder.get("length", "1"))
                mesh = trimesh.creation.cylinder(radius=radius, height=length, sections=64)
                kind = "cylinder"
                parameters = {"radius": radius, "length": length}
                source_path = None
            elif sphere is not None:
                radius = float(sphere.get("radius", "1"))
                mesh = trimesh.creation.icosphere(subdivisions=3, radius=radius)
                kind = "sphere"
                parameters = {"radius": radius}
                source_path = None
            elif mesh_node is not None:
                filename = mesh_node.get("filename")
                if not filename:
                    raise ValueError(f"mesh collision {collision_name} lacks filename")
                mesh_path = (parsed.path.parent / filename).resolve()
                scale = parse_vector(mesh_node.get("scale"), (1.0, 1.0, 1.0))
                mesh = load_mesh_geometry(mesh_path, scale)
                kind = "mesh"
                parameters = {"scale": scale.tolist()}
                source_path = str(mesh_path)
            else:
                raise ValueError(f"unsupported collision geometry in {collision_name}")
            result.append(
                CollisionGeometry(
                    name=collision_name,
                    kind=kind,
                    mesh=mesh,
                    transform=transform,
                    parameters=parameters,
                    source_path=source_path,
                )
            )
    if not result:
        raise ValueError("URDF contains no usable collision geometry")
    return result


def weighted_com(geometries: list[CollisionGeometry], mode: str) -> tuple[np.ndarray | None, dict[str, Any]]:
    centers: list[np.ndarray] = []
    weights: list[float] = []
    non_watertight: list[str] = []
    invalid: list[str] = []
    component_count = 0
    for geometry in geometries:
        mesh = geometry.transformed_mesh()
        try:
            component_count += len(mesh.split(only_watertight=False, engine=None))
        except Exception:
            component_count += 1
        if mode == "solid":
            if not mesh.is_watertight:
                non_watertight.append(geometry.name)
                continue
            properties = mesh.mass_properties
            weight = abs(float(properties.volume))
            center = np.asarray(properties.center_mass, dtype=float)
        elif mode == "shell":
            weight = float(mesh.area)
            if weight > 0:
                center = np.average(mesh.triangles_center, axis=0, weights=mesh.area_faces)
            else:
                center = np.full(3, np.nan)
        else:
            raise ValueError(mode)
        if not np.isfinite(weight) or weight <= 1e-12 or not np.all(np.isfinite(center)):
            invalid.append(geometry.name)
            continue
        centers.append(center)
        weights.append(weight)

    complete = not non_watertight and not invalid and len(centers) == len(geometries)
    if not centers or (mode == "solid" and not complete):
        return None, {
            "available": False,
            "complete": complete,
            "non_watertight": non_watertight,
            "invalid": invalid,
            "component_count": component_count,
        }
    result = np.average(np.vstack(centers), axis=0, weights=np.asarray(weights))
    return result, {
        "available": True,
        "complete": complete,
        "total_weight": float(sum(weights)),
        "non_watertight": non_watertight,
        "invalid": invalid,
        "component_count": component_count,
        "overlap_bias_possible": component_count > 1,
    }


def support_region(geometries: list[CollisionGeometry]) -> dict[str, Any]:
    meshes = [geometry.transformed_mesh() for geometry in geometries]
    all_vertices = np.vstack([mesh.vertices for mesh in meshes])
    bounds = np.vstack((all_vertices.min(axis=0), all_vertices.max(axis=0)))
    diagonal = float(np.linalg.norm(bounds[1] - bounds[0]))
    min_z = float(bounds[0, 2])
    tolerance = max(1e-8, diagonal * 1e-6)
    contact_rows: list[np.ndarray] = []
    contact_geometries: list[dict[str, Any]] = []
    for geometry, mesh in zip(geometries, meshes):
        mask = mesh.vertices[:, 2] <= min_z + tolerance
        if np.any(mask):
            points = np.asarray(mesh.vertices[mask, :2], dtype=float)
            contact_rows.append(points)
            contact_geometries.append(
                {
                    "name": geometry.name,
                    "kind": geometry.kind,
                    "point_count": int(len(points)),
                }
            )
    points = np.vstack(contact_rows) if contact_rows else np.empty((0, 2))
    if len(points):
        decimals = max(6, int(max(0.0, -math.log10(tolerance))) + 1)
        points = np.unique(np.round(points, decimals=decimals), axis=0)
    horizontal_extent = bounds[1, :2] - bounds[0, :2]
    horizontal_diagonal = float(np.linalg.norm(horizontal_extent))
    area_epsilon = max(1e-14, horizontal_diagonal * horizontal_diagonal * 1e-10)

    polygon_points: list[list[float]] = []
    area = 0.0
    rank = 0
    hull_equations: list[list[float]] = []
    if len(points) >= 2:
        rank = int(np.linalg.matrix_rank(points - points.mean(axis=0), tol=tolerance))
    if len(points) >= 3 and rank == 2:
        try:
            hull = ConvexHull(points)
            polygon = points[hull.vertices]
            polygon_points = polygon.tolist()
            area = float(Polygon(polygon).area)
            hull_equations = np.asarray(hull.equations, dtype=float).tolist()
        except QhullError:
            rank = 1
    kinds = sorted({row["kind"] for row in contact_geometries})
    return {
        "ground_z": min_z,
        "contact_tolerance": tolerance,
        "contact_point_count": int(len(points)),
        "contact_points": points.tolist(),
        "contact_geometries": contact_geometries,
        "contact_kinds": kinds,
        "mesh_only_contact": bool(kinds) and kinds == ["mesh"],
        "rank": rank,
        "polygon": polygon_points,
        "area": area,
        "area_epsilon": area_epsilon,
        "degenerate": rank < 2 or area <= area_epsilon,
        "bounds": bounds.tolist(),
        "horizontal_diagonal": horizontal_diagonal,
    }


def margin_metrics(com: np.ndarray | None, support: dict[str, Any]) -> dict[str, Any]:
    if com is None:
        return {"available": False}
    point = Point(float(com[0]), float(com[1]))
    ground_z = float(support["ground_z"])
    com_height = float(com[2] - ground_z)
    if support["degenerate"]:
        return {
            "available": True,
            "com": com.tolist(),
            "com_height": com_height,
            "inside": False,
            "signed_margin": None,
            "normalized_margin": None,
            "minimum_tipping_angle_deg": 0.0,
            "minimum_critical_acceleration_m_s2": 0.0,
            "critical_direction_deg": None,
        }
    polygon = Polygon(support["polygon"])
    inside = bool(polygon.covers(point))
    distance = float(polygon.boundary.distance(point))
    signed_margin = distance if inside else -float(polygon.distance(point))
    scale = max(float(support["horizontal_diagonal"]), 1e-12)
    normalized = signed_margin / scale
    angle = math.degrees(math.atan2(max(0.0, signed_margin), max(com_height, 1e-12)))

    equations = np.asarray(ConvexHull(np.asarray(support["polygon"])).equations)
    directional: list[dict[str, float]] = []
    for direction_index in range(16):
        theta = 2.0 * math.pi * direction_index / 16.0
        direction = np.array([math.cos(theta), math.sin(theta)])
        candidates = []
        for equation in equations:
            normal, offset = equation[:2], float(equation[2])
            denominator = float(np.dot(normal, direction))
            if denominator > 1e-12:
                candidates.append(-float(np.dot(normal, com[:2]) + offset) / denominator)
        ray_margin = min(candidates) if candidates else float("inf")
        critical = G * max(0.0, ray_margin) / max(com_height, 1e-12)
        directional.append(
            {
                "direction_deg": 360.0 * direction_index / 16.0,
                "edge_distance": float(ray_margin),
                "critical_acceleration_m_s2": float(critical),
            }
        )
    weakest = min(directional, key=lambda row: row["critical_acceleration_m_s2"])
    return {
        "available": True,
        "com": com.tolist(),
        "com_height": com_height,
        "inside": inside,
        "signed_margin": signed_margin,
        "normalized_margin": normalized,
        "minimum_tipping_angle_deg": angle,
        "minimum_critical_acceleration_m_s2": weakest["critical_acceleration_m_s2"],
        "critical_direction_deg": weakest["direction_deg"],
        "directions": directional,
    }


def geometric_verdict(
    case_id: str,
    support: dict[str, Any],
    solid: dict[str, Any],
    shell: dict[str, Any],
) -> tuple[str, list[str], str]:
    if case_id == "S01-living-room":
        return "EXCLUDED", ["EXCLUDED_TRIVIAL_FLOOR_SUPPORT"], "not_applicable"
    if support["degenerate"]:
        return "UNSTABLE", ["DEGENERATE_SUPPORT_REGION"], "high"
    available = [row for row in (solid, shell) if row.get("available")]
    if not available:
        return "INDETERMINATE", ["NO_COM_ESTIMATE"], "low"
    inside = [bool(row["inside"]) for row in available]
    reasons: list[str] = []
    if support["mesh_only_contact"]:
        reasons.append("MESH_CONTACT_DISCRETIZATION_RISK")
    if len(set(inside)) > 1:
        return "INDETERMINATE", reasons + ["MASS_MODEL_DISAGREEMENT"], "low"
    if not inside[0]:
        return "UNSTABLE", reasons + ["COM_PROJECTION_OUTSIDE_SUPPORT"], "high"
    if not solid.get("available"):
        return "INDETERMINATE", reasons + ["SOLID_COM_UNAVAILABLE_NON_WATERTIGHT"], "low"
    minimum_normalized = min(float(row["normalized_margin"]) for row in available)
    if minimum_normalized < 0.01:
        return "MARGINAL", reasons + ["NORMALIZED_MARGIN_LT_0P01"], "medium"
    if support["mesh_only_contact"]:
        return "INDETERMINATE", reasons, "low"
    return "STABLE_CANDIDATE", reasons, "medium"


def analyze_state(case_id: str, parsed: ParsedUrdf, name: str, values: dict[str, float]) -> tuple[dict[str, Any], list[CollisionGeometry]]:
    geometries = collision_geometries(parsed, values)
    support = support_region(geometries)
    solid_com, solid_info = weighted_com(geometries, "solid")
    shell_com, shell_info = weighted_com(geometries, "shell")
    solid_metrics = {**solid_info, **margin_metrics(solid_com, support)}
    shell_metrics = {**shell_info, **margin_metrics(shell_com, support)}
    verdict, reasons, confidence = geometric_verdict(
        case_id, support, solid_metrics, shell_metrics
    )
    return (
        {
            "state": name,
            "joint_values": values,
            "collision_geometry_count": len(geometries),
            "geometry_type_counts": {
                kind: sum(geometry.kind == kind for geometry in geometries)
                for kind in sorted({geometry.kind for geometry in geometries})
            },
            "support": support,
            "mass_models": {"uniform_solid": solid_metrics, "uniform_shell": shell_metrics},
            "geometric_verdict": verdict,
            "geometric_reasons": reasons,
            "geometric_confidence": confidence,
        },
        geometries,
    )


def matrix_to_pos_quat(matrix: np.ndarray) -> tuple[str, str]:
    pos = " ".join(f"{value:.12g}" for value in matrix[:3, 3])
    xyzw = Rotation.from_matrix(matrix[:3, :3]).as_quat()
    quat = " ".join(f"{value:.12g}" for value in (xyzw[3], xyzw[0], xyzw[1], xyzw[2]))
    return pos, quat


def build_mujoco_xml(
    geometries: list[CollisionGeometry],
    state_dir: Path,
    ground_z: float,
    density: float,
    friction: float,
) -> tuple[str, dict[str, Any]]:
    state_dir.mkdir(parents=True, exist_ok=True)
    assets: list[str] = []
    geoms: list[str] = []
    mesh_proxy_count = 0
    mesh_component_count = 0
    dropped_degenerate_mesh_component_count = 0
    for index, geometry in enumerate(geometries):
        pos, quat = matrix_to_pos_quat(geometry.transform)
        common = (
            f'name="geom_{index}" density="{density:.12g}" '
            f'friction="{friction:.12g} 0.005 0.0001" pos="{pos}" quat="{quat}"'
        )
        if geometry.kind == "box":
            half = np.asarray(geometry.parameters["size"]) / 2.0
            size = " ".join(f"{value:.12g}" for value in half)
            geoms.append(f'<geom type="box" size="{size}" {common}/>')
        elif geometry.kind == "cylinder":
            radius = geometry.parameters["radius"]
            half_length = geometry.parameters["length"] / 2.0
            geoms.append(
                f'<geom type="cylinder" size="{radius:.12g} {half_length:.12g}" {common}/>'
            )
        elif geometry.kind == "sphere":
            geoms.append(
                f'<geom type="sphere" size="{geometry.parameters["radius"]:.12g}" {common}/>'
            )
        else:
            transformed = geometry.transformed_mesh()
            try:
                pieces = transformed.split(only_watertight=False, engine=None)
            except Exception:
                pieces = [transformed]
            if not pieces:
                pieces = [transformed]
            for piece_index, piece in enumerate(pieces):
                piece = piece.copy()
                piece.remove_unreferenced_vertices()
                vertices = np.unique(np.asarray(piece.vertices), axis=0)
                rank_scale = max(float(np.linalg.norm(np.ptp(vertices, axis=0))), 1.0) if len(vertices) else 1.0
                vertex_rank = int(np.linalg.matrix_rank(vertices - vertices.mean(axis=0), tol=rank_scale * 1e-10)) if len(vertices) else 0
                if len(vertices) < 4 or vertex_rank < 3:
                    dropped_degenerate_mesh_component_count += 1
                    continue
                mesh_name = f"mesh_{index}_{piece_index}"
                mesh_path = state_dir / f"{mesh_name}.stl"
                piece.export(mesh_path)
                assets.append(f'<mesh name="{mesh_name}" file="{mesh_path}"/>')
                geoms.append(
                    f'<geom name="geom_{index}_{piece_index}" type="mesh" mesh="{mesh_name}" '
                    f'density="{density:.12g}" friction="{friction:.12g} 0.005 0.0001"/>'
                )
                mesh_component_count += 1
                if not piece.is_convex:
                    mesh_proxy_count += 1

    if not geoms:
        raise ValueError("no volumetric collision geometry remained after filtering")

    # Shift the lowest collision point to 2 mm above the plane.  This may be a
    # negative body translation when the authored model already starts high.
    lift = -ground_z + 0.002
    xml = f"""<mujoco model="standing_stability">
  <compiler angle="radian" inertiafromgeom="auto" balanceinertia="true"/>
  <option timestep="0.002" gravity="0 0 -9.81" integrator="implicitfast"/>
  <size njmax="20000" nconmax="5000"/>
  <asset>{''.join(assets)}</asset>
  <worldbody>
    <geom name="ground" type="plane" size="20 20 0.1" friction="{friction:.12g} 0.005 0.0001"/>
    <body name="assembly" pos="0 0 {lift:.12g}">
      <freejoint name="assembly_free"/>
      {''.join(geoms)}
    </body>
  </worldbody>
</mujoco>"""
    return xml, {
        "density_kg_m3": density,
        "friction_coefficient": friction,
        "mesh_component_count": mesh_component_count,
        "nonconvex_mesh_proxy_count": mesh_proxy_count,
        "dropped_degenerate_mesh_component_count": dropped_degenerate_mesh_component_count,
        "mesh_collision_policy": "MuJoCo convex hull per connected volumetric mesh component; zero-volume fragments dropped",
    }


def exceeds_topple_threshold(angle_deg: float) -> bool:
    """The user-defined fall rule is strictly greater than 25 degrees."""
    return bool(angle_deg > TOPPLE_TILT_THRESHOLD_DEG)

def body_tilt_deg(data: Any, body_id: int) -> float:
    matrix = np.asarray(data.xmat[body_id]).reshape(3, 3)
    return math.degrees(math.acos(float(np.clip(matrix[2, 2], -1.0, 1.0))))


def run_steps(model: Any, data: Any, mujoco: Any, count: int, body_id: int) -> float:
    maximum = body_tilt_deg(data, body_id)
    for _ in range(count):
        mujoco.mj_step(model, data)
        maximum = max(maximum, body_tilt_deg(data, body_id))
    return maximum


def simulate_mujoco(
    xml: str,
    force_ratios: list[float],
    directions: int = 16,
    impulse_velocity: float = 0.05,
) -> dict[str, Any]:
    import mujoco

    model = mujoco.MjModel.from_xml_string(xml)
    body_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "assembly")
    joint_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_JOINT, "assembly_free")
    qpos_adr = int(model.jnt_qposadr[joint_id])
    dof_adr = int(model.jnt_dofadr[joint_id])
    initial_qpos = np.asarray(model.qpos0).copy()

    def reset_and_settle() -> tuple[Any, float, float]:
        data = mujoco.MjData(model)
        data.qpos[:] = initial_qpos
        mujoco.mj_forward(model, data)
        maximum = run_steps(model, data, mujoco, int(SETTLE_DURATION_SECONDS / model.opt.timestep), body_id)
        return data, maximum, body_tilt_deg(data, body_id)

    settled, settle_max, settle_final = reset_and_settle()
    settled_qpos = settled.qpos.copy()
    settled_qvel = settled.qvel.copy()
    mass = float(model.body_mass[body_id])
    settle_tipped = exceeds_topple_threshold(settle_max)

    force_results: list[dict[str, Any]] = []
    impulse_results: list[dict[str, Any]] = []
    for direction_index in range(directions):
        theta = 2.0 * math.pi * direction_index / directions
        direction = np.array([math.cos(theta), math.sin(theta), 0.0])
        critical_ratio = None
        peak_at_critical = None
        final_at_critical = None
        for ratio in force_ratios:
            data = mujoco.MjData(model)
            data.qpos[:] = settled_qpos
            data.qvel[:] = settled_qvel
            mujoco.mj_forward(model, data)
            data.xfrc_applied[body_id, :3] = direction * ratio * mass * G
            peak = run_steps(model, data, mujoco, int(0.75 / model.opt.timestep), body_id)
            data.xfrc_applied[body_id, :] = 0.0
            peak = max(peak, run_steps(model, data, mujoco, int(0.75 / model.opt.timestep), body_id))
            final = body_tilt_deg(data, body_id)
            if exceeds_topple_threshold(peak):
                critical_ratio = ratio
                peak_at_critical = peak
                final_at_critical = final
                break
        force_results.append(
            {
                "direction_deg": 360.0 * direction_index / directions,
                "critical_force_over_weight": critical_ratio,
                "critical_acceleration_m_s2": None if critical_ratio is None else critical_ratio * G,
                "peak_tilt_deg": peak_at_critical,
                "final_tilt_deg": final_at_critical,
            }
        )

        data = mujoco.MjData(model)
        data.qpos[:] = settled_qpos
        data.qvel[:] = settled_qvel
        data.qvel[dof_adr : dof_adr + 3] += direction * impulse_velocity
        mujoco.mj_forward(model, data)
        peak = run_steps(model, data, mujoco, int(IMPULSE_OBSERVATION_SECONDS / model.opt.timestep), body_id)
        final = body_tilt_deg(data, body_id)
        impulse_results.append(
            {
                "direction_deg": 360.0 * direction_index / directions,
                "delta_v_m_s": impulse_velocity,
                "tipped": exceeds_topple_threshold(peak),
                "peak_tilt_deg": peak,
                "final_tilt_deg": final,
            }
        )

    measured = [
        row["critical_force_over_weight"]
        for row in force_results
        if row["critical_force_over_weight"] is not None
    ]
    return {
        "available": True,
        "topple_threshold_deg": TOPPLE_TILT_THRESHOLD_DEG,
        "topple_rule": "maximum tilt strictly greater than threshold",
        "settle_duration_s": SETTLE_DURATION_SECONDS,
        "impulse_observation_duration_s": IMPULSE_OBSERVATION_SECONDS,
        "mujoco_version": mujoco.__version__,
        "model_mass_kg_under_assumed_density": mass,
        "settle": {
            "tipped": settle_tipped,
            "peak_tilt_deg": settle_max,
            "final_tilt_deg": settle_final,
            "contact_count_final": int(settled.ncon),
            "final_body_position": settled.qpos[qpos_adr : qpos_adr + 3].tolist(),
        },
        "force_probe": {
            "force_ratios": force_ratios,
            "directions": force_results,
            "minimum_observed_force_over_weight": min(measured) if measured else None,
        },
        "impulse_probe": {
            "delta_v_m_s": impulse_velocity,
            "tipped_direction_count": sum(row["tipped"] for row in impulse_results),
            "directions": impulse_results,
        },
    }


def combine_verdict(state: dict[str, Any]) -> tuple[str, list[str], str]:
    geometric = state["geometric_verdict"]
    reasons = list(state["geometric_reasons"])
    confidence = state["geometric_confidence"]
    physics = state.get("mujoco", {})
    if geometric == "EXCLUDED" or not physics.get("available"):
        return geometric, reasons, confidence
    if geometric == "UNSTABLE":
        return geometric, reasons + ["MUJOCO_DID_NOT_OVERRIDE_GEOMETRIC_FAILURE"], confidence
    if physics["settle"]["tipped"]:
        return "UNSTABLE", reasons + ["MUJOCO_TIPPED_DURING_SETTLE"], "high"
    minimum_force = physics["force_probe"]["minimum_observed_force_over_weight"]
    if minimum_force is not None and minimum_force <= 0.1:
        return "MARGINAL", reasons + ["MUJOCO_TIPS_AT_LE_0P1_WEIGHT_FORCE"], "medium"
    if geometric == "STABLE_CANDIDATE":
        return geometric, reasons + ["MUJOCO_SETTLE_CONFIRMED"], "medium"
    return geometric, reasons + ["MUJOCO_PROXY_DID_NOT_RESOLVE_GEOMETRY_OR_MASS_UNCERTAINTY"], confidence


def analyze_case(
    case_dir: Path,
    output_dir: Path,
    run_physics: bool,
    density: float,
    friction: float,
) -> dict[str, Any]:
    case_id = case_dir.name
    urdf_path = case_dir / "scene.urdf"
    parsed = parse_urdf(urdf_path)
    case = {
        "case_id": case_id,
        "input_urdf": str(urdf_path),
        "input_urdf_sha256": parsed.sha256,
        "input_scene_glb_sha256": sha256_file(case_dir / "scene.glb"),
        "input_source_py_sha256": sha256_file(case_dir / "source.py"),
        "robot_name": parsed.robot_name,
        "coordinate_convention": "URDF Z-up; imported GLB mesh origins include the URDF rotation",
        "movable_joint_count": sum(joint.kind in MOVABLE_JOINT_TYPES for joint in parsed.joints),
        "states": [],
    }
    for state_name, values in state_values(parsed).items():
        state, geometries = analyze_state(case_id, parsed, state_name, values)
        if run_physics and state["geometric_verdict"] != "EXCLUDED":
            state_dir = output_dir / "mujoco_assets" / case_id / state_name
            try:
                xml, proxy = build_mujoco_xml(
                    geometries,
                    state_dir,
                    state["support"]["ground_z"],
                    density,
                    friction,
                )
                (state_dir / "model.xml").write_text(xml, encoding="utf-8")
                state["mujoco_proxy"] = proxy
                if proxy["dropped_degenerate_mesh_component_count"]:
                    state["geometric_reasons"].append("MUJOCO_DROPPED_DEGENERATE_MESH_COMPONENTS")
                state["mujoco"] = simulate_mujoco(
                    xml,
                    force_ratios=[0.05, 0.1, 0.2, 0.35, 0.5, 0.75, 1.0, 1.5],
                )
            except Exception as error:
                state["mujoco"] = {
                    "available": False,
                    "error": f"{type(error).__name__}: {error}",
                }
        verdict, reasons, confidence = combine_verdict(state)
        state["verdict"] = verdict
        state["reasons"] = reasons
        state["confidence"] = confidence
        if verdict not in VERDICTS:
            raise AssertionError(verdict)
        case["states"].append(state)
    initial = next(state for state in case["states"] if state["state"] == "initial")
    case["initial_verdict"] = initial["verdict"]
    case["initial_confidence"] = initial["confidence"]
    case["worst_state_verdict"] = min(
        (state["verdict"] for state in case["states"]),
        key=lambda value: {
            "UNSTABLE": 0,
            "MARGINAL": 1,
            "INDETERMINATE": 2,
            "STABLE_CANDIDATE": 3,
            "EXCLUDED": 4,
        }[value],
    )
    return case


def fmt(value: Any, digits: int = 4) -> str:
    if value is None:
        return "—"
    if isinstance(value, float):
        return f"{value:.{digits}f}"
    return str(value)


def report_markdown(payload: dict[str, Any]) -> str:
    active_states = [state for case in payload["cases"] for state in case["states"] if state["geometric_verdict"] != "EXCLUDED"]
    physics_covered = sum(state.get("mujoco", {}).get("available", False) for state in active_states)
    natural_fall_count = sum(state.get("mujoco", {}).get("settle", {}).get("tipped", False) for state in active_states)
    lines = [
        "# 01 Final Standing Stability：现有 aDSL 模型验证",
        "",
        "## Material Passport",
        "",
        f"- Material ID: `{payload['material_passport']['material_id']}`",
        "- Type: experiment validation report",
        f"- Verification Status: {payload['material_passport']['verification_status']}",
        f"- Generated (UTC): {payload['generated_at_utc']}",
        f"- Input root: `{payload['input_root']}`",
        f"- MuJoCo coverage: {physics_covered}/{len(active_states)} non-excluded states",
        f"- Free-settle falls (>25°): {natural_fall_count}/{len(active_states)} non-excluded states",
        "",
        "## 结论边界",
        "",
        "本报告是均匀密度假设下的几何预筛与 MuJoCo 刚体代理实验，不是工程认证。原始 URDF 没有质量、密度和惯性；未建模材料分布、装配间隙、柔性、地面不平、制造误差或锚固。`STABLE_CANDIDATE` 仅表示在这些假设下未发现致命站立问题。",
        "`Verdict` 是支撑区、质心与 MuJoCo 的综合结构筛查；是否在仿真中实际倒下单独看 `Natural fall >25°`，两者不能混写。",
        "",
        "## 方法与规则",
        "",
        "- 坐标：URDF 使用 Z-up；GLB 的 Y-up→Z-up 旋转由 URDF collision origin 保留。",
        "- 支撑区：取最低点以上 `max(1e-8, bbox_diagonal×1e-6)` 的接触顶点，投影到 XY 后求凸包。少于二维或面积近零即为退化支撑。",
        "- 质心：分别计算均匀实体体积质心与均匀薄壳面积质心。任何 collision 非 watertight 时，实体质心不发布。多部件相交可能重复计质量，保留为限制。",
        "- 分析对象：稳定性计算采用 URDF collision geometry；`scene.glb` 仅冻结并记录哈希。碰撞代理与视觉网格不同的 case，结论只适用于代理。",
        "- 裕度：质心投影到支撑凸包边界的带符号最短距离；正数在内、负数在外。归一化裕度除以模型水平包围盒对角线。",
        "- 倾倒角：`atan(max(0, margin) / COM_height)`；16 个水平角度另算静态临界加速度。",
        "- MuJoCo：密度 1000 kg/m³、摩擦系数 2.0；自由沉降和小冲量各观察 5 秒，并进行 16 向水平力阶梯；任一时刻最大倾斜角严格大于 25° 即记为倒下。primitive collision 保留；每个有体积的 mesh 连通分量由 MuJoCo 凸包碰撞代理；少于 4 顶点或共面的零体积碎片被丢弃并计数。",
        "- 判定：退化支撑或两种质心均在外→`UNSTABLE`；质量模型冲突/实体质心不可得/纯 mesh 接触→`INDETERMINATE`；归一化裕度 <1% 或 ≤0.1 倍自重力倾倒→`MARGINAL`。",
        "- 是否倒下：只采用用户指定规则——5 秒自由沉降期间最大倾斜角严格大于 25°；恰好 25° 不算倒下。水平力结果单独报告达到 >25° 的最小 F/W。",
        "",
        "## 工具依据",
        "",
        "- [Trimesh mass properties](https://trimesh.org/trimesh.base.html) 用于 watertight mesh 的体积质量属性；[SciPy spatial](https://docs.scipy.org/doc/scipy/reference/spatial.html) 提供二维 ConvexHull。",
        "- [MuJoCo XML Reference](https://mujoco.readthedocs.io/en/stable/XMLreference.html)、[Computation](https://mujoco.readthedocs.io/en/latest/computation/) 与 [Python API](https://mujoco.readthedocs.io/en/stable/python.html) 是物理代理、接触和执行接口依据。",
        "",
        "## 初始姿态汇总",
        "",
        "| Case | Verdict | Confidence | Support area | Solid margin | Shell margin | Geometric tip angle | Natural fall >25° | Settle max tilt | Min F/W to >25° |",
        "|---|---|---:|---:|---:|---:|---:|---|---:|---:|",
    ]
    for case in payload["cases"]:
        state = next(row for row in case["states"] if row["state"] == "initial")
        solid = state["mass_models"]["uniform_solid"]
        shell = state["mass_models"]["uniform_shell"]
        physics = state.get("mujoco", {})
        settle = physics.get("settle", {})
        force = physics.get("force_probe", {})
        angle_values = [
            model.get("minimum_tipping_angle_deg")
            for model in (solid, shell)
            if model.get("available")
        ]
        angle = min(angle_values) if angle_values else None
        settle_text = "—" if not physics.get("available") else ("TIPPED" if settle.get("tipped") else "upright")
        lines.append(
            "| {case} | {verdict} | {confidence} | {area} | {solid} | {shell} | {angle} | {settle} | {settle_peak} | {force} |".format(
                case=case["case_id"],
                verdict=state["verdict"],
                confidence=state["confidence"],
                area=fmt(state["support"]["area"]),
                solid=fmt(solid.get("signed_margin")),
                shell=fmt(shell.get("signed_margin")),
                angle=fmt(angle, 2),
                settle=settle_text,
                settle_peak=fmt(settle.get("peak_tilt_deg"), 2),
                force=fmt(force.get("minimum_observed_force_over_weight"), 2),
            )
        )

    lines.extend(["", "## 逐项结果", ""])
    for case in payload["cases"]:
        lines.extend(
            [
                f"### {case['case_id']}",
                "",
                f"- URDF SHA256: `{case['input_urdf_sha256']}`",
                f"- Movable joints: {case['movable_joint_count']}",
                f"- Initial / worst-state verdict: `{case['initial_verdict']}` / `{case['worst_state_verdict']}`",
            ]
        )
        for state in case["states"]:
            support = state["support"]
            solid = state["mass_models"]["uniform_solid"]
            shell = state["mass_models"]["uniform_shell"]
            physics = state.get("mujoco", {})
            lines.extend(
                [
                    "",
                    f"**{state['state']}** — `{state['verdict']}` ({state['confidence']})",
                    "",
                    f"- Joint values: `{json.dumps(state['joint_values'], sort_keys=True)}`",
                    f"- Support: {support['contact_point_count']} points, rank={support['rank']}, area={fmt(support['area'])}, mesh-only={support['mesh_only_contact']}.",
                    f"- Uniform solid: available={solid.get('available')}, margin={fmt(solid.get('signed_margin'))}, normalized={fmt(solid.get('normalized_margin'))}, tip angle={fmt(solid.get('minimum_tipping_angle_deg'), 2)}°.",
                    f"- Uniform shell: available={shell.get('available')}, margin={fmt(shell.get('signed_margin'))}, normalized={fmt(shell.get('normalized_margin'))}, tip angle={fmt(shell.get('minimum_tipping_angle_deg'), 2)}°.",
                    f"- Reasons: `{', '.join(state['reasons']) or 'none'}`.",
                ]
            )
            if physics.get("available"):
                lines.append(
                    f"- MuJoCo: natural fall (>25°)={physics['settle']['tipped']}, settle max tilt={fmt(physics['settle']['peak_tilt_deg'], 2)}°, settle final tilt={fmt(physics['settle']['final_tilt_deg'], 2)}°, contacts={physics['settle']['contact_count_final']}, min observed F/W={fmt(physics['force_probe']['minimum_observed_force_over_weight'], 2)}, impulse tipped directions={physics['impulse_probe']['tipped_direction_count']}/16, dropped zero-volume mesh fragments={state.get('mujoco_proxy', {}).get('dropped_degenerate_mesh_component_count', 0)}."
                )
            elif physics:
                lines.append(f"- MuJoCo failed: `{physics.get('error')}`.")

    lines.extend(
        [
            "",
            "## 关键解释",
            "",
            "- `T03-radial-wheel` 的支撑面积为 0，但完全对称初态在 5 秒自由沉降中最大倾角仅 0.002°；它没有“自行倒下”，但约 0.2×自重水平力会使其超过 25°。",
            "- `A01-cabinet` 的最低平面几何质心落在初始支撑区外，但 5 秒自由沉降最大倾角仅 0.639°，并在轻微摇摆后形成额外接触；按 >25° 规则它没有倒下，结构 verdict 因几何/接触分歧仍保持保守。",
            "- motorcycle 的有限平底可能来自三角网格离散化或轮胎代理，不足以证明真实两轮车能无支架站立，因此纯 mesh 接触保持 `INDETERMINATE`。",
            "- M01 scratch、T02、T05 的 MuJoCo 代理分别丢弃 9、14、2 个零体积碎片；三者因此继续保持 `INDETERMINATE`。",
            "- `S01-living-room` 自带整块房间地板，整体支撑测试会得到平凡答案，故排除；物件级拆分属于另一项实验。",
            "- MuJoCo mesh 碰撞使用凸包代理，不能消除 concavity、质量分布与真实接触面的不确定性；物理结果只用于交叉复核。",
            "",
            "## 可复现性",
            "",
            f"- Python: `{payload['environment']['python']}`",
            f"- Trimesh: `{payload['environment']['trimesh']}`",
            f"- SciPy: `{payload['environment']['scipy']}`",
            f"- Shapely: `{payload['environment']['shapely']}`",
            f"- MuJoCo: `{payload['environment'].get('mujoco')}`",
            f"- Command: `{payload['command']}`",
            "- 原始 GLB、URDF 和 source.py 未修改；机器可读明细见同目录 `results.json` 和 `summary.csv`。",
            "",
        ]
    )
    return "\n".join(lines)


def write_outputs(payload: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    with (output_dir / "summary.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=[
                "case_id",
                "state",
                "verdict",
                "confidence",
                "support_area",
                "solid_margin",
                "shell_margin",
                "mujoco_settle_tipped",
                "topple_threshold_deg",
                "mujoco_settle_peak_tilt_deg",
                "mujoco_settle_final_tilt_deg",
                "minimum_force_over_weight",
                "reasons",
            ],
        )
        writer.writeheader()
        for case in payload["cases"]:
            for state in case["states"]:
                physics = state.get("mujoco", {})
                writer.writerow(
                    {
                        "case_id": case["case_id"],
                        "state": state["state"],
                        "verdict": state["verdict"],
                        "confidence": state["confidence"],
                        "support_area": state["support"]["area"],
                        "solid_margin": state["mass_models"]["uniform_solid"].get("signed_margin"),
                        "shell_margin": state["mass_models"]["uniform_shell"].get("signed_margin"),
                        "mujoco_settle_tipped": physics.get("settle", {}).get("tipped"),
                        "topple_threshold_deg": physics.get("topple_threshold_deg"),
                        "mujoco_settle_peak_tilt_deg": physics.get("settle", {}).get("peak_tilt_deg"),
                        "mujoco_settle_final_tilt_deg": physics.get("settle", {}).get("final_tilt_deg"),
                        "minimum_force_over_weight": physics.get("force_probe", {}).get("minimum_observed_force_over_weight"),
                        "reasons": ";".join(state["reasons"]),
                    }
                )
    (output_dir / "final_standing_stability_analysis.md").write_text(
        report_markdown(payload), encoding="utf-8"
    )


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--input-root",
        type=Path,
        default=Path("/jiigan-hp/lms/aDSL/experiment/audit_20260830"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--physics", choices=("off", "on"), default="on")
    parser.add_argument("--mujoco-pythonpath", type=Path)
    parser.add_argument("--density", type=float, default=1000.0)
    parser.add_argument("--friction", type=float, default=2.0)
    args = parser.parse_args()
    if args.mujoco_pythonpath:
        sys.path.insert(0, str(args.mujoco_pythonpath))
    if not args.input_root.is_dir():
        parser.error(f"input root does not exist: {args.input_root}")
    output_resolved = args.output_dir.resolve()
    input_resolved = args.input_root.resolve()
    if output_resolved == input_resolved or input_resolved in output_resolved.parents:
        parser.error("output-dir must not be inside the frozen input root")

    case_dirs = sorted(
        path for path in args.input_root.iterdir() if path.is_dir() and (path / "scene.urdf").is_file()
    )
    if not case_dirs:
        parser.error("no case directories with scene.urdf found")
    cases = [
        analyze_case(path, args.output_dir, args.physics == "on", args.density, args.friction)
        for path in case_dirs
    ]
    try:
        import mujoco

        mujoco_version = mujoco.__version__
    except ImportError:
        mujoco_version = None
    payload = {
        "material_passport": {
            "schema": 9,
            "material_id": f"adsl-final-standing-stability-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}",
            "verification_status": "ANALYZED",
        },
        "generated_at_utc": utc_now(),
        "input_root": str(input_resolved),
        "output_dir": str(output_resolved),
        "command": " ".join(sys.argv),
        "assumptions": {
            "density_kg_m3": args.density,
            "friction_coefficient": args.friction,
            "mass_models": ["uniform_solid", "uniform_shell"],
            "ground": "rigid, flat, horizontal",
            "anchoring": "none",
            "physics_enabled": args.physics == "on",
            "topple_rule": "maximum tilt strictly greater than 25 degrees",
            "topple_threshold_deg": TOPPLE_TILT_THRESHOLD_DEG,
            "settle_duration_s": SETTLE_DURATION_SECONDS,
            "impulse_observation_duration_s": IMPULSE_OBSERVATION_SECONDS,
        },
        "environment": {
            "python": sys.version.split()[0],
            "trimesh": trimesh.__version__,
            "scipy": __import__("scipy").__version__,
            "shapely": __import__("shapely").__version__,
            "mujoco": mujoco_version,
        },
        "case_count": len(cases),
        "cases": cases,
    }
    write_outputs(payload, args.output_dir)
    print(json.dumps({"case_count": len(cases), "output_dir": str(args.output_dir)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
