from __future__ import annotations
from typing import List, Dict, Any

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

    bpy.data.objects.remove(other_obj, do_unlink=True)
    if last_exc is not None:
        raise last_exc

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
                    for other in built_children[1:]:
                        _apply_boolean(
                            base, other,
                            "UNION" if mode == "UNION" else "INTERSECT"
                        )
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
                        for other in other_objs:
                            dup = _duplicate_object(other)
                            _apply_boolean(base_obj, dup, "DIFFERENCE")
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

def _mesh_triangles(data, matrix):
    data.calc_loop_triangles()
    vertices = np.asarray([tuple(matrix @ v.co) for v in data.vertices], dtype=float).reshape(-1, 3)
    faces = np.asarray([tuple(t.vertices) for t in data.loop_triangles], dtype=np.int64).reshape(-1, 3)
    triangles = vertices[faces]
    crosses = np.cross(triangles[:, 1] - triangles[:, 0], triangles[:, 2] - triangles[:, 0])
    return vertices, faces, crosses


def _bmesh_component_count(mesh):
    remaining = set(mesh.verts)
    count = 0
    while remaining:
        stack = [remaining.pop()]
        count += 1
        while stack:
            for edge in stack.pop().link_edges:
                for vertex in edge.verts:
                    if vertex in remaining:
                        remaining.remove(vertex)
                        stack.append(vertex)
    return count


def _normalize_exact_zero_length_edges(obj, vertices, faces, crosses, polygons):
    """Collapse only adjacent exact duplicates, preserving every positive triangle.

    Triangulate before welding so deleting a redundant polygon corner cannot
    silently change the diagonals of a slightly nonplanar Boolean face.
    """
    import bmesh
    from collections import Counter

    mesh = bmesh.new()
    candidate = None
    committed = False
    try:
        mesh.from_mesh(obj.data)
        mesh.verts.ensure_lookup_table()
        mesh.faces.ensure_lookup_table()
        targets, affected, used = {}, set(), set()
        for edge in mesh.edges:
            a, b = sorted(edge.verts, key=lambda v: v.index)
            if (tuple(a.co) != tuple(b.co) or not edge.is_manifold or not edge.is_contiguous
                    or not all(v.is_manifold for v in edge.verts)
                    or not all(f.index in polygons and len(f.verts) > 3 for f in edge.link_faces)):
                continue
            if a in used or b in used:
                raise ValueError('adjacent zero-length edges require separate diagnosis')
            targets[b] = a
            used.update((a, b))
            affected.update(edge.link_faces)
        if not targets:
            return False
        components = _bmesh_component_count(mesh)
        positions = {v: tuple(v.co) for v in mesh.verts if v not in targets}
        candidate = obj.data.copy()
        bmesh.ops.triangulate(mesh, faces=list(affected), quad_method='FIXED', ngon_method='EAR_CLIP')
        bmesh.ops.weld_verts(mesh, targetmap=targets)
        if (not all(e.is_manifold and e.is_contiguous for e in mesh.edges)
                or not all(v.is_manifold for v in mesh.verts)):
            raise ValueError('exact zero-length cleanup is not closed, manifold and consistently wound')
        if _bmesh_component_count(mesh) != components:
            raise ValueError('exact zero-length cleanup changed connectivity')
        if (len(mesh.verts) != len(positions)
                or any(not v.is_valid or tuple(v.co) != p for v, p in positions.items())):
            raise ValueError('exact zero-length cleanup moved or removed unrelated vertices')
        mesh.to_mesh(candidate)
        candidate.update()
        new_vertices, new_faces, new_crosses = _mesh_triangles(candidate, obj.matrix_world)
        if not np.isfinite(new_crosses).all() or np.any(np.all(new_crosses == 0, axis=1)):
            raise ValueError('exact zero-length cleanup left degenerate triangles')
        if (len(np.unique(new_vertices, axis=0)) != len(new_vertices)
                or not np.array_equal(np.unique(vertices, axis=0), np.unique(new_vertices, axis=0))):
            raise ValueError('exact zero-length cleanup changed positions or left coincident vertices')

        def surfaces(data, points, triangles, normals):
            # Cyclic permutations preserve winding; reversed triangles do not.
            keys = []
            for triangle, cross, loop in zip(triangles, normals, data.loop_triangles):
                if np.all(cross == 0):
                    continue
                coords = tuple(tuple(p) for p in points[triangle])
                oriented = min(coords, coords[1:]+coords[:1], coords[2:]+coords[:2])
                keys.append((oriented, data.polygons[loop.polygon_index].material_index))
            return Counter(keys)

        if surfaces(obj.data, vertices, faces, crosses) != surfaces(candidate, new_vertices, new_faces, new_crosses):
            raise ValueError('exact zero-length cleanup changed positive triangle surfaces or materials')
        original = obj.data
        obj.data = candidate
        committed = True
        obj['adsl_zero_length_normalization'] = json.dumps(dict(
            method='exact_zero_length_edge_cleanup', status='APPLIED', merged_vertices=len(targets),
            zero_area_triangles_before=int(np.count_nonzero(np.all(crosses == 0, axis=1))),
            zero_area_triangles_after=0, maximum_displacement_mm=0.0,
            positive_triangle_surfaces_unchanged=True, materials_preserved=True,
            boundary_edges_after=0, closed=True, manifold=True, winding_consistent=True,
            shell_components=components))
        if original.users == 0:
            bpy.data.meshes.remove(original)
        return True
    finally:
        mesh.free()
        if candidate is not None and not committed:
            bpy.data.meshes.remove(candidate)


def _normalize_zero_area_tessellation(obj):
    """Clean exact zero-length edges or retriangulate bad planar polygons.

    Exact zero cross products trigger this pass; small positive faces are left
    alone. Work on a copy and commit only a closed, manifold, consistently wound
    result with unchanged geometric vertex positions and components. Exact edge
    cleanup preserves every positive triangle; planar retriangulation preserves
    area and signed volume. Unresolved degenerate triangles remain evaluation errors.
    """
    vertices, faces, crosses = _mesh_triangles(obj.data, obj.matrix_world)
    bad = np.flatnonzero(np.all(crosses == 0, axis=1))
    if not len(bad):
        return
    polygons = sorted({obj.data.loop_triangles[int(i)].polygon_index for i in bad})

    def reject(reason):
        raise ValueError(
            f"EVALUATED_MESH_DEGENERATE: object={obj.name!r} "
            f"zero_area_triangles={len(bad)} polygons={polygons} "
            f"coincident_vertices={len(vertices)-len(np.unique(vertices, axis=0))} reason={reason}"
        )

    if not np.isfinite(vertices).all():
        reject('nonfinite vertices')
    try:
        if _normalize_exact_zero_length_edges(obj, vertices, faces, crosses, polygons):
            return
    except (ValueError, RuntimeError) as error:
        reject(str(error))
    # A nonplanar polygon can change its surface when its diagonal changes.
    # Use only a floating-point arithmetic bound, never a small-face threshold.
    for index in polygons:
        ids = np.asarray(obj.data.polygons[index].vertices)
        if len(ids) < 4:
            reject('degenerate triangle has no alternate polygon tessellation')
        offsets = vertices[ids] - vertices[ids[0]]
        normals = np.cross(offsets[1:-1], offsets[2:])
        normal = normals[np.argmax(np.linalg.norm(normals, axis=1))]
        size = np.linalg.norm(normal)
        bound = 64 * np.finfo(float).eps * np.max(np.abs(offsets)) * size
        if size == 0:
            reject(f'polygon {index} is collinear')
        distance = float(np.max(np.abs(offsets @ normal)) / size)
        if np.any(np.abs(offsets @ normal) > bound):
            reject(f'polygon {index} is nonplanar: max_plane_distance_scene_units={distance:.9g} '
                   f'planarity_bound_scene_units={bound/size:.9g}')

    import bmesh
    candidate = obj.data.copy()
    mesh = bmesh.new()
    committed = False
    try:
        mesh.from_mesh(candidate)
        mesh.faces.ensure_lookup_table()
        components = _bmesh_component_count(mesh)
        bmesh.ops.triangulate(mesh, faces=[mesh.faces[i] for i in polygons],
                              quad_method='BEAUTY', ngon_method='EAR_CLIP')
        if (not all(e.is_manifold and e.is_contiguous for e in mesh.edges)
                or not all(v.is_manifold for v in mesh.verts)):
            reject('retriangulation is not closed, manifold and consistently wound')
        if _bmesh_component_count(mesh) != components:
            reject('retriangulation changed connectivity')
        mesh.to_mesh(candidate)
        candidate.update()
        new_vertices, new_faces, new_crosses = _mesh_triangles(candidate, obj.matrix_world)
        if not np.array_equal(vertices, new_vertices):
            reject('retriangulation changed vertices')
        if np.any(np.all(new_crosses == 0, axis=1)) or not np.isfinite(new_crosses).all():
            reject('retriangulation left degenerate triangles')
        # Exact duplicates can become nonmanifold under downstream exact welding.
        if len(np.unique(new_vertices, axis=0)) != len(new_vertices):
            reject('coincident vertices require separate diagnosis')
        area = np.linalg.norm(crosses, axis=1).sum()
        new_area = np.linalg.norm(new_crosses, axis=1).sum()
        volume_terms = np.einsum('ij,ij->i', vertices[faces[:, 0]] - vertices[0], crosses)
        new_terms = np.einsum('ij,ij->i', new_vertices[new_faces[:, 0]] - vertices[0], new_crosses)
        epsilon = 64 * np.finfo(float).eps
        if (abs(new_area - area) > epsilon * area
                or abs(new_terms.sum() - volume_terms.sum())
                > epsilon * max(np.abs(volume_terms).sum(), np.abs(new_terms).sum())):
            reject('retriangulation changed surface area or signed volume')
        original = obj.data
        obj.data = candidate
        committed = True
        obj['adsl_retriangulated_polygons'] = len(polygons)
        obj['adsl_zero_area_triangles_before'] = len(bad)
        obj['adsl_mesh_shell_components'] = components
        if original.users == 0:
            bpy.data.meshes.remove(original)
    except (ValueError, RuntimeError) as error:
        if str(error).startswith('EVALUATED_MESH_DEGENERATE:'):
            raise
        reject(str(error))
    finally:
        mesh.free()
        if not committed:
            bpy.data.meshes.remove(candidate)


def _normalize_numeric_microcracks(obj, mm_per_unit):
    """Weld only roundoff-sized short edges in isolated triangular boundary loops.

    Never search across loops, objects or disconnected shells. Two float32 ULPs
    per local coordinate, 1e-4 of the loop's longest edge, and a hard 1e-4 mm
    displacement ceiling must ALL hold. These are numerical bounds, not fit
    allowances. A failed trial leaves the original mesh available to checkers.
    """
    import bmesh
    if not np.isfinite(mm_per_unit) or mm_per_unit <= 0:
        raise ValueError('microcrack normalization requires positive finite mm_per_unit')
    mesh = bmesh.new()
    candidate = None
    committed = False
    report = None
    try:
        mesh.from_mesh(obj.data)
        mesh.verts.ensure_lookup_table()
        mesh.edges.ensure_lookup_table()
        boundary = {e for e in mesh.edges if e.is_boundary}
        if not boundary:
            return
        report = dict(method='local_boundary_weld', status='SKIPPED',
            boundary_edges_before=len(boundary), boundary_edges_after=len(boundary),
            maximum_displacement_mm=0.0, displacement_limit_mm=1e-4,
            merged_vertices=0, target_boundary_loops=0,
            reason='no isolated triangular boundary with roundoff-sized short edge')
        if any(not (e.is_boundary or e.is_manifold) for e in mesh.edges):
            report['reason'] = 'pre-existing nonmanifold or wire edges'
            return report
        matrix = np.asarray(obj.matrix_world, dtype=float)
        linear = matrix[:3, :3] * mm_per_unit
        vertices, faces, crosses = _mesh_triangles(obj.data, obj.matrix_world)
        if not np.isfinite(vertices).all() or not np.isfinite(linear).all():
            report['reason'] = 'nonfinite geometry'
            return report
        components = _bmesh_component_count(mesh)
        targetmap = {}
        pairs = []
        # Connected components of boundary edges are the only search regions.
        while boundary:
            edge = min(boundary, key=lambda e: e.index)
            boundary.remove(edge)
            loop_edges, loop_vertices, stack = {edge}, set(edge.verts), list(edge.verts)
            while stack:
                for adjacent in stack.pop().link_edges:
                    if adjacent in boundary:
                        boundary.remove(adjacent)
                        loop_edges.add(adjacent)
                        for v in adjacent.verts:
                            if v not in loop_vertices:
                                loop_vertices.add(v)
                                stack.append(v)
            if (len(loop_edges) != 3 or len(loop_vertices) != 3
                    or any(sum(e.is_boundary for e in v.link_edges) != 2 for v in loop_vertices)):
                continue
            ordered = sorted(loop_vertices, key=lambda v: v.index)
            coords = np.asarray([tuple(v.co) for v in ordered])
            ulps = np.spacing(np.abs(coords).astype(np.float32)).astype(float)
            distance = float(np.max(np.linalg.norm(2 * ulps, axis=1)))
            found = bmesh.ops.find_doubles(mesh, verts=ordered, dist=distance)['targetmap']
            longest = max(np.linalg.norm(linear @ (np.asarray(e.verts[0].co) -
                                                   np.asarray(e.verts[1].co))) for e in loop_edges)
            accepted = {}
            for source, target in found.items():
                if source not in loop_vertices or target not in loop_vertices:
                    continue
                a, b = np.asarray(source.co, dtype=float), np.asarray(target.co, dtype=float)
                delta = a - b
                bound = 2 * np.maximum(np.spacing(np.abs(a).astype(np.float32)),
                                       np.spacing(np.abs(b).astype(np.float32))).astype(float)
                displacement = max(float(np.linalg.norm(linear @ delta)),
                    float(np.linalg.norm(vertices[source.index] - vertices[target.index]) * mm_per_unit))
                shared = set(source.link_faces) & set(target.link_faces)
                # Do not collapse a real triangle or remove a face/material patch.
                if (np.all(np.abs(delta) <= bound) and displacement <= 1e-4
                        and displacement <= longest * 1e-4 and longest > 0
                        and len(shared) == 1 and all(len(f.verts) > 3 for f in shared)):
                    accepted[source] = target
            if len(accepted) != 1:
                continue
            source, target = next(iter(accepted.items()))
            targetmap[source] = target
            pairs.append(dict(source_local=list(source.co), target_local=list(target.co),
                displacement_mm=max(float(np.linalg.norm(linear @ (np.asarray(source.co) -
                                                                   np.asarray(target.co)))),
                    float(np.linalg.norm(vertices[source.index] - vertices[target.index]) * mm_per_unit))))
        if not targetmap:
            return report
        report.update(status='REJECTED', target_boundary_loops=len(pairs),
            attempted_maximum_displacement_mm=max(p['displacement_mm'] for p in pairs))
        original_positions = {v: tuple(v.co) for v in mesh.verts if v not in targetmap}
        original_faces = {f: f.material_index for f in mesh.faces}
        candidate = obj.data.copy()
        bmesh.ops.weld_verts(mesh, targetmap=targetmap)
        report['attempted_boundary_edges_after'] = sum(e.is_boundary for e in mesh.edges)
        if (not all(e.is_manifold and e.is_contiguous for e in mesh.edges)
                or not all(v.is_manifold for v in mesh.verts)):
            raise ValueError('weld result is not closed, manifold and consistently wound')
        if _bmesh_component_count(mesh) != components:
            raise ValueError('weld changed shell connectivity')
        if (len(mesh.faces) != len(original_faces)
                or any(not f.is_valid or f.material_index != material for f, material in original_faces.items())):
            raise ValueError('weld removed faces or changed material assignment')
        if (len(mesh.verts) != len(original_positions)
                or any(not v.is_valid or tuple(v.co) != position for v, position in original_positions.items())):
            raise ValueError('weld moved vertices outside the accepted targetmap')
        mesh.to_mesh(candidate)
        candidate.update()
        new_vertices, new_faces, new_crosses = _mesh_triangles(candidate, obj.matrix_world)
        if not np.isfinite(new_crosses).all() or np.any(np.all(new_crosses == 0, axis=1)):
            raise ValueError('weld left nonfinite or degenerate triangles')
        if len(np.unique(new_vertices, axis=0)) != len(new_vertices):
            raise ValueError('coincident vertices remain after weld')
        # Bound changes in the actual triangulated surface, not just BMVert moves.
        before = vertices * mm_per_unit
        after = new_vertices * mm_per_unit
        triangles = before[faces]
        new_triangles = after[new_faces]
        old_cross = crosses * mm_per_unit**2
        new_cross = new_crosses * mm_per_unit**2
        area = np.linalg.norm(old_cross, axis=1).sum() / 2
        new_area = np.linalg.norm(new_cross, axis=1).sum() / 2
        center = before.mean(axis=0)
        volume = np.einsum('ij,ij->i', triangles[:, 0] - center, old_cross).sum() / 6
        new_volume = np.einsum('ij,ij->i', new_triangles[:, 0] - center, new_cross).sum() / 6
        displacement = report['attempted_maximum_displacement_mm']
        lengths = np.linalg.norm(triangles - np.roll(triangles, 1, axis=1), axis=2).sum()
        eps = 64 * np.finfo(float).eps
        area_bound = 2 * displacement * lengths + eps * area
        volume_bound = 2 * displacement * max(area, new_area) + eps * max(abs(volume), abs(new_volume))
        if (abs(new_area - area) > area_bound or abs(new_volume - volume) > volume_bound
                or volume * new_volume <= 0
                or np.max(np.abs(np.array([before.min(axis=0), before.max(axis=0)]) -
                                    np.array([after.min(axis=0), after.max(axis=0)]))) > displacement + eps):
            raise ValueError('weld changed bounds, surface area or volume beyond displacement bounds')
        report.update(status='APPLIED', reason='validated local roundoff boundary weld',
            boundary_edges_after=0, maximum_displacement_mm=displacement,
            merged_vertices=len(pairs), shell_components=components, closed=True,
            manifold=True, winding_consistent=True, zero_area_triangles_after=0,
            surface_area_change_mm2=float(new_area-area), signed_volume_change_mm3=float(new_volume-volume),
            welded_pairs=pairs)
        original = obj.data
        obj.data = candidate
        committed = True
        if original.users == 0:
            bpy.data.meshes.remove(original)
        return report
    except (ValueError, RuntimeError) as error:
        if report is None:
            raise
        report.update(status='REJECTED', reason=str(error))
        return report
    finally:
        mesh.free()
        if candidate is not None and not committed:
            bpy.data.meshes.remove(candidate)
        if report is not None:
            obj['adsl_microcrack_normalization'] = json.dumps(report)


def export_glb(
    shape: Asset,
    filepath: str | Path,
    *,
    clear_scene: bool = True,
    apply_modifiers: bool = True,
    draco: bool = False,
    include_joint_children: bool = True,
    mm_per_unit: float | None = None,
):
    _require_blender()
    export_path = Path(filepath)
    export_path.parent.mkdir(parents=True, exist_ok=True)
    if clear_scene:
        bpy.ops.wm.read_factory_settings(use_empty=True)
    _build_shape(shape, include_joint_children=include_joint_children)
    for obj in bpy.context.scene.objects:
        if obj.type == "MESH":
            _normalize_zero_area_tessellation(obj)
            if mm_per_unit is not None:
                _normalize_numeric_microcracks(obj, mm_per_unit)
    if not any(obj.type == "MESH" for obj in bpy.data.objects):
        raise RuntimeError("Nothing to export: scene has no objects.")
    bpy.ops.export_scene.gltf(
        filepath=str(export_path),
        export_format='GLB',
        use_selection=False,
        export_apply=apply_modifiers,
        export_draco_mesh_compression_enable=bool(draco),
        export_extras=True,
    )
    return export_path

__all__ = ["export_glb"]
