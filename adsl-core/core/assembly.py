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


class FixedAssembly:
    """Explicit print-part roots; receiver is placed before each tab child.

    No graph closure, multi-mate solving, articulation or automatic segmentation.
    """
    def __init__(self, *, root_id: str, mm_per_unit: float, root_frame: InterfaceFrame | None = None):
        self.root_id = _print_part_identifier(root_id)
        self.mm_per_unit = float(mm_per_unit)
        if not math.isfinite(self.mm_per_unit) or self.mm_per_unit <= 0:
            raise ValueError("mm_per_unit must be positive and fixed")
        self.bodies, self.parts, self.components, self.connections = {}, {}, {}, []
        self.transforms = {self.root_id: (root_frame or InterfaceFrame()).matrix()}
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

    def connect(self, interface_id: str, *, tab_part: str, slot_part: str,
                tab_frame: InterfaceFrame, slot_frame: InterfaceFrame, parameters: TabSlot,
                parameter_name: str, tab_port: str = "tab", slot_port: str = "slot"):
        for name in (interface_id, parameter_name, tab_port, slot_port):
            _identifier(name)
        if interface_id in {c['id'] for c in self.connections}:
            raise ValueError("duplicate interface ID")
        if tab_part == slot_part or {tab_part, slot_part} - self.parts.keys():
            raise ValueError("self connection or unknown print part")
        if slot_part not in self.transforms or tab_part in self.transforms:
            raise ValueError("unsupported placement: connect unplaced tab child to placed receiver; no cycles/multiple mates")
        for connection in self.connections:
            if connection['parameter_name'] == parameter_name and connection['parameters'] != asdict(parameters):
                raise ValueError("shared parameter name has inconsistent values")
            if (connection['slot_part'], connection['slot_port']) == (slot_part, slot_port):
                raise ValueError("slot port already occupied")
        tf, sf = tab_frame.matrix(), slot_frame.matrix()
        tab, cutter = parameters.geometry(self.mm_per_unit)
        tab, cutter = transform(tab, tf), transform(cutter, sf)
        self.parts[tab_part] = boolean_union(self.parts[tab_part], tab)
        self.parts[slot_part] = boolean_difference(self.parts[slot_part], cutter)
        self.transforms[tab_part] = self.transforms[slot_part] @ sf @ np.linalg.inv(tf)
        self.connections.append(dict(id=interface_id, tab_part=tab_part, slot_part=slot_part,
            tab_port=tab_port, slot_port=slot_port, parameter_name=parameter_name,
            parameters=asdict(parameters), tab_frame=tf.tolist(), slot_frame=sf.tolist(),
            tab_solid=tab, slot_cutter=cutter))

    def validate(self):
        for part_id in self.parts:
            _print_part_identifier(part_id)
        if not self.parts or set(self.parts) != set(self.transforms):
            raise ValueError("all print parts must be placed in one rooted tree")
        if len(self.connections) != len(self.parts) - 1:
            raise ValueError("placement must be a tree")

    def scene(self, *, exploded_mm: float = 0):
        self.validate()
        scene = Asset(label="FixedAssembly")
        for i, (name, part) in enumerate(self.parts.items()):
            placed = transform(part, self.transforms[name])
            if exploded_mm:
                placed = translate_shape(placed, (i * exploded_mm / self.mm_per_unit, 0, 0))
            scene.attach_part(name, placed)
        return scene
