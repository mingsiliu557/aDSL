"""Canonical, recursive Mesh64 evaluation; Blender is only a leaf tessellator.

Solids stay in a nearby local frame. Their affine transforms and material
provenance are plain data, so neither nested CSG nor consumers read Blender
objects or depend on datablocks surviving a scene reset.
"""
from __future__ import annotations

from dataclasses import dataclass, field
import math

import numpy as np

from ..asset import Asset
from ..math_utils import _as_mat4
from ..joints import _normalize_joint_limit
from .mesh_validity import MeshEvaluationError, checked_solid, validate_mesh, mesh_metrics


_UNIT_MESHES = {}


def _manifold():
    try:
        import manifold3d as mf
    except ImportError as error:
        raise MeshEvaluationError('GEOMETRY_DEPENDENCY_UNAVAILABLE', str(error),
            failure_kind='environment') from error
    return mf


def _matrix(value):
    try:
        matrix = np.array(_as_mat4(value), dtype=np.float64, copy=True)
    except (TypeError, ValueError) as error:
        raise MeshEvaluationError('INVALID_TRANSFORM', str(error), stage='input_geometry',
                                  failure_kind='input_geometry') from error
    if not np.isfinite(matrix).all() or not np.array_equal(matrix[3], [0, 0, 0, 1]):
        raise MeshEvaluationError('INVALID_TRANSFORM', 'expected a finite affine matrix',
            stage='input_geometry', failure_kind='input_geometry')
    if abs(np.linalg.det(matrix[:3, :3])) == 0:
        raise MeshEvaluationError('INVALID_TRANSFORM', 'singular affine transform',
            stage='input_geometry', failure_kind='input_geometry')
    return matrix


@dataclass
class MeshPiece:
    solid: object
    transform: np.ndarray
    node_path: str
    primitive_type: str
    materials: list[dict]
    records: list[dict] = field(default_factory=list)

    def world_solid(self):
        return self.solid.transform(np.ascontiguousarray(self.transform[:3], dtype=np.float64))

    def world_mesh(self, *, mm_per_unit=1.0):
        """Apply transforms in NumPy float64, preserving directed inner shells."""
        import trimesh
        raw = self.solid.to_mesh64()
        local = np.asarray(raw.vert_properties, dtype=np.float64)[:, :3]
        world = (local @ self.transform[:3, :3].T + self.transform[:3, 3]) * mm_per_unit
        faces = np.asarray(raw.tri_verts, dtype=np.int64)
        if np.linalg.det(self.transform[:3, :3]) < 0:
            faces = faces[:, [0, 2, 1]]
        mesh = trimesh.Trimesh(world, faces, process=False)
        ids = np.asarray(raw.face_id, dtype=np.int64)
        if len(ids) and (ids.min() < 0 or ids.max() >= len(self.materials)):
            raise MeshEvaluationError('MATERIAL_PROVENANCE_UNAVAILABLE', 'invalid face source material',
                node_path=self.node_path)
        mesh.face_attributes['material'] = ids
        mesh.metadata['materials'] = [dict(m) for m in self.materials]
        return mesh


@dataclass
class Evaluation:
    pieces: list[MeshPiece]
    nodes: list[dict]
    records: list[dict]
    materials: list[dict]


def _register_material(materials, kind, color, alpha):
    """Share equal appearance; primitive identity is not a material boundary."""
    color = [float(c) for c in color]
    spec = dict(base_color=[*color, 1.0], alpha=1.0 if alpha is None else float(alpha),
        metallic=0.0, roughness=0.5,
        blend_method='OPAQUE' if alpha is None else 'BLEND', use_backface_culling=False)
    for index, existing in enumerate(materials):
        if all(existing.get(key) == value for key, value in spec.items()):
            return index
    materials.append(dict(name=f'mat_{kind}_{len(materials)}', **spec))
    return len(materials)-1


def _unit_mesh(kind):
    """Use the existing Blender tessellation density, at unit size/origin only."""
    if kind in _UNIT_MESHES:
        return _UNIT_MESHES[kind]
    try:
        import bpy
    except ImportError as error:
        raise MeshEvaluationError('GEOMETRY_DEPENDENCY_UNAVAILABLE',
            'primitive tessellation requires bpy', failure_kind='environment') from error
    obj = None
    try:
        if kind == 'sphere':
            bpy.ops.mesh.primitive_uv_sphere_add(radius=1.0, location=(0, 0, 0))
        elif kind == 'cube':
            bpy.ops.mesh.primitive_cube_add(size=1.0, location=(0, 0, 0))
        elif kind == 'cylinder':
            bpy.ops.mesh.primitive_cylinder_add(radius=1.0, depth=1.0, location=(0, 0, 0))
        else:
            raise ValueError(f'unsupported tessellator {kind}')
        obj = bpy.context.active_object
        obj.data.calc_loop_triangles()
        vertices = np.array([tuple(v.co) for v in obj.data.vertices], dtype=np.float64)
        faces = np.array([tuple(t.vertices) for t in obj.data.loop_triangles], dtype=np.uint64)
        normalizations = []
        if not mesh_metrics(vertices, faces, allow_empty=True)['valid']:
            from .mesh_validity import normalize_blender_input
            normalization = normalize_blender_input(obj)
            normalizations = normalization.get('normalizations', [])
            obj.data.calc_loop_triangles()
            vertices = np.array([tuple(v.co) for v in obj.data.vertices], dtype=np.float64)
            faces = np.array([tuple(t.vertices) for t in obj.data.loop_triangles], dtype=np.uint64)
        vertices.setflags(write=False)
        faces.setflags(write=False)
        _UNIT_MESHES[kind] = vertices, faces, normalizations
        return vertices, faces, normalizations
    except MeshEvaluationError:
        raise
    except Exception as error:
        raise MeshEvaluationError('PRIMITIVE_TESSELLATION_FAILED', str(error),
            failure_kind='environment', primitive_type=kind) from error
    finally:
        if obj is not None:
            data = obj.data
            bpy.data.objects.remove(obj, do_unlink=True)
            if data.users == 0:
                bpy.data.meshes.remove(data)


def _leaf(prim, world, path, materials, records, primitive_index):
    kind, params = prim['type'], prim['params']
    if kind not in ('cube', 'sphere', 'cylinder', 'mesh'):
        raise MeshEvaluationError('INVALID_PRIMITIVE', f'unsupported primitive type {kind!r}',
            stage='input_geometry', failure_kind='input_geometry', node_path=path,
            primitive_index=primitive_index)
    placement = np.eye(4, dtype=np.float64)
    normalizations = []
    if kind == 'mesh':
        vertices = np.asarray(params['vertices'], dtype=np.float64)
        faces = np.asarray(params['triangles'])
        input_precision = 'float64_parameters'
    else:
        vertices, faces, normalizations = _unit_mesh(kind)
        input_precision = 'float32_unit_tessellation_float64_parameters'
        if kind == 'cube':
            size = np.asarray(params['scale'], dtype=np.float64)
            placement[:3, :3] = np.diag(size)
            placement[:3, 3] = params['center']
            positive = np.all(size > 0)
        elif kind == 'sphere':
            radius = float(params['radius'])
            placement[:3, :3] *= radius
            placement[:3, 3] = params['center']
            positive = radius > 0
        else:
            p0, p1 = np.asarray(params['p0'], dtype=np.float64), np.asarray(params['p1'], dtype=np.float64)
            delta = p1 - p0
            length = float(np.linalg.norm(delta))
            radius = float(params['radius'])
            if not math.isfinite(length) or length <= 0 or radius <= 0:
                raise MeshEvaluationError('INVALID_PRIMITIVE', 'cylinder radius/length must be positive',
                    stage='input_geometry', failure_kind='input_geometry', node_path=path)
            direction = delta / length
            cross = np.array([-direction[1], direction[0], 0.0])
            skew = np.array([[0, -cross[2], cross[1]], [cross[2], 0, -cross[0]],
                             [-cross[1], cross[0], 0]])
            rotation = (np.diag([1.0, -1.0, -1.0]) if direction[2] <= -1 + 1e-15
                        else np.eye(3) + skew + skew @ skew / (1 + direction[2]))
            placement[:3, :3] = rotation @ np.diag([radius, radius, length])
            placement[:3, 3] = (p0 + p1) / 2
            positive = True
        if not positive or not np.isfinite(placement).all():
            raise MeshEvaluationError('INVALID_PRIMITIVE', 'dimensions must be finite and positive',
                stage='input_geometry', failure_kind='input_geometry', node_path=path)
    material_id = _register_material(materials, kind, prim.get('color', (1, 1, 1)), prim.get('alpha'))
    solid, metrics = validate_mesh(vertices, faces, allow_empty=True,
        face_ids=np.full(len(faces), material_id, dtype=np.uint64),
        node_path=path, primitive_type=kind, primitive_index=primitive_index,
        input_precision=input_precision)
    # Intrinsic dimensions and the declared primitive center belong to the
    # local shape. Never add them to a large incoming world translation before
    # CSG: subtracting that translation later would already have lost bits.
    placement = _matrix(placement)
    solid = solid.transform(np.ascontiguousarray(placement[:3], dtype=np.float64))
    transform = world @ _matrix(prim.get('xform'))
    row = dict(stage='internal_evaluation', operation=kind, node_path=path,
               primitive_index=primitive_index, input_precision=input_precision,
               output_precision='float64', metrics_frame='local_tessellation',
               material_id=material_id, local_primitive_transform=placement.tolist(),
               transform=transform.tolist(), metrics=metrics)
    normalizations = [dict(n, node_path=path, primitive_index=primitive_index) for n in normalizations]
    records.extend(normalizations)
    records.append(row)
    return MeshPiece(solid, transform, path, kind, materials, [*normalizations, row])


def _joint_state(joint):
    value = float(joint.initial)
    result = np.eye(4, dtype=np.float64)
    axis = np.asarray(joint.axis, dtype=np.float64)
    length = float(np.linalg.norm(axis))
    axis = np.array([0., 0., 1.]) if length < 1e-12 else axis / length
    if joint.joint_type == 'prismatic':
        result[:3, 3] = axis * value
    elif joint.joint_type == 'revolute' and abs(value) > 1e-15:
        x, y, z = axis
        c, s, q = np.cos(value), np.sin(value), 1 - np.cos(value)
        result[:3, :3] = ((c+x*x*q, x*y*q-z*s, x*z*q+y*s),
                          (y*x*q+z*s, c+y*y*q, y*z*q-x*s),
                          (z*x*q-y*s, z*y*q+x*s, c+z*z*q))
    return result


def _nearby_frame(pieces):
    """Factor a common incoming translation without adding a local centroid.

    The centroid may be small compared with a large world translation. Adding
    those values before nested CSG irreversibly rounds the local geometry.
    Keeping this frame translational also leaves solid volumes in scene units.
    """
    frame = np.eye(4, dtype=np.float64)
    for piece in pieces:
        if not piece.solid.is_empty():
            frame[:3, 3] = piece.transform[:3, 3]
            break
    return frame


def _operate(pieces, operation, path, materials, records, *, hull_material=None):
    mf = _manifold()
    anchor = _nearby_frame(pieces)
    inverse = np.linalg.inv(anchor)
    inputs = [p.solid.transform(np.ascontiguousarray((inverse @ p.transform)[:3])) for p in pieces]
    try:
        if operation == 'HULL':
            points = [np.asarray(s.to_mesh64().vert_properties)[:, :3] for s in inputs if not s.is_empty()]
            solid = mf.Manifold.hull_points(np.concatenate(points)) if points else mf.Manifold()
            if not solid.is_empty():
                raw = solid.to_mesh64()
                solid, _ = validate_mesh(np.asarray(raw.vert_properties)[:, :3], np.asarray(raw.tri_verts),
                    face_ids=np.full(len(raw.tri_verts), hull_material, dtype=np.uint64), node_path=path)
        else:
            op = {'UNION': mf.OpType.Add, 'DIFFERENCE': mf.OpType.Subtract,
                  'INTERSECT': mf.OpType.Intersect}[operation]
            # Batch evaluation consumes every input, including empty operands.
            # Subtract means head minus every tail, matching shared cutters.
            solid = mf.Manifold.batch_boolean(inputs, op)
        checked_solid(solid, allow_empty=True, operation=operation, node_path=path, input_count=len(inputs))
    except MeshEvaluationError:
        raise
    except Exception as error:
        raise MeshEvaluationError('BOOLEAN_EVALUATION_FAILED', str(error), operation=operation,
            node_path=path, input_count=len(inputs), attempted_measures=['recursive_manifold_mesh64']) from error
    row = dict(stage='internal_evaluation', operation=operation, node_path=path, input_count=len(inputs),
               input_precision='float64', output_precision='float64', empty=bool(solid.is_empty()),
               volume_scene_units3=float(solid.volume()), self_intersection='NOT_EVALUATED',
               method='recursive_manifold_mesh64')
    records.append(row)
    return MeshPiece(solid, anchor, path, operation.lower(), materials, [row])


def evaluate_shape(shape, *, include_joint_children=True, path=None, mm_per_unit=1.0):
    """Evaluate Asset CSG without creating a Blender Boolean intermediate.

    Independent primitives remain separate pieces. Only declared CSG nodes
    combine operands; DIFFERENCE applies the same cutters to every base piece.
    mm_per_unit supplies diagnostic units; output solids stay in scene units.
    """
    if not isinstance(shape, Asset):
        raise TypeError('evaluate_shape requires an Asset')
    if not math.isfinite(mm_per_unit) or mm_per_unit <= 0:
        raise ValueError('mm_per_unit must be finite and positive')
    materials, records, nodes = [], [], []

    def visit(asset, world, node_path, parent, name, mode, joint=None):
        node = dict(path=node_path, parent=parent, name=name, attach_mode=mode,
                    world_transform=world.tolist())
        if joint is not None:
            node['joint'] = dict(name=joint.name, joint_type=joint.joint_type, axis=list(joint.axis),
                initial=float(joint.initial), limit=_normalize_joint_limit(joint.joint_type, joint.limit))
        nodes.append(node)
        pieces, consumed = [], set()
        for index, prim in enumerate(asset.iter_local_primitives()):
            kind = prim['type']
            if kind not in ('boolean', 'hull'):
                try:
                    pieces.append(_leaf(prim, world, node_path, materials, records, index))
                except MeshEvaluationError as error:
                    error.diagnostic.setdefault('node_path', node_path)
                    error.diagnostic.setdefault('primitive_index', index)
                    raise
                except (TypeError, ValueError, KeyError, IndexError) as error:
                    raise MeshEvaluationError('INVALID_PRIMITIVE', str(error), stage='input_geometry',
                        failure_kind='input_geometry', node_path=node_path,
                        primitive_index=index, primitive_type=kind) from error
                continue
            operation = 'HULL' if kind == 'hull' else prim['params']['mode']
            if operation not in ('HULL', 'UNION', 'DIFFERENCE', 'INTERSECT'):
                raise MeshEvaluationError('INVALID_BOOLEAN_OPERATION', str(operation),
                    stage='input_geometry', failure_kind='input_geometry', node_path=node_path)
            child_names = list(asset._parts) if kind == 'hull' else sorted(asset._parts)
            consumed.update(child_names)
            operands = {}
            for child_name in child_names:
                operands[child_name] = visit(asset._parts[child_name], world,
                    f'{node_path}/{child_name}', node_path, child_name, 'part')
            flat = [p for child_name in child_names for p in operands[child_name]]
            if operation == 'DIFFERENCE':
                if 'base' in operands:
                    bases = operands['base']
                    cutters = [p for child_name in child_names if child_name.startswith('op_')
                               for p in operands[child_name]]
                else:
                    bases, cutters = flat[:1], flat[1:]
                pieces.extend(_operate([base, *cutters], operation, node_path, materials, records)
                              for base in bases)
            else:
                material = None
                if operation == 'HULL':
                    material = _register_material(materials, 'hull', prim.get('color', (1, 1, 1)), prim.get('alpha'))
                pieces.append(_operate(flat, operation, node_path, materials, records, hull_material=material))
        for child_name, child in asset._parts.items():
            if child_name not in consumed:
                pieces.extend(visit(child, world, f'{node_path}/{child_name}', node_path, child_name, 'part'))
        if include_joint_children:
            for joint_name, child in asset._joint_children.items():
                joint = asset._joints.get(joint_name)
                if joint is not None:
                    child_world = world @ _matrix(joint.origin) @ _joint_state(joint)
                    pieces.extend(visit(child, child_world, f'{node_path}/{joint_name}',
                                        node_path, joint_name, 'joint', joint))
        return pieces

    name = str(shape.label or 'asset')
    pieces = visit(shape, np.eye(4, dtype=np.float64), path or name, None, name, 'root')
    return Evaluation(pieces, nodes, records, materials)
