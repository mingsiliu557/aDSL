"""Real Boolean export and bounded geometric evidence for fixed assembly v1.

Called inside the existing isolated asset executor (120-second geometry budget).
Mesh64 recursively evaluates CSG; independent pieces union only WITHIN each print part.
The shared Blender exporter normalizes exact-zero edges/polygon tessellation and
validated local numerical boundary cracks within each evaluated object.
No cross-part welding, remeshing, solver checks or fit-tolerance tuning is performed.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import itertools
import json
from pathlib import Path
import time

import numpy as np

from ..assembly import FixedAssembly, _check_world_frames
from .export_glb import export_glb


def mesh_solid(mesh, face_ids=None):
    from .mesh_validity import validate_mesh
    solid, _ = validate_mesh(mesh.vertices, mesh.faces, face_ids=face_ids)
    return solid


def solid_mesh(solid):
    import manifold3d as mf
    import trimesh
    if solid.status() != mf.Error.NoError:
        raise ValueError(f"Manifold Boolean failed: {solid.status()}")
    raw = solid.to_mesh64()
    return trimesh.Trimesh(np.asarray(raw.vert_properties)[:, :3], np.asarray(raw.tri_verts), process=False)


def evaluated(shape, path, mm_per_unit, *, keep_materials=False, validate_geometry=True,
              _display_cache=None, _write_display=True):
    """Consume canonical Mesh64 directly, unioning only this declared part.

    Directed inner cavity shells stay in the same Manifold. This function never
    obtains checker/print geometry from Blender or a previously exported GLB.
    The historical return triple and visual-only manufacturing status remain.
    """
    import manifold3d as mf
    from .mesh64 import evaluate_shape, _nearby_frame
    from .mesh_validity import checked_solid, validate_mesh
    evaluation = evaluate_shape(shape, mm_per_unit=mm_per_unit)
    pieces = [p for p in evaluation.pieces if not p.solid.is_empty()]
    if not pieces:
        from .mesh_validity import MeshEvaluationError
        raise MeshEvaluationError('EMPTY_REQUIRED_GEOMETRY', 'empty evaluated part')
    anchor = _nearby_frame(pieces)
    inverse = np.linalg.inv(anchor)
    inputs = [p.solid.transform(np.ascontiguousarray((inverse @ p.transform)[:3], dtype=np.float64))
              for p in pieces]
    merged = mf.Manifold.batch_boolean(inputs, mf.OpType.Add)
    checked_solid(merged, node_path=str(shape.label or 'part'), operation='WITHIN_PART_UNION',
                  input_count=len(inputs))
    # Manufacturing consumes the Mesh64 result directly. A display conversion
    # may simplify/quantize its own copy, but cannot change this canonical mesh.
    linear = anchor.copy(); linear[:3, 3] = 0.0
    merged = merged.transform(np.ascontiguousarray(linear[:3], dtype=np.float64))
    merged = merged.scale((mm_per_unit,)*3)
    raw = merged.to_mesh64()
    import trimesh
    vertices = np.asarray(raw.vert_properties, dtype=np.float64)[:, :3] + anchor[:3, 3]*mm_per_unit
    mesh = trimesh.Trimesh(vertices,
                          np.asarray(raw.tri_verts, dtype=np.int64), process=False)
    mesh.face_attributes['source_face_id'] = np.asarray(raw.face_id, dtype=np.uint64)
    mesh.face_attributes['material'] = mesh.face_attributes['source_face_id'].astype(np.int64)
    mesh.metadata['materials'] = evaluation.materials
    canonical, metrics = validate_mesh(mesh.vertices, mesh.faces,
        face_ids=mesh.face_attributes['material'], stage='canonical_geometry')
    # Serialization only instantiates this exact mesh, without re-evaluating CSG.
    display = (_display_export({str(shape.label or 'part'):mesh},
        {str(shape.label or 'part'):np.eye(4)}, Path(path), mm_per_unit, _display_cache)
        if _write_display else dict(status='NOT_EXECUTED', file=None,
            reason='Internal canonical measurement does not require a display file'))
    normalizations = [dict(r) for r in evaluation.records if r.get('method') in
        ('planar_polygon_retriangulation', 'exact_zero_length_edge_cleanup', 'local_boundary_weld')]
    diagnostics = dict(input_shells=len(pieces), exact_duplicate_vertices_merged=0,
        proximity_welding=any(r.get('method') == 'local_boundary_weld' and r.get('status') == 'APPLIED'
                            for r in normalizations), mesh_face_groups=[[0,len(mesh.faces)]],
        manufacturing_status='PASS', manufacturing_geometry_valid=True,
        display_status=display['status'], display_export=display,
        display_failures=[display['diagnostic']] if display['status']=='FAIL' else [],
        display_complete=display['status']=='PASS', omitted_mesh_nodes=[],
        internal_evaluation=dict(status='PASS', method='recursive_manifold_mesh64',
            precision='float64', records=evaluation.records,
            self_intersection='NOT_EVALUATED'),
        target_precision=display.get('target_precision', display.get('diagnostic')),
        canonical_mesh=metrics, mesh_normalizations=normalizations,
        file_validation=dict(status=display['status'], stage='file_readback',
                             file=display['file'], output_role='display'),
        geometry_validation='PASS' if validate_geometry else 'NOT_EVALUATED')
    return mesh, canonical if validate_geometry else None, diagnostics


def _write_mesh_glb(meshes, transforms, path, mm_per_unit, *, display_cache=None):
    """Convert display copies only; canonical manufacturing meshes stay intact."""
    import bpy
    import mathutils
    import trimesh
    from .export_glb import _instantiate_mesh, _patch_glb_transforms, _verify_glb_meshes
    from .mesh_validity import target_mesh, MeshEvaluationError
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    matrices, expected, conversions = {}, {}, {}
    for name, mesh in meshes.items():
        material_ids = np.asarray(mesh.face_attributes['material'], dtype=np.uint64)
        digest = hashlib.sha256()
        for values in (mesh.vertices, mesh.faces, material_ids, np.asarray([mm_per_unit])):
            digest.update(np.ascontiguousarray(values).tobytes())
        key = digest.hexdigest()
        prepared = display_cache.get(key) if display_cache is not None else None
        if isinstance(prepared, dict):
            diagnostic = dict(prepared)
            code, message = diagnostic.pop('code'), diagnostic.pop('message')
            diagnostic.update(part_id=name, node_path=name, cached_conversion=True)
            raise MeshEvaluationError(code, message, **diagnostic)
        if prepared is None:
            display_input = mesh.copy()
            display_input.vertices = np.asarray(mesh.vertices, dtype=np.float64)/mm_per_unit
            display_solid = mesh_solid(display_input, face_ids=material_ids)
            try:
                prepared = target_mesh(display_solid, mm_per_unit=mm_per_unit,
                    part_id=name, node_path=name, output_role='display')
            except MeshEvaluationError as error:
                if display_cache is not None:
                    display_cache[key] = dict(error.diagnostic)
                raise
            if display_cache is not None:
                display_cache[key] = prepared
        local_mesh, recenter, conversion = prepared
        local_mesh = local_mesh.copy()
        conversions[name] = conversion
        local_mesh.metadata['materials'] = mesh.metadata['materials']
        local_mesh.face_attributes['material'] = local_mesh.face_attributes['source_face_id'].astype(np.int64)
        parent = bpy.data.objects.new(name, None)
        parent['adsl_print_part_id'] = name
        bpy.context.collection.objects.link(parent)
        matrices[name] = np.asarray(transforms[name], dtype=np.float64)
        parent.matrix_world = mathutils.Matrix(matrices[name].tolist())
        obj = _instantiate_mesh(local_mesh, name+'_mesh', parent, recenter)
        matrices[obj.name] = recenter
        expected[obj.name] = len(local_mesh.faces)
    bpy.ops.export_scene.gltf(filepath=str(path), export_format='GLB', export_yup=True,
        export_apply=False, export_extras=True, export_draco_mesh_compression_enable=False)
    _patch_glb_transforms(path, matrices)
    _verify_glb_meshes(path, expected)
    return conversions


def _display_export(meshes, transforms, path, mm_per_unit, display_cache=None):
    """Record display failure without discarding valid manufacturing geometry."""
    try:
        conversions = (_write_mesh_glb(meshes, transforms, path, mm_per_unit,
                                     display_cache=display_cache) if display_cache is not None
                       else _write_mesh_glb(meshes, transforms, path, mm_per_unit))
        return dict(status='PASS', file=Path(path).name, target_precision=conversions,
                    file_validation='PASS')
    except (ValueError, RuntimeError, OSError, ImportError) as error:
        diagnostic = dict(getattr(error, 'diagnostic', {}) or dict(
            code='DISPLAY_EXPORT_FAILED', stage='display_export', failure_kind='export', message=str(error)))
        diagnostic.update(output_role='display', file=Path(path).name)
        Path(path).unlink(missing_ok=True)
        return dict(status='FAIL', file=None, diagnostic=diagnostic, file_validation='FAIL')


def _display_failure(manifest, failure, output=None):
    """Display diagnostics never certify or invalidate manufacturing files."""
    failure = dict(failure, output_role='display')
    rows = manifest.setdefault('display_failures', [])
    if failure not in rows:
        rows.append(failure)
    manifest['display_status'] = 'FAIL'
    for part in manifest.get('parts', []):
        if part.get('glb') == failure.get('file'):
            part.update(glb=None, display_status='FAIL', display_complete=False)
            part.setdefault('display_failures', []).append(failure)
            part['display_export'] = dict(part.get('display_export', {}),
                status='FAIL', file=None, readback_failure=failure)
            if 'diagnostic' in manifest:
                manifest['diagnostic']['display_available'] = False
    for key in ('scene_glb', 'exploded_glb'):
        if manifest.get(key) == failure.get('file'):
            manifest[key] = None
            manifest.setdefault('display_exports', {})[key] = dict(
                manifest.get('display_exports', {}).get(key, {}),
                status='FAIL', file=None, readback_failure=failure)
    if failure.get('file') == 'scene.glb' and 'diagnostic' in manifest:
        manifest['diagnostic']['display_available'] = False
    if output is not None and failure.get('file'):
        path = (Path(output)/failure['file']).resolve()
        if path.parent == Path(output).resolve() and path.suffix == '.glb':
            try:
                path.unlink(missing_ok=True)
            except OSError as error:
                failure['cleanup_error'] = str(error)[:240]


def _serialization_triangles(mesh):
    """Compare surface sets, not redundant encoding; never alter the mesh.

    Blender GLB serialization can omit exact zero-area and duplicate triangles.
    Keep those defects in the assets for topology; their multiplicity does not
    describe a different displayed surface. Positive-area triangles, however
    small, remain subject to the existing coordinate tolerance.
    """
    triangles = np.asarray(mesh.triangles)
    if not np.isfinite(triangles).all():
        raise ValueError('non-finite serialized triangle coordinates')
    cross = np.cross(triangles[:,1]-triangles[:,0], triangles[:,2]-triangles[:,0])
    nonzero = np.any(cross != 0, axis=1)
    usable = triangles[nonzero]
    order = np.lexsort((usable[:,:,2], usable[:,:,1], usable[:,:,0]), axis=1)
    canonical = np.take_along_axis(usable, order[:,:,None], axis=1)
    unique = np.unique(canonical.reshape(-1,9), axis=0).reshape(-1,3,3)
    return unique, dict(exact_zero_area_faces=int((~nonzero).sum()),
                       exact_duplicate_surface_faces=len(usable)-len(unique),
                       surface_triangles=len(unique), mesh_modified=False)


def _verify_written_exports(output, manifest, solids, *, surface_meshes=None,
                            compare_triangles=False, compare_placement=False):
    """Check readable, nonempty part files; numerical comparisons are regression-only."""
    import trimesh
    z_up = np.array([[1,0,0,0], [0,0,-1,0], [0,1,0,0], [0,0,0,1.]])
    unit = manifest['mm_per_unit']
    parts = {p['id']:p for p in manifest['parts']}
    checks = manifest['export_consistency'] = []
    manifest['triangle_comparison'] = 'REGRESSION_ONLY' if compare_triangles else 'NOT_EXECUTED'
    manifest['placement_comparison'] = 'REGRESSION_ONLY' if compare_placement else 'NOT_EXECUTED'
    files = [(p['stl'], {name: np.asarray(p['print_transform_mm'])}, False) for name,p in parts.items()]
    for name, part in parts.items():
        if part.get('glb'):
            files.append((part['glb'], {name:np.eye(4)}, True))
        else:
            checks.append(dict(file=f'{name}.glb', part_id=name, status='NOT_EVALUATED',
                               output_role='display', reason='Display conversion unavailable'))
    for filename, field in [('scene.glb','assembly_transform'), ('exploded.glb','exploded_transform')]:
        transforms = {name:np.array(p[field], dtype=float) for name,p in parts.items()}
        for transform in transforms.values():
            transform[:3,3] *= unit
        manifest_key = 'scene_glb' if filename == 'scene.glb' else 'exploded_glb'
        if manifest.get(manifest_key, filename):
            files.append((filename, transforms, True))
        else:
            checks.append(dict(file=filename, status='NOT_EVALUATED', output_role='display',
                               reason='Display conversion unavailable'))
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
                from .mesh_validity import validate_mesh
                actual, file_metrics = validate_mesh(mesh.vertices, mesh.faces,
                    stage='file_readback', part_id=name, file=filename)
                checks.append(dict(file=filename, part_id=name, status='PASS',
                    output_role='display' if glb else 'manufacturing',
                    scope='mesh_validity', metrics=file_metrics,
                    self_intersection='NOT_EVALUATED'))
                if surface_meshes is not None:
                    reference = surface_meshes[name]
                    if not len(mesh.vertices) or not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
                        raise ValueError(f'empty or nonfinite saved geometry for {name}')
                    if compare_placement:
                        tolerance = manifest['numeric_tolerance']['length_mm']
                        bounds_error = float(np.max(np.abs(mesh.bounds-reference.bounds)))
                        pose_error = 0.0
                        if glb:
                            actual_pose = z_up @ scene.graph[name][0] @ np.linalg.inv(z_up)
                            actual_pose[:3,3] *= unit
                            delta = actual_pose-transforms[name]
                            scale = max(1., float(np.max(np.abs(reference.vertices))))
                            pose_error = max(float(np.max(np.abs(delta[:3,3]))),
                                             float(np.max(np.abs(delta[:3,:3])))*scale)
                        passed = bounds_error <= tolerance and pose_error <= tolerance
                        checks.append(dict(file=filename, part_id=name, status='PASS' if passed else 'FAIL',
                            scope='placement_regression_only', local_bounds_deviation_mm=bounds_error,
                            placement_deviation_mm=pose_error))
                        if not passed:
                            failure = dict(code='EXPORTED_FILE_PLACEMENT_OR_SCALE_MISMATCH',
                                file=filename, part_id=name, local_bounds_deviation_mm=bounds_error,
                                placement_deviation_mm=pose_error)
                            _display_failure(manifest, failure, output) if glb else manifest['failures'].append(failure)
                    if not compare_triangles:
                        if not compare_placement:
                            checks.append(dict(file=filename, part_id=name, status='PASS',
                                               scope='file_structure_only'))
                        continue
                    # Serialization consistency only: compare surface triangles,
                    # allowing redundant encoding and the existing float32 bound.
                    # This does not test closedness, connectivity or interface fit.
                    from scipy.spatial import cKDTree
                    a, actual_encoding = _serialization_triangles(mesh)
                    b, reference_encoding = _serialization_triangles(reference)
                    def distance(left, right):
                        variants = np.concatenate([right[:,p,:].reshape(-1,9)
                            for p in itertools.permutations(range(3))])
                        return float(cKDTree(variants).query(left.reshape(-1,9))[0].max())
                    deviation = max(distance(a,b), distance(b,a)) if len(a) and len(b) else float('inf')
                    passed = len(a)==len(b) and deviation <= manifest['numeric_tolerance']['length_mm']*np.sqrt(3)
                    checks.append(dict(file=filename, part_id=name, status='PASS' if passed else 'FAIL',
                        triangle_coordinate_deviation_mm=deviation, scope='serialization_only',
                        actual_encoding=actual_encoding, reference_encoding=reference_encoding))
                    if not passed:
                        failure = dict(code='EXPORTED_FILE_GEOMETRY_MISMATCH',
                            file=filename, part_id=name, triangle_coordinate_deviation_mm=deviation)
                        _display_failure(manifest, failure, output) if glb else manifest['failures'].append(failure)
                    continue
                # Exact duplicate vertices arise at STL/material seams, not repair.
                vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
                actual = mesh_solid(trimesh.Trimesh(vertices, inverse[mesh.faces], process=False))
                reference = solids[name]
                difference = abs((actual-reference).volume()) + abs((reference-actual).volume())
                passed = difference <= manifest['numeric_tolerance']['volume_mm3']
                checks.append(dict(file=filename, part_id=name, status='PASS' if passed else 'FAIL',
                                   symmetric_difference_mm3=difference))
                if not passed:
                    failure = dict(code='EXPORTED_FILE_GEOMETRY_MISMATCH',
                        file=filename, part_id=name, symmetric_difference_mm3=difference)
                    _display_failure(manifest, failure, output) if glb else manifest['failures'].append(failure)
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            checks.append(dict(file=filename, status='FAIL',
                output_role='display' if glb else 'manufacturing', reason=str(error)[:300]))
            failure = dict(code='EXPORTED_FILE_INVALID', file=filename,
                stage='read_written_exports', failure_kind='export', reason=str(error)[:300])
            _display_failure(manifest, failure, output) if glb else manifest['failures'].append(failure)
    required = {p['id'] for p in manifest.get('part_declarations', manifest['parts'])}
    printed = {row['part_id'] for row in checks if row.get('output_role') == 'manufacturing'
               and row.get('scope') == 'mesh_validity' and row['status'] == 'PASS'}
    stl_files = {p['stl'] for p in parts.values()}
    stl_failures = any(row['status'] == 'FAIL' and row.get('file') in stl_files for row in checks)
    manifest['manufacturing_geometry_valid'] = required == set(parts) and all(
        p.get('manufacturing_geometry_valid', True) for p in parts.values())
    manifest['manufacturing_status'] = 'PASS' if (
        manifest['manufacturing_geometry_valid'] and printed == required and not stl_failures) else 'FAIL'
    manifest['manufacturing_file_validation'] = [row for row in checks
        if row.get('output_role') == 'manufacturing']
    for name, part in parts.items():
        part['manufacturing_status'] = 'PASS' if (name in printed and not any(
            row['status']=='FAIL' and row.get('file')==part['stl'] for row in checks)) else 'FAIL'


def _write(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2))
    temporary.replace(path)


def _diagnostic_view(assembly, output, meshes, manifest, display_cache=None):
    """Place saved candidate meshes without CSG, repair or certification."""
    import trimesh
    scene = trimesh.Scene()
    z_to_y = np.array([[1,0,0,0], [0,0,1,0], [0,-1,0,0], [0,0,0,1.]])
    parts = []
    for name in assembly.parts:
        visual_only = manifest.get('verification_scope') == 'visual_code_only'
        row = {'id':name, 'geometry_valid':None if visual_only else name in meshes, 'shown':False}
        try:
            path = output/f'{name}.glb'
            if name in meshes:
                path = output/f'{name}.diagnostic.glb'
                _write_mesh_glb({name:meshes[name]}, {name:np.eye(4)}, path, assembly.mm_per_unit,
                                display_cache=display_cache)
            saved = trimesh.load(path, force='scene', process=False)
            placement = z_to_y @ assembly.transforms[name] @ np.linalg.inv(z_to_y)
            omitted = []
            for node in saved.graph.nodes_geometry:
                transform, key = saved.graph[node]
                mesh = saved.geometry[key].copy()
                if (not isinstance(mesh, trimesh.Trimesh) or not len(mesh.faces)
                        or not np.isfinite(mesh.vertices).all()):
                    omitted.append(str(node))
                    continue
                scene.add_geometry(mesh, node_name=f'{name}/{node}', transform=placement @ transform)
                row['shown'] = True
            if omitted:
                row['omitted_mesh_nodes'] = omitted
        except (ValueError, RuntimeError, OSError, KeyError) as error:
            row['reason'] = str(error)[:240]
        parts.append(row)
    diagnostic = {'diagnostic_only':True, 'parts':parts,
        'invalid_parts':[p['id'] for p in parts if p['geometry_valid'] is False],
        'missing_parts':[p['id'] for p in parts if not p['shown']],
        'display_available':all(p['shown'] and (visual_only or p['geometry_valid']) and not p.get('omitted_mesh_nodes') for p in parts),
        'semantic_completeness':'NOT_EVALUATED',
        'glb':None}
    if scene.geometry:
        scene.metadata['diagnostic_only'] = True
        path = output/'diagnostic_scene.glb'
        scene.export(path)
        diagnostic['glb'] = path.name
    else:
        diagnostic['reason'] = 'No finite nonempty candidate mesh available for display'
    _write(output/'diagnostic.json', diagnostic)
    manifest['diagnostic'] = diagnostic
    _write(output/'assembly_manifest.json', manifest)
    return diagnostic


def _plan_delta(initial, actual):
    """Small declaration diff, not an approval or a geometry check."""
    before = {row['id']:row for row in initial}
    after = {row['id']:row for row in actual}
    return {
        'added':[after[key] for key in sorted(after.keys()-before.keys())],
        'removed':[before[key] for key in sorted(before.keys()-after.keys())],
        'changed':[{'id':key, 'initial':before[key], 'actual':after[key]}
                   for key in sorted(before.keys() & after.keys()) if before[key] != after[key]],
    }


def export_assembly(assembly: FixedAssembly, output: Path, *, source_sha256: str, expected: dict):
    """Export all evidence even when geometric checks reject the candidate."""
    visual_only = expected.get('validation_mode', 'geometry') == 'visual_only'
    if not visual_only:
        import manifold3d as mf
    start = time.monotonic()
    assembly.validate()
    output.mkdir(parents=True, exist_ok=True)
    manifest = dict(source_sha256=source_sha256, mm_per_unit=assembly.mm_per_unit,
        stl_vertex_unit='mm', root_id=assembly.root_id, parts=[], interfaces=[], failures=[],
        # Actual declarations survive a failed mesh export too. They do not
        # certify the selected bodies or duplicate the mesh validity results.
        part_declarations=[dict(id=name, components=list(assembly.components[name]),
                                assembly_transform=assembly.transforms[name].tolist())
                           for name in assembly.parts],
        connections=[{key:connection[key] for key in
            ('id','tab_part','slot_part','tab_port','slot_port','parameter_name',
             'parameters','tab_frame','slot_frame')} for connection in assembly.connections],
        status='RUNNING', manufacturing_status='RUNNING', manufacturing_geometry_valid=None,
        display_status='RUNNING', display_failures=[], scene_glb=None, exploded_glb=None,
        verification_scope='interface_geometry_only',
        unverified=['insertion_path', 'press_fit_retention', 'load_bearing', 'printability'],
        physical_checkers={name: 'NOT_EXECUTED' for name in ('topology', 'standing', 'overhang', 'fea')},
        backend={'boolean':'Manifold Mesh64', 'within_part_union':'Manifold Mesh64',
                 'manifold_version':importlib.metadata.version('manifold3d')})
    target = output / 'assembly_manifest.json'
    if visual_only:
        manifest.update(verification_scope='visual_code_only', geometry_validation='NOT_EVALUATED',
            unverified=manifest['unverified']+['closedness','connectivity','interface_geometry','interference','dimensions'])
        manifest['backend'].update(boolean='Manifold Mesh64', within_part_union='Manifold Mesh64')
    _write(target, manifest)
    def require(condition, code, **location):
        if not condition:
            manifest['failures'].append(dict(code=code, stage=manifest.get('stage','declaration'),
                failure_kind='candidate_geometry', **location))
    if expected.get('require_multiple_parts', False):
        manifest['task_constraints'] = {'require_multiple_parts':True}
        require(len(assembly.parts) >= 2 and len(assembly.connections) >= 1,
                'MULTIPART_ASSEMBLY_REQUIRED', part_count=len(assembly.parts),
                connection_count=len(assembly.connections),
                reason='This task requires at least two print parts and one connector; grouping may change.')
    require(assembly.mm_per_unit == expected['mm_per_unit'], 'SCALE_CHANGED')
    # Frozen input contract, not a geometric test of the generated fit.
    for connection in assembly.connections if visual_only else ():
        require(connection['parameters']['fit_offset_mm'] == expected['fit_offset_mm'],
                'FIT_ALLOWANCE_CHANGED', interface_id=connection['id'])
    plan = expected.get('assembly_plan')
    if plan:
        require(assembly.root_id == plan['root_part'], 'ROOT_CHANGED')
        actual_parts = [dict(id=k, components=sorted(v)) for k,v in assembly.components.items()]
        planned_parts = [dict(id=p['id'], components=sorted(p['components'])) for p in plan['print_parts']]
        actual_links = [{k:c[k] for k in ('id','tab_part','slot_part','tab_port','slot_port','parameter_name')}
                        for c in assembly.connections]
        planned_links = [{k:c[k] for k in ('id','tab_part','slot_part','tab_port','slot_port','parameter_name')}
                         for c in plan['connections']]
        parts_delta = _plan_delta(planned_parts, actual_parts)
        links_delta = _plan_delta(planned_links, actual_links)
        order_changed = [c['id'] for c in actual_links] != [c['id'] for c in planned_links]
        codes = (['PART_MEMBERSHIP_CHANGED'] if any(parts_delta.values()) else [])
        if any(links_delta.values()) or order_changed:
            codes.append('CONNECTION_PLAN_CHANGED')
        manifest['plan_changes'] = dict(status='CHANGED' if codes else 'UNCHANGED', codes=codes,
            parts=parts_delta, connections=links_delta, connection_order_changed=order_changed)
        manifest['initial_plan_sha256'] = hashlib.sha256(json.dumps(plan, sort_keys=True).encode()).hexdigest()
    meshes, solids, bodies, display_cache = {}, {}, {}, {}
    # Float32 Blender coordinates: a fixed representation-error bound, not fit.
    for name, part in assembly.parts.items():
        manifest['stage'] = f'part:{name}'
        _write(target, manifest)
        part_stage = 'evaluate_part'
        try:
            if visual_only:
                mesh, _, diagnostics = evaluated(part, output/f'{name}.glb', assembly.mm_per_unit,
                    keep_materials=True, validate_geometry=False, _display_cache=display_cache)
                meshes[name] = mesh
                part_stage = 'write_print_mesh'
                print_transform = assembly.print_rotation(name)
                print_transform[2,3] = -float((mesh.vertices @ print_transform[:3,:3].T)[:,2].min())
                printed = mesh.copy(); printed.apply_transform(print_transform)
                # Binary STL rounds to float32 after print placement; tiny
                # valid faces can collapse. Preserve coordinates in ASCII.
                printed.export(output/f'{name}.stl', file_type='stl_ascii')
                manifest['parts'].append(dict(id=name, components=assembly.components[name],
                    stl=f'{name}.stl', glb=diagnostics['display_export']['file'],
                    assembly_transform=assembly.transforms[name].tolist(),
                    print_transform_mm=print_transform.tolist(), **diagnostics))
                for failure in diagnostics['display_failures']:
                    _display_failure(manifest, dict(failure, part_id=name))
                continue
            mesh, solid, diagnostics = evaluated(part, output/f'{name}.glb', assembly.mm_per_unit,
                keep_materials=True, _display_cache=display_cache)
            _, body, _ = evaluated(assembly.bodies[name], output/f'{name}.body.glb',
                                   assembly.mm_per_unit, _write_display=False)
            meshes[name], solids[name], bodies[name] = mesh, solid, body
            components = sum(float(component.volume()) > 0 for component in solid.decompose())
            require(components == 1, 'DISCONNECTED_PRINT_PART', part_id=name, components=components)
            part_stage = 'write_print_mesh'
            print_transform = assembly.print_rotation(name)
            print_transform[2, 3] = -float((mesh.vertices @ print_transform[:3,:3].T)[:,2].min())
            printed = mesh.copy()
            printed.apply_transform(print_transform)
            stl = output/f'{name}.stl'
            printed.export(stl, file_type='stl_ascii')
            manifest['parts'].append(dict(id=name, components=assembly.components[name], stl=stl.name,
                glb=diagnostics['display_export']['file'],
                assembly_transform=assembly.transforms[name].tolist(),
                print_transform_mm=print_transform.tolist(), volume_mm3=float(mesh.volume),
                bounds_mm=mesh.bounds.tolist(), closed=bool(mesh.is_watertight), connected_components=components,
                zero_area_faces=int(np.count_nonzero(mesh.area_faces <= 0)), **diagnostics))
            for failure in diagnostics['display_failures']:
                _display_failure(manifest, dict(failure, part_id=name))
        except (ValueError, RuntimeError, OSError) as error:
            from .mesh_validity import MeshEvaluationError
            if isinstance(error, MeshEvaluationError):
                diagnostic = dict(error.diagnostic, part_id=name)
                evaluation_failure = diagnostic.get('failure_kind') in (
                    'input_geometry', 'geometry_evaluation', 'target_precision', 'candidate_evaluation')
                manifest['failures'].append(dict(code=diagnostic['code'], part_id=name,
                    stage=diagnostic.get('stage', part_stage),
                    failure_kind='candidate_evaluation' if evaluation_failure else 'export',
                    reason=str(error)[:300], diagnostic=diagnostic))
            elif isinstance(error, ValueError) and str(error).startswith((
                    'EVALUATED_MESH_DEGENERATE:', 'EVALUATED_MESH_INVALID:', 'BOOLEAN_RECOVERY_FAILED:')):
                code = str(error).partition(':')[0]
                diagnostic = dict(code='BOOLEAN_EVALUATION_FAILED' if code == 'BOOLEAN_RECOVERY_FAILED'
                    else 'INVALID_EVALUATED_MESH', legacy_code=code, stage='internal_evaluation',
                    failure_kind='geometry_evaluation', part_id=name,
                    attempted_actions=['legacy_bounded_normalization_or_boolean_recovery'],
                    message=str(error))
                manifest['failures'].append(dict(code=code, part_id=name,
                    stage='internal_evaluation', failure_kind='candidate_evaluation',
                    diagnostic=diagnostic, reason=str(error)[:300]))
            else:
                geometry = isinstance(error, ValueError) and str(error).startswith((
                    'empty evaluated', 'No finite nonempty mesh available for display',
                    'mesh is not a finite closed oriented volume, or has zero-area faces'))
                kind = ('export' if isinstance(error, OSError) or part_stage == 'write_print_mesh'
                        else 'candidate_geometry' if geometry else 'unknown')
                manifest['failures'].append(dict(code='PART_EXPORT_FAILED' if kind == 'export' else
                    'PART_DISPLAY_UNAVAILABLE' if visual_only else 'PART_GEOMETRY_INVALID',
                    part_id=name, stage=part_stage, failure_kind=kind, reason=str(error)[:300]))
    if expected.get('physics', {}).get('overhang', {}).get('partition_objective') is not None:
        # Reference availability is separate from display/export success.
        manifest['partition_reference_inputs'] = []
        for name in assembly.parts:
            row = dict(part_id=name, status='INDETERMINATE', frame='part_local_mm',
                       source_sha256=source_sha256)
            try:
                body = bodies.get(name)
                if body is None:
                    _, body, _ = evaluated(assembly.bodies[name], output/f'{name}.body.glb',
                                          assembly.mm_per_unit, _write_display=False)
                reference_mesh = solid_mesh(body)
                path = output/f'{name}.body.npz'
                np.savez(path, vertices=np.asarray(reference_mesh.vertices, dtype=np.float64),
                         faces=np.asarray(reference_mesh.faces, dtype=np.int64))
                row.update(status='PASS', npz=path.name,
                    sha256=hashlib.sha256(path.read_bytes()).hexdigest())
            except (ValueError, RuntimeError, OSError, ImportError) as error:
                row['reason'] = f'{type(error).__name__}: {str(error)[:240]}'
            manifest['partition_reference_inputs'].append(row)
    # A failed part must not prevent visual feedback about available geometry.
    try:
        _diagnostic_view(assembly, output, meshes, manifest, display_cache)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        manifest['diagnostic'] = {'diagnostic_only':True, 'display_available':False,
                                  'glb':None, 'reason':str(error)[:240]}
    if visual_only:
        manifest['diagnostic']['omitted_mesh_nodes'] = [
            {'part_id':p['id'], **node} for p in manifest['parts'] for node in p['omitted_mesh_nodes']]
        manifest['diagnostic']['display_available'] = bool(manifest['diagnostic'].get('display_available') and
            len(meshes)==len(assembly.parts) and all(p['display_complete'] for p in manifest['parts']))
        if len(meshes)==len(assembly.parts) and len(manifest['parts'])==len(assembly.parts):
            max_coord = max(1., *(float(np.max(np.abs(m.vertices))) for m in meshes.values()))
            manifest['numeric_tolerance'] = dict(length_mm=float(16*np.finfo(np.float32).eps*max_coord),
                source='16 * float32 epsilon * max local coordinate; serialization only')
            bounds=[]
            for name,mesh in meshes.items():
                world=mesh.copy(); transform=np.array(assembly.transforms[name],copy=True)
                transform[:3,3] *= assembly.mm_per_unit
                world.apply_transform(transform); bounds.append(world.bounds)
            extent=np.max(np.asarray(bounds)[:,1,:],axis=0)-np.min(np.asarray(bounds)[:,0,:],axis=0)
            manifest['stage'] = 'write_and_verify_final_meshes'
            _write(target, manifest)
            try:
                _write_final_meshes(assembly, output, manifest, meshes, extent, display_cache)
                _verify_written_exports(output, manifest, {}, surface_meshes=meshes)
            except (ValueError, RuntimeError, OSError, KeyError) as error:
                from .mesh_validity import MeshEvaluationError
                if isinstance(error, MeshEvaluationError):
                    diagnostic = dict(error.diagnostic)
                    manifest['failures'].append(dict(code=diagnostic['code'],
                        part_id=diagnostic.get('part_id'), stage=diagnostic.get('stage'),
                        failure_kind='candidate_evaluation' if diagnostic.get('failure_kind') in
                            ('input_geometry','geometry_evaluation','target_precision') else 'export',
                        diagnostic=diagnostic, reason=str(error)[:300]))
                else:
                    manifest['failures'].append(dict(code='EXPORTED_FILE_INVALID',
                        stage='write_final_meshes', failure_kind='export', reason=str(error)[:300]))
        manifest.setdefault('manufacturing_file_validation', [])
        if manifest['manufacturing_status'] == 'RUNNING':
            manifest['manufacturing_status'] = 'FAIL'
            manifest['manufacturing_geometry_valid'] = False
        if manifest['display_status'] == 'RUNNING':
            manifest['display_status'] = 'FAIL'
        manifest.update(status='NOT_EVALUATED', export_status='PASS' if (
            not manifest['failures'] and manifest['manufacturing_status']=='PASS'
            and manifest['display_status']=='PASS') else 'FAIL',
            stage='complete', elapsed_seconds=time.monotonic()-start)
        manifest['files_sha256']={p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.suffix in ('.glb','.stl')}
        _write(target,manifest)
        return manifest
    if len(solids) != len(assembly.parts):
        manifest.update(status='FAIL', export_status='FAIL', manufacturing_status='FAIL',
                        manufacturing_geometry_valid=False, display_status='FAIL',
                        elapsed_seconds=time.monotonic()-start)
        manifest['files_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
            for p in output.iterdir() if p.suffix in ('.glb','.stl')}
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
        _, tab, _ = evaluated(connection['tab_solid'], output/f'{cid}.tab.glb',
                              assembly.mm_per_unit, _write_display=False)
        _, cutter, _ = evaluated(connection['slot_cutter'], output/f'{cid}.cutter.glb',
                                 assembly.mm_per_unit, _write_display=False)
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
        residual = _check_world_frames(tab_world, slot_world, assembly.mm_per_unit)
        require(residual['matched'], 'MATE_FRAME_MISMATCH', interface_id=cid,
                tab_part=tab_id, slot_part=slot_id,
                translation_error_mm=residual['translation_error_mm'],
                rotation_error_deg=residual['rotation_error_deg'])
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
    # Local import: assembly_topology also reuses this module's mesh conversion.
    from ..assembly_topology import material_interference
    for a, b in itertools.combinations(world, 2):
        allowed = [world_tabs[c['id']] for c in assembly.connections
            if {c['tab_part'],c['slot_part']} == {a,b} and c['parameters']['fit_offset_mm'] < 0]
        measurement = material_interference(world[a],world[b],allowed,volume_tolerance_mm3=volume_tol)
        require(measurement['status'] == 'PASS', 'UNDECLARED_PART_INTERFERENCE', parts=[a,b],
            volume_mm3=measurement['undeclared_interference_mm3'],
            raw_intersection_mm3=measurement['raw_intersection_mm3'],volume_tolerance_mm3=volume_tol,
            bounds_mm=measurement['bounds_mm'],frame='assembly',method=measurement['method'])
    bounds = np.array([solid.bounding_box() for solid in world.values()])
    extent = np.max(bounds[:, 3:], axis=0) - np.min(bounds[:, :3], axis=0)
    require(np.all(np.abs(extent - np.asarray(expected['final_size_mm'])) <= length_tol*4),
            'FINAL_SIZE_MISMATCH', actual_mm=extent.tolist(), expected_mm=expected['final_size_mm'])
    # One canonical local exterior per part, scoped to this export/candidate.
    # Only rigid placement differs between standalone, assembly and exploded GLB.
    manifest['stage'] = 'write_and_verify_final_meshes'
    _write(target, manifest)
    try:
        _write_final_meshes(assembly, output, manifest, meshes, extent, display_cache)
        _verify_written_exports(output, manifest, solids)
    except (ValueError, RuntimeError, OSError, KeyError) as error:
        from .mesh_validity import MeshEvaluationError
        if isinstance(error, MeshEvaluationError):
            diagnostic = dict(error.diagnostic)
            manifest['failures'].append(dict(code=diagnostic['code'],
                part_id=diagnostic.get('part_id'), stage=diagnostic.get('stage'),
                failure_kind='candidate_evaluation' if diagnostic.get('failure_kind') in
                    ('input_geometry','geometry_evaluation','target_precision') else 'export',
                diagnostic=diagnostic, reason=str(error)[:300]))
        else:
            manifest['failures'].append(dict(code='EXPORTED_FILE_INVALID',
                stage='write_final_meshes', failure_kind='export', reason=str(error)[:300]))
    manifest.update(status='PASS' if not manifest['failures'] else 'FAIL', stage='complete',
                    elapsed_seconds=time.monotonic()-start, assembled_size_mm=extent.tolist())
    if manifest['manufacturing_status'] == 'RUNNING':
        manifest['manufacturing_status'] = 'FAIL'
    manifest['export_status'] = 'PASS' if (manifest['status']=='PASS'
        and manifest['manufacturing_status']=='PASS' and manifest['display_status']=='PASS') else 'FAIL'
    manifest['files_sha256'] = {p.name:hashlib.sha256(p.read_bytes()).hexdigest()
        for p in output.iterdir() if p.suffix in ('.glb','.stl')}
    _write(target, manifest)
    return manifest


def _write_final_meshes(assembly, output, manifest, meshes, extent, display_cache=None):
    """Shared serialization of this candidate's meshes, not another CSG pass."""
    exploded = {name:np.array(matrix, copy=True) for name,matrix in assembly.transforms.items()}
    manifest['display_failures'] = []
    manifest['display_status'] = 'PASS'
    manifest['canonical_scene_consistency'] = dict(status='FAIL' if any(
        failure['code']=='EXPORTED_INTERFACE_GEOMETRY_MISMATCH'
        for failure in manifest['failures']) else 'PASS',
        method='canonical_mesh64_parts_and_assembly_transforms',
        source='manufacturing_geometry', independent_print_parts=True,
        part_ids=list(meshes), assembled_size_mm=np.asarray(extent).tolist(),
        display_mesh_used=False, self_intersection='NOT_EVALUATED')
    for i, part in enumerate(manifest['parts']):
        name = part['id']
        exploded[name][0,3] += i*float(np.max(extent))*1.25/assembly.mm_per_unit
        part['exploded_transform'] = exploded[name].tolist()
        display = _display_export({name:meshes[name]}, {name:np.eye(4)},
                                  output/f'{name}.glb', assembly.mm_per_unit, display_cache)
        part.update(glb=display['file'], display_status=display['status'],
                    display_complete=display['status']=='PASS', display_export=display,
                    target_precision=display.get('target_precision', display.get('diagnostic')),
                    display_failures=[] if display['status']=='PASS' else [display['diagnostic']])
        if display['status'] != 'PASS':
            _display_failure(manifest, dict(display['diagnostic'], part_id=name))
    for key, transforms in [('scene_glb', assembly.transforms), ('exploded_glb', exploded)]:
        filename = 'scene.glb' if key == 'scene_glb' else 'exploded.glb'
        display = _display_export(meshes, transforms, output/filename, assembly.mm_per_unit, display_cache)
        manifest[key] = display['file']
        manifest.setdefault('display_exports', {})[key] = display
        if display['status'] != 'PASS':
            _display_failure(manifest, display['diagnostic'])
