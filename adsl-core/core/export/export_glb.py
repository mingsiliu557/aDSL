from __future__ import annotations

from pathlib import Path
import json
import numpy as np

from ..asset import Asset
from ..joints import _normalize_joint_limit

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
