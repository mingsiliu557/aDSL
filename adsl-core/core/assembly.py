"""Explicit static print parts and paired rectangular interfaces (no joint solver).

Independent implementation of paired-interface/frame ideas described in Procedura
https://arxiv.org/html/2608.26238v1, §3.1–3.3. No third-party code is copied.
Interface dimensions are millimetres; body coordinates use mm_per_unit.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math
import re
import numpy as np

from .asset import Asset
from .boolean import boolean_difference, boolean_union
from .primitives import Cube
from .transforms import transform, translate_shape


def _identifier(value: str) -> str:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*", value):
        raise ValueError(f"Invalid assembly identifier: {value!r}")
    return value


def _print_part_identifier(value: str) -> str:
    value = _identifier(value)
    if value in {'scene', 'exploded'}:
        raise ValueError(f"Print-part ID {value!r} is reserved for assembly export filenames (scene.glb/exploded.glb)")
    return value


@dataclass(frozen=True)
class InterfaceFrame:
    """Local stop-plane centre; +Z enters slot, +X spans tab width.

    Both sides use the SAME insertion-axis convention, not opposite outward
    normals. Positions are scene units; axes must be orthonormal.
    """
    origin: tuple = (0, 0, 0)
    x_axis: tuple = (1, 0, 0)
    insert_axis: tuple = (0, 0, 1)

    def matrix(self):
        p, x, z = (np.asarray(v, dtype=float) for v in (self.origin, self.x_axis, self.insert_axis))
        if any(v.shape != (3,) or not np.isfinite(v).all() for v in (p, x, z)):
            raise ValueError("interface frame needs finite 3-vectors")
        if not np.allclose([np.linalg.norm(x), np.linalg.norm(z), x @ z], [1, 1, 0], atol=1e-8, rtol=0):
            raise ValueError("interface axes must be unit and perpendicular")
        m = np.eye(4)
        m[:3, :3] = np.column_stack((x, np.cross(z, x), z))
        m[:3, 3] = p
        return m


@dataclass(frozen=True)
class TabSlot:
    width_mm: float
    thickness_mm: float
    insertion_mm: float
    slot_depth_mm: float
    fit_offset_mm: float
    root_overlap_mm: float = 0.5
    opening_extension_mm: float = 0.5
    lead_in_mm: float = 0.0

    def __post_init__(self):
        if not all(math.isfinite(float(v)) for v in asdict(self).values()):
            raise ValueError("interface parameters must be finite")
        if min(self.width_mm, self.thickness_mm, self.insertion_mm, self.slot_depth_mm,
               self.root_overlap_mm, self.opening_extension_mm) <= 0:
            raise ValueError("interface dimensions and explicit overlaps must be positive")
        if self.slot_depth_mm < self.insertion_mm:
            raise ValueError("slot depth must accommodate full insertion")
        if min(self.width_mm, self.thickness_mm) + 2 * self.fit_offset_mm <= 0:
            raise ValueError("fit offset collapses slot cross-section")
        if not 0 <= self.lead_in_mm < min(self.width_mm / 2, self.thickness_mm / 2, self.insertion_mm):
            raise ValueError("lead-in exceeds effective tab dimensions")

    def geometry_recipe(self, mm_per_unit: float = 1):
        """Shared boxes and cut planes for generation and final-mesh queries."""
        w, t, length, depth, fit, root, opening, lead = (
            getattr(self, name) / mm_per_unit for name in asdict(self))
        planes = []
        if lead:
            for axis, half in ((0, w / 2), (1, t / 2)):
                for sign in (-1, 1):
                    normal = np.zeros(3)
                    normal[axis], normal[2] = sign / math.sqrt(2), 1 / math.sqrt(2)
                    point = np.zeros(3)
                    point[axis], point[2] = sign * (half - lead), length
                    z = np.zeros(3)
                    z[1 - axis] = 1
                    planes.append((tuple(point), tuple(normal), tuple(z)))
        return dict(tab_size=(w,t,length+root), tab_center=(0,0,(length-root)/2),
                    slot_size=(w+2*fit,t+2*fit,depth+opening), slot_center=(0,0,(depth-opening)/2),
                    cut_span=4*max(w,t,length+root), planes=planes)

    def geometry(self, mm_per_unit: float):
        """Tab solid and slot cutter from ONE shared parameter set."""
        r = self.geometry_recipe(mm_per_unit)
        tab = Cube(r['tab_size'], center=r['tab_center'])
        for point, normal, z in r['planes']:
            m = InterfaceFrame(point, normal, z).matrix()
            span = r['cut_span']
            tab = boolean_difference(tab, transform(Cube((span,span,span), center=(span/2,0,0)), m))
        slot = Cube(r['slot_size'], center=r['slot_center'])
        return tab, slot


def _check_world_frames(tab_world, slot_world, mm_per_unit):
    """Existing matrix tolerance in scene units, not manufacturing clearance."""
    tab_world, slot_world = np.asarray(tab_world), np.asarray(slot_world)
    relative_rotation = tab_world[:3, :3] @ slot_world[:3, :3].T
    angle = math.acos(float(np.clip((np.trace(relative_rotation) - 1) / 2, -1, 1)))
    return dict(matched=bool(np.allclose(tab_world, slot_world, rtol=0, atol=1e-8)),
                translation_error_mm=float(np.linalg.norm(tab_world[:3, 3] - slot_world[:3, 3]) * mm_per_unit),
                rotation_error_deg=math.degrees(angle))


def _require_matching_frames(connection, transforms, mm_per_unit):
    residual = _check_world_frames(
        transforms[connection['tab_part']] @ np.asarray(connection['tab_frame']),
        transforms[connection['slot_part']] @ np.asarray(connection['slot_frame']), mm_per_unit)
    if not residual['matched']:
        raise ValueError(
            f"MATE_FRAME_MISMATCH: interface={connection['id']} "
            f"tab={connection['tab_part']} slot={connection['slot_part']} "
            f"translation_error_mm={residual['translation_error_mm']:.12g} "
            f"rotation_error_deg={residual['rotation_error_deg']:.12g}")


def _validate_connection(connection, parts, previous):
    cid = connection['id']
    for key in ('id', 'parameter_name', 'tab_port', 'slot_port'):
        _identifier(connection[key])
    if connection['tab_part'] == connection['slot_part'] or {connection['tab_part'], connection['slot_part']} - parts.keys():
        raise ValueError(f"connection {cid}: self connection or unknown print part")
    for other in previous:
        if other['id'] == cid:
            raise ValueError(f"connection {cid}: duplicate interface ID")
        if other['parameter_name'] == connection['parameter_name'] and other['parameters'] != connection['parameters']:
            raise ValueError(f"connection {cid}: shared parameter name {connection['parameter_name']} has inconsistent values")
        for role in ('tab', 'slot'):
            if (other[f'{role}_part'], other[f'{role}_port']) == (connection[f'{role}_part'], connection[f'{role}_port']):
                raise ValueError(f"connection {cid}: {role} port {connection[f'{role}_part']}/{connection[f'{role}_port']} already occupied")


class FixedAssembly:
    """Ordered placement and additional interfaces with world-frame checks.

    Either placed endpoint can locate the other. Already placed parts stay fixed;
    extra connections check consistency, without graph solving or articulation.
    """
    def __init__(self, *, root_id: str, mm_per_unit: float, root_frame: InterfaceFrame | None = None):
        self.root_id = _print_part_identifier(root_id)
        self.mm_per_unit = float(mm_per_unit)
        if not math.isfinite(self.mm_per_unit) or self.mm_per_unit <= 0:
            raise ValueError("mm_per_unit must be positive and fixed")
        self.bodies, self.parts, self.components, self.connections = {}, {}, {}, []
        self.transforms = {self.root_id: (root_frame or InterfaceFrame()).matrix()}
        self.print_rotations = {}
        self._selected_nodes = set()
        self._input_bodies = []  # Keep identity references alive for overlap detection.

    def add_part(self, part_id: str, body: Asset, *, components: tuple[str, ...]):
        _print_part_identifier(part_id)
        if part_id in self.parts or not isinstance(body, Asset):
            raise ValueError("duplicate print-part ID or non-Asset body")
        if not components or len(set(components)) != len(components):
            raise ValueError("each print part needs unique semantic component names")
        if set(components) & {c for values in self.components.values() for c in values}:
            raise ValueError("semantic component belongs to multiple print parts")
        def nodes(asset):
            if asset._joints:
                raise ValueError("articulation is unsupported in fixed assembly v1")
            return {id(asset)} | set().union(*(nodes(c) for c in asset._children.values()))
        selected = nodes(body)
        if selected & self._selected_nodes:
            raise ValueError("overlapping ancestor/descendant or repeated material selection")
        self._selected_nodes.update(selected)
        self._input_bodies.append(body)
        self.bodies[part_id] = body.copy()
        self.parts[part_id] = body.copy()
        self.components[part_id] = tuple(components)

    def set_print_orientation(self, part_id: str, *, rotation_deg=(0, 0, 0)):
        """Print-only Euler degrees, column vectors: Rz @ Ry @ Rx.

        Does not alter local geometry, assembly frames or interface placement.
        """
        if part_id not in self.parts:
            raise ValueError(f'unknown print part: {part_id}')
        angles = np.asarray(rotation_deg, dtype=float)
        if angles.shape != (3,) or not np.isfinite(angles).all():
            raise ValueError('print orientation needs three finite angles in degrees')
        self.print_rotations[part_id] = tuple(float(a) for a in angles)

    def print_rotation(self, part_id: str):
        x, y, z = np.deg2rad(self.print_rotations.get(part_id, (0, 0, 0)))
        cx, cy, cz = np.cos([x, y, z]); sx, sy, sz = np.sin([x, y, z])
        rotation = np.eye(4)
        rotation[:3, :3] = (np.array([[cz,-sz,0],[sz,cz,0],[0,0,1]]) @
            np.array([[cy,0,sy],[0,1,0],[-sy,0,cy]]) @
            np.array([[1,0,0],[0,cx,-sx],[0,sx,cx]]))
        return rotation

    def connect(self, interface_id: str, *, tab_part: str, slot_part: str,
                tab_frame: InterfaceFrame, slot_frame: InterfaceFrame, parameters: TabSlot,
                parameter_name: str, tab_port: str = "tab", slot_port: str = "slot"):
        connection = dict(id=interface_id, tab_part=tab_part, slot_part=slot_part,
            tab_port=tab_port, slot_port=slot_port, parameter_name=parameter_name,
            parameters=asdict(parameters))
        _validate_connection(connection, self.parts, self.connections)
        tf, sf = tab_frame.matrix(), slot_frame.matrix()
        connection.update(tab_frame=tf.tolist(), slot_frame=sf.tolist())
        tab_placed, slot_placed = tab_part in self.transforms, slot_part in self.transforms
        new_part, new_transform = None, None
        if not tab_placed and not slot_placed:
            raise ValueError(f"connection {interface_id}: tab={tab_part} slot={slot_part}: at least one endpoint must already be placed")
        if not tab_placed:
            new_part, new_transform = tab_part, self.transforms[slot_part] @ sf @ np.linalg.inv(tf)
        elif not slot_placed:
            new_part, new_transform = slot_part, self.transforms[tab_part] @ tf @ np.linalg.inv(sf)
        else:
            _require_matching_frames(connection, self.transforms, self.mm_per_unit)
        # Boolean construction attaches children; copies protect the saved trees
        # (including their parent links) until BOTH mating geometries succeed.
        tab, cutter = parameters.geometry(self.mm_per_unit)
        tab, cutter = transform(tab, tf), transform(cutter, sf)
        tab_result = boolean_union(self.parts[tab_part].copy(), tab)
        slot_result = boolean_difference(self.parts[slot_part].copy(), cutter)
        connection.update(tab_solid=tab, slot_cutter=cutter)
        self.parts[tab_part], self.parts[slot_part] = tab_result, slot_result
        if new_part is not None:
            self.transforms[new_part] = new_transform
        self.connections.append(connection)

    def validate(self):
        for part_id in self.parts:
            _print_part_identifier(part_id)
        if self.root_id not in self.parts or set(self.parts) != set(self.transforms):
            raise ValueError("root must be a print part and all print parts must have exactly one transform")
        placed = {self.root_id}
        for index, connection in enumerate(self.connections):
            _validate_connection(connection, self.parts, self.connections[:index])
            endpoints = {connection['tab_part'], connection['slot_part']}
            if not endpoints & placed:
                raise ValueError(f"connection {connection['id']}: tab={connection['tab_part']} slot={connection['slot_part']}: at least one endpoint must already be placed")
            placed.update(endpoints)
            _require_matching_frames(connection, self.transforms, self.mm_per_unit)
        if placed != set(self.parts):
            raise ValueError("all print parts must be reached by ordered connections from the root")

    def scene(self, *, exploded_mm: float = 0):
        self.validate()
        scene = Asset(label="FixedAssembly")
        for i, (name, part) in enumerate(self.parts.items()):
            placed = transform(part, self.transforms[name])
            if exploded_mm:
                placed = translate_shape(placed, (i * exploded_mm / self.mm_per_unit, 0, 0))
            scene.attach_part(name, placed)
        return scene
