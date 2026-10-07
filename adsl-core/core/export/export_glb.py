from __future__ import annotations
from typing import List, Dict, Any

from .mesh_repair import (_remove_mesh_object, _mesh_triangles,
    _normalize_zero_area_tessellation)
from .mesh_validity import _mesh_defects

from pathlib import Path
import json
import numpy as np

from ..asset import Asset
from ..joints import _normalize_joint_limit
from ..math_utils import _as_mat4

try:  # pragma: no cover - exercised only in Blender's Python
    import bpy
    import mathutils
except Exception as _blender_import_error:  # pragma: no cover
    bpy = None
    mathutils = None
    _BLENDER_IMPORT_ERROR = _blender_import_error
else:  # pragma: no cover
    _BLENDER_IMPORT_ERROR = None


def _require_blender() -> None:
    if bpy is None or mathutils is None:
        raise RuntimeError(
            "GLB export requires Blender's Python environment with bpy available."
        ) from _BLENDER_IMPORT_ERROR


def _joint_state_matrix(joint) -> np.ndarray:
    value = float(getattr(joint, "initial", 0.0))
    matrix = np.eye(4, dtype=float)
    axis = np.asarray(joint.axis, dtype=float)
    norm = float(np.linalg.norm(axis))
    axis = np.array([0.0, 0.0, 1.0]) if norm < 1e-12 else axis / norm
    if joint.joint_type == "prismatic":
        matrix[:3, 3] = axis * value
    elif joint.joint_type == "revolute" and abs(value) > 1e-15:
        x, y, z = axis
        c, s, q = np.cos(value), np.sin(value), 1.0 - np.cos(value)
        matrix[:3, :3] = (
            (c + x*x*q, x*y*q - z*s, x*z*q + y*s),
            (y*x*q + z*s, c + y*y*q, y*z*q - x*s),
            (z*x*q - y*s, z*y*q + x*s, c + z*z*q),
        )
    return matrix


def _material_key(base: str, color, alpha):
    r, g, b = color
    a = 1.0 if alpha is None else float(alpha)
    ri = int(max(0, min(1, r)) * 255)
    gi = int(max(0, min(1, g)) * 255)
    bi = int(max(0, min(1, b)) * 255)
    ai = int(max(0, min(1, a)) * 255)
    return f"{base}_{ri:02x}{gi:02x}{bi:02x}_{ai:02x}"

def _make_material(name: str, color, alpha):
    key = _material_key(name, color, alpha)
    mat = bpy.data.materials.get(key)
    if mat is None:
        mat = bpy.data.materials.new(name=key)
        mat.use_nodes = True
    bsdf = mat.node_tree.nodes.get('Principled BSDF')
    if bsdf is not None:
        r, g, b = color
        bsdf.inputs['Base Color'].default_value = (float(r), float(g), float(b), 1.0)
        if alpha is not None:
            bsdf.inputs['Alpha'].default_value = float(alpha)
            mat.blend_method = 'BLEND'
        else:
            mat.blend_method = 'OPAQUE'
    return mat

def _apply_xform_matrix(obj, M):
    if M is None:
        return
    mm = mathutils.Matrix(_as_mat4(M).tolist())
    obj.matrix_world = mm @ obj.matrix_world
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=True, rotation=True, scale=True)
    obj.select_set(False)

def _make_sphere(prim: Dict[str, Any]) -> "bpy.types.Object":
    p = prim["params"]
    c = prim["color"]
    a = prim["alpha"]
    cx, cy, cz = p["center"]
    r = float(p["radius"])
    bpy.ops.mesh.primitive_uv_sphere_add(radius=r, location=(cx, cy, cz))
    obj = bpy.context.active_object
    _apply_xform_matrix(obj, prim.get("xform"))
    mat = _make_material("mat_sphere", c, a)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return obj

def _make_cube(prim: Dict[str, Any]) -> "bpy.types.Object":
    p = prim["params"]
    c = prim["color"]
    a = prim["alpha"]
    cx, cy, cz = p["center"]
    sx, sy, sz = p["scale"]
    bpy.ops.mesh.primitive_cube_add(size=1.0, location=(cx, cy, cz))
    obj = bpy.context.active_object
    obj.scale = (sx, sy, sz)
    bpy.context.view_layer.objects.active = obj
    obj.select_set(True)
    bpy.ops.object.transform_apply(location=False, rotation=False, scale=True)
    obj.select_set(False)
    _apply_xform_matrix(obj, prim.get("xform"))
    mat = _make_material("mat_cube", c, a)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return obj

def _make_cylinder(prim):
    p = prim["params"]
    c = prim["color"]
    a = prim["alpha"]

    p0 = mathutils.Vector(p["p0"])
    p1 = mathutils.Vector(p["p1"])
    r = float(p["radius"])

    v = p1 - p0
    length = v.length if v.length > 1e-8 else 1e-8
    mid = (p0 + p1) * 0.5

    z = mathutils.Vector((0, 0, 1))
    rot_quat = z.rotation_difference(v.normalized()) if v.length > 1e-8 else z.rotation_difference(z)

    bpy.ops.mesh.primitive_cylinder_add(
        radius=r,
        depth=length,
        location=mid,
        rotation=rot_quat.to_euler()
    )
    obj = bpy.context.active_object


    _apply_xform_matrix(obj, prim.get("xform"))
    mat = _make_material("mat_cylinder", c, a)
    if obj.data.materials:
        obj.data.materials[0] = mat
    else:
        obj.data.materials.append(mat)
    return obj

def _make_mesh(prim):
    data = bpy.data.meshes.new("constructive_mesh")
    data.from_pydata(prim["params"]["vertices"], [], prim["params"]["triangles"])
    data.update()
    obj = bpy.data.objects.new("constructive_mesh", data)
    bpy.context.collection.objects.link(obj)
    _apply_xform_matrix(obj, prim.get("xform"))
    data.materials.append(_make_material("mat_mesh", prim["color"], prim["alpha"]))
    return obj


def _duplicate_object(
    obj: "bpy.types.Object"
) -> "bpy.types.Object":
    dup = obj.copy()
    dup.data = obj.data.copy()
    bpy.context.collection.objects.link(dup)
    return dup

def _apply_boolean(
    base_obj: "bpy.types.Object",
    other_obj: "bpy.types.Object",
    operation: str
):
    bpy.context.view_layer.objects.active = base_obj
    last_exc: Exception | None = None

    # FAST can return successfully with open/degenerate geometry at coplanar
    # overlaps (SF02/SF03). Use EXACT directly; keep parameters and cleanup.
    for solver in ("EXACT",):
        modifier = base_obj.modifiers.new(
            name=f"Boolean_{operation}",
            type='BOOLEAN'
        )
        modifier.operation = operation
        modifier.solver = solver
        modifier.object = other_obj

        try:
            bpy.ops.object.modifier_apply(modifier=modifier.name)
            bpy.data.objects.remove(other_obj, do_unlink=True)
            return
        except Exception as exc:
            last_exc = exc
            try:
                base_obj.modifiers.remove(modifier)
            except Exception:
                pass

    if last_exc is not None:
        raise last_exc





def _recover_boolean(base, operands, operation):
    """Recompute from valid operands, then validate target-precision geometry.

    All edits are on a temporary object. No hole filling, proximity welding or
    repair of invalid input solids. Tolerances derive from float32 coordinate
    precision; simplification is bounded independently by measured displacement.
    """
    import bmesh
    from mathutils.bvhtree import BVHTree
    from ..constructive import _manifold
    mf = _manifold()
    solids, source_vertices, source_faces, face_materials, materials = [], [], [], [], []
    bpy.context.view_layer.update()
    for operand in operands:
        if _mesh_defects(operand.data):
            raise ValueError(f'BOOLEAN_RECOVERY_INVALID_OPERAND: object={operand.name!r}')
        v, f, _ = _mesh_triangles(operand.data, operand.matrix_world)
        # Deduplicate only exact coordinate copies (e.g. attribute seams).
        unique, inverse = np.unique(v, axis=0, return_inverse=True)
        solid = mf.Manifold(mf.Mesh64(unique, inverse[f].astype(np.uint64)))
        if solid.status() != mf.Error.NoError or solid.is_empty():
            raise ValueError(f'BOOLEAN_RECOVERY_INVALID_OPERAND: {solid.status()}')
        solids.append(solid)
        slots = []
        for material in operand.data.materials:
            if material not in materials:
                materials.append(material)
            slots.append(materials.index(material))
        face_materials.extend(slots[min(operand.data.polygons[t.polygon_index].material_index,
                                       len(slots)-1)] if slots else 0
                              for t in operand.data.loop_triangles)
        source_faces.extend((f + len(source_vertices)).tolist())
        source_vertices.extend(v.tolist())
    op = {'UNION': mf.OpType.Add, 'DIFFERENCE': mf.OpType.Subtract,
          'INTERSECT': mf.OpType.Intersect}[operation]
    reference = mf.Manifold.batch_boolean(solids, op)
    if reference.status() != mf.Error.NoError or reference.is_empty():
        raise ValueError(f'BOOLEAN_RECOVERY_UNAVAILABLE: operation={operation} status={reference.status()} empty={reference.is_empty()}')
    raw = reference.to_mesh64()
    rv, rf = np.asarray(raw.vert_properties[:, :3]), np.asarray(raw.tri_verts)
    scale = max(1., float(np.abs(rv).max()))
    quantum = float(np.spacing(np.float32(scale)))
    displacement_bound = float(16 * np.finfo(np.float32).eps * scale)
    reference_tree = BVHTree.FromPolygons(rv.tolist(), rf.tolist(), all_triangles=True)
    material_tree = BVHTree.FromPolygons(source_vertices, source_faces, all_triangles=True)
    components = len(reference.decompose())
    failures = []
    # A small fixed attempt budget, never an unbounded tolerance escalation.
    for tolerance in (0., quantum, 2 * quantum):
        candidate = None
        try:
            solid = reference if tolerance == 0 else reference.simplify(tolerance)
            if solid.status() != mf.Error.NoError:
                raise ValueError(str(solid.status()))
            raw = solid.to_mesh64()
            world = np.asarray(raw.vert_properties[:, :3])
            inverse = np.linalg.inv(np.asarray(base.matrix_world))
            local = world @ inverse[:3, :3].T + inverse[:3, 3]
            data = bpy.data.meshes.new('boolean_recomputed')
            data.from_pydata(local.tolist(), [], np.asarray(raw.tri_verts).tolist())
            candidate = bpy.data.objects.new('boolean_recovery_candidate', data)
            bpy.context.collection.objects.link(candidate)
            candidate.matrix_world = base.matrix_world.copy()
            bm = bmesh.new()
            try:
                bm.from_mesh(data)
                by_coordinate, targetmap = {}, {}
                for vert in bm.verts:
                    key = tuple(vert.co)
                    if key in by_coordinate:
                        targetmap[vert] = by_coordinate[key]
                    else:
                        by_coordinate[key] = vert
                bmesh.ops.weld_verts(bm, targetmap=targetmap)
                bmesh.ops.dissolve_degenerate(bm, dist=0, edges=list(bm.edges))
                welded = len(targetmap)
                if (not all(e.is_manifold and e.is_contiguous for e in bm.edges)
                        or not all(v.is_manifold for v in bm.verts)):
                    raise ValueError('target precision weld is not manifold')
                bm.to_mesh(data)
            finally:
                bm.free()
            data.update()
            _normalize_zero_area_tessellation(candidate)
            defects = _mesh_defects(candidate.data)
            if defects:
                raise ValueError(str(defects))
            v, f, _ = _mesh_triangles(candidate.data, candidate.matrix_world)
            measured = mf.Manifold(mf.Mesh64(v, f.astype(np.uint64)))
            if (measured.status() != mf.Error.NoError or measured.is_empty()
                    or len(measured.decompose()) != components):
                raise ValueError('target precision changed shell connectivity')
            tree = BVHTree.FromPolygons(v.tolist(), f.tolist(), all_triangles=True)
            distance = max(max(reference_tree.find_nearest(tuple(p))[3] for p in v),
                           max(tree.find_nearest(tuple(p))[3] for p in rv))
            volume_change = abs(measured.volume() - reference.volume())
            bounds_change = float(np.max(np.abs(np.array(measured.bounding_box()) -
                                                  np.array(reference.bounding_box()))))
            if (distance > displacement_bound or bounds_change > displacement_bound
                    or volume_change > reference.surface_area() * displacement_bound):
                raise ValueError('target precision geometry displacement exceeds numerical bound')
            for material in materials:
                candidate.data.materials.append(material)
            for polygon, triangle in zip(candidate.data.polygons, v[f]):
                hit = material_tree.find_nearest(tuple(triangle.mean(axis=0)))
                polygon.material_index = face_materials[hit[2]]
            report = dict(method='manifold_boolean_recompute', operation=operation,
                          operand_count=len(operands), simplify_tolerance_scene_units=tolerance,
                          exact_vertices_welded=welded, shell_components=components,
                          max_vertex_to_surface_sample_distance_scene_units=distance,
                          displacement_bound_scene_units=displacement_bound,
                          volume_change_scene_units3=volume_change,
                          bounds_change_scene_units=bounds_change,
                          zero_area_triangles_after=0, boundary_edges_after=0,
                          nonmanifold_edges_after=0, status='APPLIED')
            encoded_report = json.dumps(report)
            old = base.data
            base.data = candidate.data
            base['adsl_boolean_recovery'] = encoded_report
            if old.users == 0:
                bpy.data.meshes.remove(old)
            return
        except (ValueError, RuntimeError) as error:
            failures.append(str(error))
        finally:
            if candidate is not None:
                _remove_mesh_object(candidate)
    raise ValueError(f'BOOLEAN_RECOVERY_FAILED: object={base.name!r} attempts={failures}')


def _apply_boolean_group(base, others, operation):
    """Keep original operands until the complete Boolean node is validated."""
    if not others:
        return
    candidate = _duplicate_object(base)
    pending = []
    try:
        try:
            for other in others:
                duplicate = _duplicate_object(other)
                pending.append(duplicate)
                _apply_boolean(candidate, duplicate, operation)
                pending.pop()
            _normalize_zero_area_tessellation(candidate)
            defects = _mesh_defects(candidate.data)
            if defects:
                raise ValueError(f'invalid Boolean output: {defects}')
        except (ValueError, RuntimeError) as error:
            _recover_boolean(candidate, [base, *others], operation)
            candidate['adsl_boolean_recovery_trigger'] = str(error)
        old = base.data
        base.data = candidate.data
        for key in candidate.keys():
            if key.startswith('adsl_'):
                base[key] = candidate[key]
        if old.users == 0:
            bpy.data.meshes.remove(old)
    finally:
        for obj in pending:
            if obj.name in bpy.data.objects:
                _remove_mesh_object(obj)
        _remove_mesh_object(candidate)


def _new_asset_node(
    name: str,
    *,
    parent=None,
    path: str,
    attach_mode: str,
    joint=None,
):
    # Blender object names are globally unique, so leaf-only names such as
    # ``front_panel`` are silently rewritten to ``front_panel.001`` when the
    # same part occurs in multiple subassemblies. Use the semantic path as the
    # stable GLB node name and retain the clean leaf name as explicit metadata.
    node = bpy.data.objects.new(str(path), None)
    bpy.context.collection.objects.link(node)
    node.empty_display_type = "PLAIN_AXES"
    node["adsl_kind"] = "asset"
    node["adsl_name"] = str(name)
    node["adsl_path"] = path
    node["adsl_attach_mode"] = attach_mode
    if joint is not None:
        node["adsl_joint_name"] = str(joint.name)
        node["adsl_joint_type"] = str(joint.joint_type)
        node["adsl_joint_axis"] = [float(value) for value in joint.axis]
        node["adsl_joint_initial"] = float(joint.initial)
        joint_limit = _normalize_joint_limit(joint.joint_type, joint.limit)
        if joint_limit is not None:
            node["adsl_joint_limit"] = [joint_limit[0], joint_limit[1]]
    if parent is not None:
        node.parent = parent
    return node


def _parent_geometry(obj, parent, *, name: str) -> None:
    world_matrix = obj.matrix_world.copy()
    obj.parent = parent
    obj.matrix_world = world_matrix
    obj.name = name
    obj["adsl_kind"] = "geometry"


def _build_shape(
    shape: Asset,
    world_xform=None,
    *,
    include_joint_children: bool = True,
    parent_node=None,
    node_name: str | None = None,
    path: str | None = None,
    attach_mode: str = "root",
    joint=None,
) -> List["bpy.types.Object"]:
    if world_xform is None:
        world_xform = np.eye(4, dtype=float)
    else:
        world_xform = _as_mat4(world_xform)

    resolved_name = str(node_name or shape.label or "asset")
    resolved_path = path or resolved_name
    hierarchy_node = _new_asset_node(
        resolved_name,
        parent=parent_node,
        path=resolved_path,
        attach_mode=attach_mode,
        joint=joint,
    )

    objs: List["bpy.types.Object"] = []
    consumed_children = set()

    def _build_local_geometry() -> None:
        for primitive_index, prim in enumerate(shape.iter_local_primitives()):
            prim = dict(prim)
            # Compose with incoming transform (e.g., from kinematic joints)
            px = prim.get("xform")
            if px is None:
                px = np.eye(4, dtype=float)
            else:
                px = _as_mat4(px)
            prim["xform"] = world_xform @ px

            t = prim["type"]
            if t == "sphere":
                cur_obj = _make_sphere(prim)
                _parent_geometry(cur_obj, hierarchy_node, name=f"geometry_{primitive_index}_sphere")
                cur_obj.data.name = f"{resolved_name}_sphere_mesh"
                objs.append(cur_obj)
            elif t == "cube":
                cur_obj = _make_cube(prim)
                _parent_geometry(cur_obj, hierarchy_node, name=f"geometry_{primitive_index}_cube")
                cur_obj.data.name = f"{resolved_name}_cube_mesh"
                objs.append(cur_obj)
            elif t == "cylinder":
                cur_obj = _make_cylinder(prim)
                _parent_geometry(cur_obj, hierarchy_node, name=f"geometry_{primitive_index}_cylinder")
                cur_obj.data.name = f"{resolved_name}_cylinder_mesh"
                objs.append(cur_obj)
            elif t == "mesh":
                cur_obj = _make_mesh(prim)
                _parent_geometry(cur_obj, hierarchy_node, name=f"geometry_{primitive_index}_mesh")
                objs.append(cur_obj)
            elif t == "hull":
                from ..constructive import _manifold, _mesh_data
                child_names = list(shape._parts)
                consumed_children.update(child_names)
                operands = []
                try:
                    for name in child_names:
                        operands.extend(_build_shape(
                            shape._parts[name], world_xform,
                            include_joint_children=include_joint_children,
                            parent_node=hierarchy_node, node_name=name,
                            path=f"{resolved_path}/{name}", attach_mode="part"))
                    bpy.context.view_layer.update()
                    points = [tuple(obj.matrix_world @ v.co)
                              for obj in operands for v in obj.data.vertices]
                    if not points:
                        raise ValueError("hull: operands contain no mesh vertices")
                    params = _mesh_data(_manifold().Manifold.hull_points(points), "hull")
                    # Operand vertices already include parent/operand transforms.
                    cur_obj = _make_mesh(dict(params=params, xform=None,
                                              color=prim["color"], alpha=prim["alpha"]))
                    _parent_geometry(cur_obj, hierarchy_node, name=f"geometry_{primitive_index}_hull")
                    objs.append(cur_obj)
                finally:
                    for obj in operands:
                        data = obj.data
                        bpy.data.objects.remove(obj, do_unlink=True)
                        if data.users == 0:
                            bpy.data.meshes.remove(data)
            elif t == "boolean":
                mode = prim["params"]["mode"]
                child_names = sorted(shape._parts.keys())
                consumed_children.update(child_names)

                if mode in ["UNION", "INTERSECT"]:
                    built_children = []
                    for name in child_names:
                        built_children.extend(
                            _build_shape(
                                shape._parts[name],
                                world_xform,
                                include_joint_children=include_joint_children,
                                parent_node=hierarchy_node,
                                node_name=name,
                                path=f"{resolved_path}/{name}",
                                attach_mode="part",
                            )
                        )
                    if not built_children:
                        continue
                    base = built_children[0]
                    _apply_boolean_group(base, built_children[1:], mode)
                    for other in built_children[1:]:
                        _remove_mesh_object(other)
                    objs.append(base)
                elif mode == "DIFFERENCE":
                    base_objs = []
                    other_objs = []

                    if "base" in shape._parts:
                        base_objs = _build_shape(
                            shape._parts["base"],
                            world_xform,
                            include_joint_children=include_joint_children,
                            parent_node=hierarchy_node,
                            node_name="base",
                            path=f"{resolved_path}/base",
                            attach_mode="part",
                        )
                        for name in child_names:
                            if name.startswith("op_"):
                                other_objs.extend(
                                    _build_shape(
                                        shape._parts[name],
                                        world_xform,
                                        include_joint_children=include_joint_children,
                                        parent_node=hierarchy_node,
                                        node_name=name,
                                        path=f"{resolved_path}/{name}",
                                        attach_mode="part",
                                    )
                                )
                    else:
                        built_children = []
                        for name in child_names:
                            built_children.extend(
                                _build_shape(
                                    shape._parts[name],
                                    world_xform,
                                    include_joint_children=include_joint_children,
                                    parent_node=hierarchy_node,
                                    node_name=name,
                                    path=f"{resolved_path}/{name}",
                                    attach_mode="part",
                                )
                            )
                        if not built_children:
                            continue
                        base_objs, other_objs = [built_children[0]], built_children[1:]
                    if not base_objs:
                        continue
                    results = []
                    for base_obj in base_objs:
                        _apply_boolean_group(base_obj, other_objs, "DIFFERENCE")
                        results.append(base_obj)
                    for other in other_objs:
                        try:
                            bpy.data.objects.remove(other, do_unlink=True)
                        except Exception:
                            pass
                    objs.extend(results)
                else:
                    raise ValueError(f"Unknown boolean mode: {mode}")
            else:
                raise ValueError(f"Unknown primitive type: {t}")

    _build_local_geometry()

    for name, child in shape._parts.items():
        if name in consumed_children:
            continue
        objs.extend(
            _build_shape(
                child,
                world_xform,
                include_joint_children=include_joint_children,
                parent_node=hierarchy_node,
                node_name=name,
                path=f"{resolved_path}/{name}",
                attach_mode="part",
            )
        )

    if include_joint_children:
        # Traverse kinematic joint children so articulated scenes export fully.
        for jname, child in getattr(shape, "_joint_children", {}).items():
            j = shape._joints.get(jname)
            if j is None:
                continue
            child_world = world_xform @ _as_mat4(j.origin) @ _joint_state_matrix(j)
            objs.extend(
                _build_shape(
                    child,
                    child_world,
                    include_joint_children=include_joint_children,
                    parent_node=hierarchy_node,
                    node_name=jname,
                    path=f"{resolved_path}/{jname}",
                    attach_mode="joint",
                    joint=j,
                )
            )

    return objs











def _patch_glb_transforms(path, transforms):
    """Keep affine node transforms in float64 at the GLTF JSON boundary.

    Blender stores object matrices in float32. Accessor positions deliberately
    stay float32/local; serializing the paired NumPy transforms avoids losing
    a small object solely because its world translation is large.
    """
    import struct
    data = Path(path).read_bytes()
    chunks, offset = [], 12
    while offset < len(data):
        length, kind = struct.unpack_from('<II', data, offset)
        chunks.append((kind, data[offset+8:offset+8+length]))
        offset += 8+length
    document = json.loads(chunks[0][1])
    y_up = np.array([[1.,0,0,0], [0,0,1,0], [0,-1,0,0], [0,0,0,1]])
    z_up = y_up.T
    found = set()
    for node in document.get('nodes', []):
        name = node.get('name')
        if name not in transforms:
            continue
        matrix = y_up @ np.asarray(transforms[name], dtype=np.float64) @ z_up
        for key in ('translation', 'rotation', 'scale'):
            node.pop(key, None)
        node['matrix'] = matrix.T.reshape(-1).tolist()
        found.add(name)
    missing = set(transforms)-found
    if missing:
        raise ValueError(f'GLB_EXPORT_INCOMPLETE: missing transform nodes={sorted(missing)!r}')
    payload = json.dumps(document, separators=(',', ':')).encode()
    payload += b' ' * (-len(payload) % 4)
    chunks[0] = (chunks[0][0], payload)
    body = b''.join(struct.pack('<II', len(payload), kind)+payload for kind,payload in chunks)
    Path(path).write_bytes(struct.pack('<4sII', b'glTF', 2, 12+len(body))+body)


def _instantiate_mesh(mesh, name, parent, transform):
    """Instantiate canonical local coordinates and plain material provenance."""
    data = bpy.data.meshes.new(name)
    data.from_pydata(mesh.vertices.tolist(), [], mesh.faces.tolist())
    specs = mesh.metadata.get('materials', [])
    for spec in specs:
        material = bpy.data.materials.new(spec['name'])
        material.use_nodes = True
        bsdf = material.node_tree.nodes.get('Principled BSDF')
        for key, field in [('Base Color','base_color'), ('Alpha','alpha'),
                           ('Metallic','metallic'), ('Roughness','roughness')]:
            bsdf.inputs[key].default_value = spec[field]
        material.blend_method = spec.get('blend_method', 'OPAQUE')
        material.use_backface_culling = spec.get('use_backface_culling', False)
        data.materials.append(material)
    for polygon, material in zip(data.polygons, mesh.face_attributes.get('material',
                                np.zeros(len(mesh.faces), dtype=int)), strict=True):
        polygon.material_index = int(material)
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    obj.parent = parent
    obj.matrix_parent_inverse.identity()
    obj.matrix_basis = mathutils.Matrix(np.asarray(transform, dtype=np.float64).tolist())
    obj['adsl_kind'] = 'geometry'
    return obj


def _export_evaluation(evaluation, path, *, mm_per_unit=1.0, apply_modifiers=True, draco=False):
    """Only the final canonical mesh crosses the Blender precision boundary."""
    from .mesh_validity import target_mesh, checked_solid
    nodes, transforms, expected = {}, {}, {}
    for node in evaluation.nodes:
        parent = nodes.get(node['parent'])
        obj = _new_asset_node(node['name'], parent=parent, path=node['path'],
                              attach_mode=node['attach_mode'])
        # Joint metadata is plain data; no Joint object/datablock is retained.
        if 'joint' in node:
            for key, value in node['joint'].items():
                if value is not None:
                    obj['adsl_joint_'+('type' if key == 'joint_type' else key)] = value
        world = np.asarray(node['world_transform'], dtype=np.float64)
        parent_world = (np.eye(4) if node['parent'] is None else
                        np.asarray(next(n['world_transform'] for n in evaluation.nodes
                                        if n['path'] == node['parent']), dtype=np.float64))
        local = np.linalg.inv(parent_world) @ world
        obj.matrix_basis = mathutils.Matrix(local.tolist())
        nodes[node['path']] = obj
        transforms[obj.name] = local
    for index, piece in enumerate(evaluation.pieces):
        if piece.solid.is_empty():
            continue  # Empty CSG intermediates may coexist with valid outputs.
        checked_solid(piece.solid, node_path=piece.node_path)
        affine = piece.transform.copy()
        translation = affine[:3, 3].copy()
        affine[:3, 3] = 0
        actual_local = piece.solid.transform(np.ascontiguousarray(affine[:3], dtype=np.float64))
        mesh, recenter, record = target_mesh(actual_local, mm_per_unit=mm_per_unit,
                                           node_path=piece.node_path, operation=piece.primitive_type.upper(),
                                           input_count=piece.records[-1].get('input_count', 1),
                                           operation_nodes=[{key:r[key] for key in ('node_path','operation','input_count') if key in r}
                                               for r in evaluation.records if r.get('input_count') is not None])
        mesh.face_attributes['material'] = mesh.face_attributes['source_face_id']
        mesh.metadata['materials'] = evaluation.materials
        world = np.eye(4)
        world[:3, 3] = translation+recenter[:3, 3]
        parent = nodes[piece.node_path]
        node_world = np.asarray(next(n['world_transform'] for n in evaluation.nodes
                                   if n['path'] == piece.node_path), dtype=np.float64)
        local = np.linalg.inv(node_world) @ world
        obj = _instantiate_mesh(mesh, f'{piece.node_path}/geometry_{index}_{piece.primitive_type}', parent, local)
        obj['adsl_mesh_evaluation'] = json.dumps(dict(method='recursive_manifold_mesh64',
            records=piece.records, target_precision=record))
        transforms[obj.name] = local
        expected[obj.name] = len(mesh.faces)
    if not expected:
        from .mesh_validity import MeshEvaluationError
        raise MeshEvaluationError('EMPTY_REQUIRED_GEOMETRY', 'scene has no nonempty final geometry')
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', use_selection=False,
        export_apply=apply_modifiers, export_draco_mesh_compression_enable=bool(draco), export_extras=True)
    _patch_glb_transforms(path, transforms)
    _verify_glb_meshes(path, expected)
    return Path(path)


def export_glb(shape: Asset, filepath: str | Path, *, clear_scene: bool = True,
               apply_modifiers: bool = True, draco: bool = False,
               include_joint_children: bool = True, mm_per_unit: float | None = None):
    _require_blender()
    from .mesh64 import evaluate_shape
    export_path = Path(filepath)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    if clear_scene:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    evaluation = evaluate_shape(shape, include_joint_children=include_joint_children,
                                mm_per_unit=1.0 if mm_per_unit is None else mm_per_unit)
    return _export_evaluation(evaluation, export_path,
        mm_per_unit=1.0 if mm_per_unit is None else mm_per_unit,
        apply_modifiers=apply_modifiers, draco=draco)


def _verify_glb_meshes(path, expected):
    """A successful exporter return must not hide an omitted required mesh."""
    import struct
    with Path(path).open('rb') as stream:
        header = stream.read(20)
        if len(header) != 20 or header[:4] != b'glTF' or header[16:20] != b'JSON':
            raise ValueError('GLB_EXPORT_INCOMPLETE: invalid GLB header')
        document = json.loads(stream.read(struct.unpack('<I', header[12:16])[0]))
    nodes = {node.get('name'): node for node in document.get('nodes', [])}
    for name, triangle_count in expected.items():
        node = nodes.get(name, {})
        if 'mesh' not in node:
            raise ValueError(f'GLB_EXPORT_INCOMPLETE: missing mesh object={name!r}')
        primitives = document['meshes'][node['mesh']].get('primitives', [])
        triangles = sum(document['accessors'][p['indices']]['count'] // 3
                        for p in primitives if p.get('mode', 4) == 4 and 'indices' in p
                        and 'POSITION' in p.get('attributes', {}))
        if triangles != triangle_count:
            raise ValueError(f'GLB_EXPORT_INCOMPLETE: object={name!r} expected_triangles={triangle_count} actual={triangles}')

    import trimesh
    from .mesh_validity import validate_mesh
    scene = trimesh.load(path, force='scene', process=False)
    for name in expected:
        # GLTF material splitting is encoding, not separate physical shells.
        pieces = []
        for node in scene.graph.nodes_geometry:
            cursor = node
            while cursor is not None and cursor != name:
                cursor = scene.graph.transforms.parents.get(cursor)
            if cursor == name:
                _, geometry = scene.graph[node]
                pieces.append(scene.geometry[geometry])
        if not pieces:
            raise ValueError(f'GLB_EXPORT_INCOMPLETE: no decoded mesh object={name!r}')
        mesh = trimesh.util.concatenate(pieces)
        try:
            _, metrics = validate_mesh(mesh.vertices, mesh.faces, stage='file_readback', node_path=name)
        except ValueError as error:
            raise ValueError(f'GLB_WRITTEN_MESH_INVALID: object={name!r}: {error}') from error

__all__ = ["export_glb"]
