"""Real Boolean export and bounded geometric evidence for fixed assembly v1.

Called inside the existing isolated asset executor (120-second geometry budget).
Blender evaluates aDSL CSG; Manifold unions shells WITHIN each print part only.
No mesh repair, remeshing, solver checks or tolerance tuning is performed.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import itertools
import json
from pathlib import Path
import time

import numpy as np

from ..assembly import FixedAssembly
from .export_glb import export_glb


def mesh_solid(mesh, face_ids=None):
    import manifold3d as mf
    if not np.isfinite(mesh.vertices).all() or np.any(mesh.area_faces <= 0) or not mesh.is_volume:
        raise ValueError("mesh is not a finite closed oriented volume, or has zero-area faces")
    solid = mf.Manifold(mf.Mesh64(np.array(mesh.vertices, dtype=np.float64, order='C', copy=True),
                                 np.array(mesh.faces, dtype=np.uint64, order='C', copy=True),
                                 face_id=face_ids))
    if solid.status() != mf.Error.NoError:
        raise ValueError(f"Manifold input rejected: {solid.status()}")
    return solid


def solid_mesh(solid):
    import manifold3d as mf
    import trimesh
    if solid.status() != mf.Error.NoError:
        raise ValueError(f"Manifold Boolean failed: {solid.status()}")
    raw = solid.to_mesh64()
    return trimesh.Trimesh(np.asarray(raw.vert_properties)[:, :3], np.asarray(raw.tri_verts), process=False)


def evaluated(shape, path, mm_per_unit, *, keep_materials=False):
    """Use actual post-Boolean Blender vertices, before GLTF's Y-up conversion."""
    import bpy
    import manifold3d as mf
    import trimesh
    export_glb(shape, path)
    shells, welded = [], 0
    materials, face_materials = [], []
    for obj in bpy.context.scene.objects:
        if obj.type != 'MESH':
            continue
        obj.data.calc_loop_triangles()
        vertex_count, triangle_count = len(obj.data.vertices), len(obj.data.loop_triangles)
        if not vertex_count or not triangle_count:
            raise ValueError(
                f"empty evaluated mesh {obj.name!r}: "
                f"vertices={vertex_count}, loop_triangles={triangle_count}"
            )
        vertices = np.asarray([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices]) * mm_per_unit
        faces = np.asarray([tuple(t.vertices) for t in obj.data.loop_triangles], dtype=np.int64)
        # Only exact duplicates within this object, never proximity welding.
        unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
        welded += len(vertices) - len(unique)
        mesh = trimesh.Trimesh(unique, inverse[faces], process=False)
        if keep_materials:
            offset = len(materials)
            # factory_settings in the next evaluation invalidates bpy references.
            # Keep plain material values, not Blender datablocks or guessed colors.
            for material in obj.data.materials:
                bsdf = material.node_tree.nodes.get('Principled BSDF') if material.use_nodes else None
                if bsdf is None:
                    raise ValueError('fixed assembly requires the existing Principled material representation')
                materials.append(dict(name=material.name,
                    base_color=list(bsdf.inputs['Base Color'].default_value),
                    alpha=float(bsdf.inputs['Alpha'].default_value),
                    metallic=float(bsdf.inputs['Metallic'].default_value),
                    roughness=float(bsdf.inputs['Roughness'].default_value),
                    blend_method=material.blend_method, use_backface_culling=material.use_backface_culling))
            ids = np.arange(len(face_materials), len(face_materials)+len(faces), dtype=np.uint64)
            face_materials.extend(offset+obj.data.polygons[t.polygon_index].material_index
                                  for t in obj.data.loop_triangles)
            # Explicit face subsets preserve provenance that mesh.split discards.
            for indices in trimesh.graph.connected_components(mesh.face_adjacency, nodes=np.arange(len(faces))):
                shell = mesh.submesh([indices], append=True, repair=False)
                shells.append(mesh_solid(shell, ids[indices]))
        else:
            for shell in mesh.split(only_watertight=False, repair=False):
                shells.append(mesh_solid(shell))
    if not shells:
        raise ValueError('empty evaluated part')
    merged = mf.Manifold.batch_boolean(shells, mf.OpType.Add)
    mesh = solid_mesh(merged)
    mesh_solid(mesh)
    if keep_materials:
        origins = np.asarray(merged.to_mesh64().face_id, dtype=np.int64)
        mesh.face_attributes['material'] = np.asarray(face_materials)[origins]
        if np.any(mesh.face_attributes['material'] >= len(materials)):
            raise ValueError('missing source material for exported face')
        mesh.metadata['materials'] = materials
    return mesh, merged, {'input_shells': len(shells), 'exact_duplicate_vertices_merged': welded,
                          'proximity_welding': False}


def _write_mesh_glb(meshes, transforms, path, mm_per_unit):
    """Only instantiate already-evaluated local meshes; never evaluate Asset CSG."""
    import bpy
    import mathutils
    bpy.ops.wm.read_factory_settings(use_empty=True)
    for name, mesh in meshes.items():
        data = bpy.data.meshes.new(name)
        data.from_pydata((mesh.vertices/mm_per_unit).tolist(), [], mesh.faces.tolist())
        for spec in mesh.metadata['materials']:
            material = bpy.data.materials.new(spec['name'])
            material.use_nodes = True
            bsdf = material.node_tree.nodes.get('Principled BSDF')
            for key, field in [('Base Color','base_color'), ('Alpha','alpha'),
                               ('Metallic','metallic'), ('Roughness','roughness')]:
                bsdf.inputs[key].default_value = spec[field]
            material.blend_method = spec['blend_method']
            material.use_backface_culling = spec['use_backface_culling']
            data.materials.append(material)
        for polygon, index in zip(data.polygons, mesh.face_attributes['material'], strict=True):
            polygon.material_index = int(index)
        parent = bpy.data.objects.new(name, None)
        parent['adsl_print_part_id'] = name
        bpy.context.collection.objects.link(parent)
        parent.matrix_world = mathutils.Matrix(np.asarray(transforms[name]).tolist())
        obj = bpy.data.objects.new(name+'_mesh', data)
        bpy.context.collection.objects.link(obj)
        obj.parent = parent
        obj.matrix_parent_inverse.identity()
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_yup=True,
        export_apply=False, export_extras=True, export_draco_mesh_compression_enable=False)


def _verify_written_exports(output, manifest, solids):
    """Read actual files, group material primitives by explicit part ID, undo poses."""
    import trimesh
    z_up = np.array([[1,0,0,0], [0,0,-1,0], [0,1,0,0], [0,0,0,1.]])
    unit = manifest['mm_per_unit']
    parts = {p['id']:p for p in manifest['parts']}
    checks = manifest['export_consistency'] = []
    files = [(p['stl'], {name: np.asarray(p['print_transform_mm'])}, False) for name,p in parts.items()]
    files += [(p['glb'], {name: np.eye(4)}, True) for name,p in parts.items()]
    for filename, field in [('scene.glb','assembly_transform'), ('exploded.glb','exploded_transform')]:
        transforms = {name:np.array(p[field], dtype=float) for name,p in parts.items()}
        for transform in transforms.values():
            transform[:3,3] *= unit
        files.append((filename, transforms, True))
    for filename, transforms, glb in files:
        try:
            grouped = {name:[] for name in transforms}
            if glb:
                scene = trimesh.load(output/filename, force='scene', process=False)
                for node in scene.graph.nodes_geometry:
                    cursor, owners = node, []
                    while cursor is not None:
                        if cursor in grouped:
                            owners.append(cursor)
                        cursor = scene.graph.transforms.parents.get(cursor)
                    if len(owners) != 1:
                        raise ValueError(f'geometry node {node!r} has no unique print-part ID')
                    transform, geometry = scene.graph[node]
                    mesh = scene.geometry[geometry].copy()
                    mesh.apply_transform(z_up @ transform)
                    mesh.apply_scale(unit)
                    grouped[owners[0]].append(mesh)
            else:
                grouped[next(iter(grouped))].append(trimesh.load(output/filename, process=False))
            for name, pieces in grouped.items():
                if not pieces:
                    raise ValueError(f'no saved geometry for {name}')
                mesh = trimesh.util.concatenate(pieces)
                mesh.apply_transform(np.linalg.inv(transforms[name]))
                # Exact duplicate vertices arise at STL/material seams, not repair.
                vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
                actual = mesh_solid(trimesh.Trimesh(vertices, inverse[mesh.faces], process=False))
                reference = solids[name]
                difference = abs((actual-reference).volume()) + abs((reference-actual).volume())
                passed = difference <= manifest['numeric_tolerance']['volume_mm3']
                checks.append(dict(file=filename, part_id=name, status='PASS' if passed else 'FAIL',
                                   symmetric_difference_mm3=difference))
                if not passed:
                    manifest['failures'].append(dict(code='EXPORTED_FILE_GEOMETRY_MISMATCH',
                        file=filename, part_id=name, symmetric_difference_mm3=difference))
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            checks.append(dict(file=filename, status='FAIL', reason=str(error)[:300]))
            manifest['failures'].append(dict(code='EXPORTED_FILE_INVALID', file=filename, reason=str(error)[:300]))


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def export_assembly(assembly: FixedAssembly, output: Path, *, source_sha256: str, expected: dict):
    """Export all evidence even when geometric checks reject the candidate."""
    import manifold3d as mf
    start = time.monotonic()
    assembly.validate()
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(source_sha256=source_sha256, mm_per_unit=assembly.mm_per_unit,
        stl_vertex_unit='mm', root_id=assembly.root_id, parts=[], interfaces=[], failures=[],
        status='RUNNING', verification_scope='interface_geometry_only',
        unverified=['insertion_path', 'press_fit_retention', 'load_bearing', 'printability'],
        physical_checkers={name: 'NOT_EXECUTED' for name in ('topology', 'standing', 'overhang', 'fea')},
        backend={'boolean':'Blender', 'within_part_union':'Manifold',
                 'manifold_version':importlib.metadata.version('manifold3d')})
    target = output / 'assembly_manifest.json'
    _write(target, manifest)
    def require(condition, code, **location):
        if not condition:
            manifest['failures'].append(dict(code=code, **location))
    require(assembly.mm_per_unit == expected['mm_per_unit'], 'SCALE_CHANGED')
    plan = expected.get('assembly_plan')
    if plan:
        require(assembly.root_id == plan['root_part'], 'ROOT_CHANGED')
        actual_parts = {k: sorted(v) for k, v in assembly.components.items()}
        require(actual_parts == {p['id']: sorted(p['components']) for p in plan['print_parts']}, 'PART_MEMBERSHIP_CHANGED')
        actual_links = [{k:c[k] for k in ('id','tab_part','slot_part','tab_port','slot_port','parameter_name')}
                        for c in assembly.connections]
        planned_links = [{k:c[k] for k in ('id','tab_part','slot_part','tab_port','slot_port','parameter_name')}
                         for c in plan['connections']]
        require(actual_links == planned_links, 'CONNECTION_PLAN_CHANGED')
    meshes, solids, bodies = {}, {}, {}
    # Float32 Blender coordinates: a fixed representation-error bound, not fit.
    for name, part in assembly.parts.items():
        manifest['stage'] = f'part:{name}'
        _write(target, manifest)
        try:
            mesh, solid, diagnostics = evaluated(part, output/f'{name}.glb', assembly.mm_per_unit, keep_materials=True)
            _, body, _ = evaluated(assembly.bodies[name], output/f'{name}.body.glb', assembly.mm_per_unit)
            meshes[name], solids[name], bodies[name] = mesh, solid, body
            components = len(mesh.split(only_watertight=False, repair=False))
            require(components == 1, 'DISCONNECTED_PRINT_PART', part_id=name, components=components)
            print_transform = np.eye(4)
            print_transform[2, 3] = -float(mesh.bounds[0, 2])
            printed = mesh.copy()
            printed.apply_transform(print_transform)
            stl = output/f'{name}.stl'
            printed.export(stl)
            manifest['parts'].append(dict(id=name, components=assembly.components[name], stl=stl.name,
                glb=f'{name}.glb', assembly_transform=assembly.transforms[name].tolist(),
                print_transform_mm=print_transform.tolist(), volume_mm3=float(mesh.volume),
                bounds_mm=mesh.bounds.tolist(), closed=bool(mesh.is_watertight), connected_components=components,
                zero_area_faces=int(np.count_nonzero(mesh.area_faces <= 0)), **diagnostics))
        except (ValueError, RuntimeError) as error:
            manifest['failures'].append(dict(code='PART_GEOMETRY_INVALID', part_id=name, reason=str(error)[:300]))
    if len(solids) != len(assembly.parts):
        manifest.update(status='FAIL', elapsed_seconds=time.monotonic()-start)
        _write(target, manifest)
        return manifest
    max_coord = max(1.0, *(float(np.max(np.abs(m.vertices))) for m in meshes.values()))
    length_tol = float(16 * np.finfo(np.float32).eps * max_coord)
    volume_tol = max(float(m.area) for m in meshes.values()) * length_tol
    manifest['numeric_tolerance'] = dict(length_mm=length_tol, volume_mm3=volume_tol,
        source='16 * float32 epsilon * max local coordinate; volume bound = max part surface area * length bound')
    expected_solids = dict(bodies)
    world_tabs = {}
    for connection in assembly.connections:
        cid, tab_id, slot_id = connection['id'], connection['tab_part'], connection['slot_part']
        manifest['stage'] = f'interface:{cid}'
        _write(target, manifest)
        _, tab, _ = evaluated(connection['tab_solid'], output/f'{cid}.tab.glb', assembly.mm_per_unit)
        _, cutter, _ = evaluated(connection['slot_cutter'], output/f'{cid}.cutter.glb', assembly.mm_per_unit)
        added = (tab - bodies[tab_id]).volume()
        removed = (bodies[slot_id] ^ cutter).volume()
        attached = (tab ^ bodies[tab_id]).volume()
        require(added > volume_tol, 'TAB_NOT_EXPOSED', interface_id=cid)
        require(removed > volume_tol, 'SLOT_NOT_CUT_INTO_BODY', interface_id=cid)
        require(attached > volume_tol, 'TAB_ROOT_NOT_EMBEDDED', interface_id=cid)
        p = connection['parameters']
        require(p['fit_offset_mm'] == expected['fit_offset_mm'], 'FIT_ALLOWANCE_CHANGED', interface_id=cid)
        sf_mm, tf_mm = (np.array(connection[k], copy=True) for k in ('slot_frame','tab_frame'))
        sf_mm[:3,3] *= assembly.mm_per_unit
        tf_mm[:3,3] *= assembly.mm_per_unit
        slot_profile = [p['width_mm']+2*p['fit_offset_mm'], p['thickness_mm']+2*p['fit_offset_mm']]
        cavity = mf.Manifold.cube((*slot_profile,p['slot_depth_mm']), center=True).translate(
            (0,0,p['slot_depth_mm']/2)).transform(sf_mm[:3,:])
        opening = mf.Manifold.cube((*slot_profile,p['opening_extension_mm']), center=True).translate(
            (0,0,-p['opening_extension_mm']/2)).transform(sf_mm[:3,:])
        root = mf.Manifold.cube((p['width_mm'],p['thickness_mm'],p['root_overlap_mm']), center=True).translate(
            (0,0,-p['root_overlap_mm']/2)).transform(tf_mm[:3,:])
        require((cavity-bodies[slot_id]).volume() <= volume_tol, 'INSUFFICIENT_SLOT_MOUNT_MATERIAL', interface_id=cid)
        require((opening^bodies[slot_id]).volume() <= volume_tol, 'SLOT_MOUTH_NOT_AT_STOP_PLANE', interface_id=cid)
        require((root-bodies[tab_id]).volume() <= volume_tol, 'INCOMPLETE_TAB_ROOT_EMBEDDING', interface_id=cid)
        expected_solids[tab_id] = expected_solids[tab_id] + tab
        expected_solids[slot_id] = expected_solids[slot_id] - cutter
        tab_world = assembly.transforms[tab_id] @ np.asarray(connection['tab_frame'])
        slot_world = assembly.transforms[slot_id] @ np.asarray(connection['slot_frame'])
        require(np.allclose(tab_world, slot_world, rtol=0, atol=1e-8), 'MATE_FRAME_MISMATCH', interface_id=cid)
        world_matrix = np.array(assembly.transforms[tab_id], copy=True)
        world_matrix[:3, 3] *= assembly.mm_per_unit
        world_tabs[cid] = tab.transform(world_matrix[:3, :])
        manifest['interfaces'].append({k:v for k,v in connection.items() if k not in ('tab_solid','slot_cutter')} |
            dict(added_tab_mm3=added, removed_slot_mm3=removed, embedded_root_mm3=attached,
                 fit_kind='nominal_interference' if connection['parameters']['fit_offset_mm'] < 0 else 'clearance'))
    for name, actual in solids.items():
        expected_solid = expected_solids[name]
        disagreement = (expected_solid - actual).volume() + (actual - expected_solid).volume()
        require(disagreement <= volume_tol, 'EXPORTED_INTERFACE_GEOMETRY_MISMATCH', part_id=name,
                symmetric_difference_mm3=disagreement)
    world = {}
    for name, solid in solids.items():
        matrix = np.array(assembly.transforms[name], copy=True)
        matrix[:3, 3] *= assembly.mm_per_unit
        world[name] = solid.transform(matrix[:3, :])
    for a, b in itertools.combinations(world, 2):
        overlap = world[a] ^ world[b]
        for c in assembly.connections:
            if {c['tab_part'], c['slot_part']} == {a, b} and c['parameters']['fit_offset_mm'] < 0:
                overlap = overlap - world_tabs[c['id']]
        require(overlap.volume() <= volume_tol, 'UNDECLARED_PART_INTERFERENCE', parts=[a,b], volume_mm3=overlap.volume())
    bounds = np.array([solid.bounding_box() for solid in world.values()])
    extent = np.max(bounds[:, 3:], axis=0) - np.min(bounds[:, :3], axis=0)
    require(np.all(np.abs(extent - np.asarray(expected['final_size_mm'])) <= length_tol*4),
            'FINAL_SIZE_MISMATCH', actual_mm=extent.tolist(), expected_mm=expected['final_size_mm'])
    # One canonical local exterior per part, scoped to this export/candidate.
    # Only rigid placement differs between standalone, assembly and exploded GLB.
    manifest['stage'] = 'write_and_verify_final_meshes'
    _write(target, manifest)
    exploded = {name:np.array(matrix, copy=True) for name,matrix in assembly.transforms.items()}
    for i, part in enumerate(manifest['parts']):
        name = part['id']
        exploded[name][0,3] += i*float(np.max(extent))*1.25/assembly.mm_per_unit
        part['exploded_transform'] = exploded[name].tolist()
        _write_mesh_glb({name:meshes[name]}, {name:np.eye(4)}, output/part['glb'], assembly.mm_per_unit)
    _write_mesh_glb(meshes, assembly.transforms, output/'scene.glb', assembly.mm_per_unit)
    _write_mesh_glb(meshes, exploded, output/'exploded.glb', assembly.mm_per_unit)
    _verify_written_exports(output, manifest, solids)
    manifest.update(status='PASS' if not manifest['failures'] else 'FAIL', stage='complete',
                    elapsed_seconds=time.monotonic()-start, assembled_size_mm=extent.tolist())
    manifest['files_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.iterdir() if p.suffix in ('.glb','.stl')}
    _write(target, manifest)
    return manifest
