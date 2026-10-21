"""Offline measurements of frozen print parts; no source execution or repair."""
from __future__ import annotations

import hashlib
from importlib.metadata import version, PackageNotFoundError
import itertools
import json
import math
from pathlib import Path

import numpy as np
import trimesh

CONNECTIVITY_PROTOCOL = 'print_plan_structural_connectivity_v1'
SUPPORT_PROTOCOL = 'support_volume_min24_v1'


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
        allow_nan=False).encode()).hexdigest()


def file_hash(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def backend_versions():
    out = {}
    for name in ('trimesh', 'python-fcl', 'manifold3d', 'embreex', 'rtree'):
        try:
            out[name] = version(name)
        except PackageNotFoundError:
            out[name] = None
    return out


def _number(value, *, positive=False):
    if (isinstance(value, bool) or not isinstance(value, (int, float)) or
            not math.isfinite(value) or (value <= 0 if positive else value < 0)):
        raise ValueError('invalid finite measurement parameter')
    return float(value)


def _matrix(value):
    from adsl.core.assembly_topology import mm_matrix
    return mm_matrix(value, 1.)


def _scene_mesh(path, excluded):
    scene = trimesh.load(path, force='scene', process=False)
    nodes = sorted(scene.graph.nodes_geometry)
    if not set(excluded).issubset(nodes):
        raise ValueError('unknown explicitly excluded auxiliary node')
    chunks, groups, offset = [], [], 0
    for node in nodes:
        if node in excluded:
            continue
        transform, key = scene.graph[node]
        mesh = scene.geometry[key].copy()
        if not isinstance(mesh, trimesh.Trimesh):
            raise ValueError('non-triangle object geometry')
        transform = np.asarray(transform, dtype=float)
        if transform.shape != (4, 4) or not np.isfinite(transform).all():
            raise ValueError('invalid scene node transform')
        mesh.apply_transform(transform)
        chunks.append(mesh)
        groups.append([offset, offset + len(mesh.faces)])
        offset += len(mesh.faces)
    if not chunks:
        raise ValueError('empty object geometry')
    return trimesh.util.concatenate(chunks), groups


def load_evaluation_parts(input_record: dict) -> dict:
    """Verify files and recover local-mm parts, without invoking any checker.

    asset_sha256 binds the adapter, verified files, units, node exclusions and
    print grouping. It is deliberately distinct from a bare manifest hash.
    """
    parts, identity, audit = [], None, {}
    try:
        data = input_record['geometry_input']
        kind = data['kind']
        if kind == 'fixed_assembly':
            from adsl.agents.assembly_topology import _input, _part_file
            from adsl.core.assembly_topology import read_print_mesh, mm_matrix
            manifest, source = Path(data['manifest_path']), Path(data['source_path'])
            if file_hash(manifest) != data['manifest_sha256']:
                raise ValueError('manifest hash mismatch')
            report = _input(manifest, source)
            scale = _number(report['mm_per_unit'], positive=True)
            declarations = report.get('part_declarations', report['parts'])
            ids = [p['id'] for p in declarations]
            actual = [p['id'] for p in report['parts']]
            if (not ids or any(not isinstance(i, str) or not i for i in ids+actual)
                    or len(set(ids)) != len(ids) or len(set(actual)) != len(actual) or set(ids) != set(actual)):
                raise ValueError('missing or duplicate declared print part')
            bindings = []
            for part in report['parts']:
                path = _part_file(manifest, report, part)
                transform = _matrix(part['print_transform_mm'])
                mesh = read_print_mesh(path, transform)
                parts.append(dict(part_id=part['id'], mesh_mm=mesh,
                    face_groups=part.get('mesh_face_groups'),
                    assembly_transform_mm=mm_matrix(part['assembly_transform'], scale)))
                bindings.append(dict(part_id=part['id'], stl_sha256=file_hash(path)))
            identity = dict(kind=kind, manifest_sha256=file_hash(manifest), source_sha256=file_hash(source), parts=bindings)
            clearances = [c.get('parameters', {}).get('fit_offset_mm') for c in report.get('connections', [])]
            clearances = [float(x) for x in clearances if isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x)]
            audit = dict(unit_source='verified manifest mm_per_unit; STL is already print-mm',
                mm_per_unit=scale, dimension_source='all final STL vertices transformed to assembly mm',
                fit_offset_min_mm=min(clearances) if clearances else None,
                fit_offset_max_mm=max(clearances) if clearances else None,
                source_sha256=report['source_sha256'])
        elif kind == 'single_print_mesh':
            path = Path(data['mesh_path'])
            if file_hash(path) != data['mesh_sha256']:
                raise ValueError('mesh hash mismatch')
            scale = _number(data['length_unit_to_mm'], positive=True)
            transform = _matrix(data['file_to_assembly_mm'])
            excluded = data.get('excluded_auxiliary_nodes', [])
            mesh, groups = _scene_mesh(path, excluded)
            mesh.apply_scale(scale)
            groups = data.get('face_groups', groups)
            parts = [dict(part_id='whole', mesh_mm=mesh, face_groups=groups,
                          assembly_transform_mm=transform)]
            identity = dict(kind=kind, mesh_sha256=file_hash(path), length_unit_to_mm=scale,
                file_to_assembly_mm=transform.tolist(), excluded_auxiliary_nodes=excluded,
                face_groups=groups, print_part_ids=['whole'])
            audit = dict(unit_source='explicit length_unit_to_mm and file_to_assembly_mm',
                length_unit_to_mm=scale, dimension_source='all object scene nodes, including node transforms',
                fit_offset_min_mm=None, fit_offset_max_mm=None)
        else:
            raise ValueError('unknown geometry adapter')
        for part in parts:
            mesh = part['mesh_mm']
            if not len(mesh.faces) or not len(mesh.vertices) or not np.isfinite(mesh.vertices).all():
                raise ValueError('empty or nonfinite print part')
        asset = digest(identity)
        if input_record.get('asset_sha256') and input_record['asset_sha256'] != asset:
            raise ValueError('evaluation asset binding mismatch')
        for part in parts:
            part['_asset_sha256'] = asset
        return dict(status='PASS', reason_codes=[], asset_sha256=asset, parts=parts,
                    identity=identity, audit=audit)
    except (ValueError, KeyError, OSError, ImportError) as error:
        return dict(status='UNAVAILABLE', reason_codes=['GEOMETRY_INPUT_UNAVAILABLE'],
            reason=f'{type(error).__name__}: {error}', asset_sha256=None,
            parts=[], identity=identity, audit=audit)


def _mesh_key(part):
    mesh = part['mesh_mm']
    return digest(dict(vertices=hashlib.sha256(np.asarray(mesh.vertices, dtype='<f8').tobytes()).hexdigest(),
        faces=hashlib.sha256(np.asarray(mesh.faces, dtype='<i8').tobytes()).hexdigest(),
        face_groups=part.get('face_groups')))


def _asset(parts):
    bindings = {p.get('_asset_sha256') for p in parts}
    if len(bindings) == 1 and None not in bindings:
        return next(iter(bindings))
    if any(x is not None for x in bindings):
        raise ValueError('mixed asset identities in print parts')
    return digest([dict(part_id=p['part_id'], mesh=_mesh_key(p),
        assembly_transform_mm=_matrix(p['assembly_transform_mm']).tolist()) for p in parts])


def _material(part):
    # A private in-memory cache shared by the two offline measurements. Never
    # changes vertices, evidence files, or the predeclared printing groups.
    from adsl.core.assembly_topology import union_print_mesh, solid_mesh
    key = _mesh_key(part)
    cache = part.get('_material_cache')
    if cache is None or cache[0] != key:
        solid, components, bound = union_print_mesh(part['mesh_mm'], part.get('face_groups'))
        mesh = solid_mesh(solid)
        cache = (key, solid, mesh, len(components), bound)
        part['_material_cache'] = cache
    return cache[1:]


def assembly_bounds(parts):
    boxes = []
    for part in parts:
        mesh = part['mesh_mm'].copy()
        mesh.apply_transform(_matrix(part['assembly_transform_mm']))
        if not len(mesh.vertices) or not np.isfinite(mesh.vertices).all():
            raise ValueError('unavailable assembly bounds')
        boxes.append(mesh.bounds)
    if not boxes:
        raise ValueError('empty assembly')
    bounds = np.array([np.min([b[0] for b in boxes], axis=0), np.max([b[1] for b in boxes], axis=0)])
    return bounds, float(np.linalg.norm(bounds[1] - bounds[0]))


def _distance(a, b, delta):
    from adsl.core.assembly_topology import checked, solid_mesh
    boxes = [np.asarray(s.bounding_box()).reshape(2, 3) for s in (a, b)]
    lower = float(np.linalg.norm(np.maximum(0, np.maximum(boxes[0][0]-boxes[1][1], boxes[1][0]-boxes[0][1]))))
    if lower > delta:
        return dict(edge=False, distance_mm=None, aabb_lower_bound_mm=lower, method='AABB_EXCLUDED')
    overlap = float(checked(a ^ b).volume())
    if not math.isfinite(overlap) or overlap < 0:
        raise ValueError('invalid material intersection volume')
    if overlap > 0:
        return dict(edge=True, distance_mm=0., intersection_mm3=overlap, method='MANIFOLD_POSITIVE_INTERSECTION')
    manager = trimesh.collision.CollisionManager()
    manager.add_object('first', solid_mesh(a))
    other = solid_mesh(b)
    # FCL reports negative signed distances for collision; confirm collision
    # explicitly rather than blindly clamping a negative/error value to zero.
    if manager.in_collision_single(other):
        return dict(edge=True, distance_mm=0., intersection_mm3=overlap, method='FCL_SURFACE_COLLISION')
    distance, data = manager.min_distance_single(other, return_data=True)
    distance = float(distance)
    if not math.isfinite(distance) or distance < 0:
        raise ValueError('invalid FCL noncollision distance')
    points = [np.asarray(data.point(name)).tolist() for name in ('first', '__external')]
    if not np.isfinite(points).all():
        raise ValueError('nonfinite FCL witness')
    return dict(edge=distance <= delta, distance_mm=distance, witness_points_mm=points,
        intersection_mm3=overlap, method='FCL_TRIANGLE_DISTANCE')


def _groups(ids, edges):
    neighbors = {i:set() for i in ids}
    for a, b in edges:
        neighbors[a].add(b); neighbors[b].add(a)
    groups, remaining = [], set(ids)
    while remaining:
        todo = [min(remaining)]; group = set()
        while todo:
            node = todo.pop()
            if node in group:
                continue
            group.add(node); todo.extend(neighbors[node]-group)
        remaining -= group; groups.append(sorted(group))
    return groups


def measure_connectivity(parts: list[dict], *, delta_ratio: float, delta_floor_mm: float) -> dict:
    ratio, floor = _number(delta_ratio), _number(delta_floor_mm)
    versions = backend_versions()
    config = dict(protocol_id=CONNECTIVITY_PROTOCOL, delta_ratio=ratio, delta_floor_mm=floor,
        backend={k:versions[k] for k in ('trimesh','python-fcl','manifold3d')},
        intersection='checked full material union; positive volume => zero distance',
        distance='FCL triangle distance <= delta; no added threshold tolerance',
        internal='existing union_print_mesh positive-volume material components')
    out = dict(protocol_id=CONNECTIVITY_PROTOCOL, status='UNAVAILABLE', overall_pass=None,
        internal_connected=None, interpart_connected=None, part_count=len(parts), part_components=[],
        interpart_graph_component_count=None, assembly_diagonal_mm=None, delta_ratio=ratio,
        delta_floor_mm=floor, delta_mm=None, reason_codes=[],
        measurement_config_sha256=digest(config), measurement_config=config,
        asset_sha256=None, evidence_ref=None,
        details=dict(pairs=[], graph_groups=None, units='mm', backend=versions, distance_tolerance_mm=0.))
    ids = [p.get('part_id') for p in parts]
    if not ids or any(not isinstance(i,str) or not i for i in ids) or len(ids) != len(set(ids)):
        out['reason_codes'] = ['EMPTY_OR_DUPLICATE_PRINT_PARTS']; return out
    try:
        out['asset_sha256'] = _asset(parts)
        bounds, diagonal = assembly_bounds(parts)
    except (ValueError, KeyError) as error:
        out['reason_codes'] = ['INVALID_GEOMETRY_INPUT']
        out['details']['input_error'] = str(error)
        return out
    delta = max(ratio*diagonal, floor)
    out.update(assembly_diagonal_mm=diagonal, delta_mm=delta)
    out['details']['assembly_bounds_mm'] = bounds.tolist()
    worlds, failures = {}, []
    for part in parts:
        name = part['part_id']
        try:
            solid, mesh, count, bound = _material(part)
            from adsl.core.assembly_topology import checked
            worlds[name] = checked(solid.transform(_matrix(part['assembly_transform_mm'])[:3]))
            out['part_components'].append(dict(part_id=name, component_count=count,
                internal_connected=count == 1, kernel_length_bound_mm=bound))
        except (ValueError, RuntimeError, ImportError) as error:
            failures.append('PART_MATERIAL_UNAVAILABLE')
            out['part_components'].append(dict(part_id=name, component_count=None,
                internal_connected=None, reason=str(error)))
    disconnected = any(p['internal_connected'] is False for p in out['part_components'])
    out['internal_connected'] = False if disconnected else (True if not failures else None)
    if disconnected:
        out['reason_codes'].append('INTERNAL_PART_DISCONNECTED')
    edges = []
    for a, b in itertools.combinations(ids, 2):
        if a not in worlds or b not in worlds:
            continue
        try:
            row = _distance(worlds[a], worlds[b], delta)
            out['details']['pairs'].append(dict(part_ids=[a,b], status='PASS', **row))
            if row['edge']:
                edges.append((a,b))
        except (ValueError, RuntimeError, OSError, ImportError) as error:
            failures.append('DISTANCE_UNAVAILABLE')
            out['details']['pairs'].append(dict(part_ids=[a,b], status='UNAVAILABLE', reason=str(error)))
    if not failures:
        groups = _groups(ids, edges)
        connected = len(groups) == 1
        out.update(status='PASS', interpart_connected=connected,
            interpart_graph_component_count=len(groups), overall_pass=not disconnected and connected)
        out['details']['graph_groups'] = groups
        if not connected:
            out['reason_codes'].append('INTERPART_GRAPH_DISCONNECTED')
    out['reason_codes'] = list(dict.fromkeys(out['reason_codes'] + failures))
    return out


def measure_support24(parts: list[dict], *, overhang_threshold_from_horizontal_deg: float,
                      bed_tolerance_mm: float) -> dict:
    from adsl.agents.partition_score import rotations24
    from adsl.agents.support_volume import support_volume, aggregate, SupportMeasurementError, ALGORITHM_VERSION, SELF_HIT_RELATIVE_EPSILON
    angle, bed = _number(overhang_threshold_from_horizontal_deg, positive=True), _number(bed_tolerance_mm)
    if angle >= 90:
        raise ValueError('support angle must be below 90 degrees')
    rotations = list(rotations24())
    config = dict(protocol_id=SUPPORT_PROTOCOL, algorithm=ALGORITHM_VERSION,
        orientation_set='axis_aligned_24', rotations=[np.asarray(r).tolist() for r in rotations],
        overhang_threshold_from_horizontal_deg=angle, bed_tolerance_mm=bed,
        self_hit_relative_epsilon=SELF_HIT_RELATIVE_EPSILON, mask_bed_epsilon_mm=1e-8,
        backend={k:v for k,v in backend_versions().items() if k != 'python-fcl'})
    details, selected, reasons, backends = [], [], [], set()
    ids = [p.get('part_id') for p in parts]
    asset = None
    if not ids or any(not isinstance(i,str) or not i for i in ids) or len(ids) != len(set(ids)):
        reasons.append('EMPTY_OR_DUPLICATE_PRINT_PARTS')
    if not reasons:
        try:
            asset = _asset(parts)
            assembly_bounds(parts)  # Reject invalid coordinate bindings even in a local pose search.
        except (ValueError, KeyError):
            reasons.append('INVALID_GEOMETRY_INPUT')
    if not reasons:
        for part in parts:
            item = dict(part_id=part['part_id'], orientations=[], selected_rotation_id=None)
            details.append(item)
            try:
                _, mesh, _, _ = _material(part)
            except (ValueError, RuntimeError, ImportError) as error:
                item.update(status='UNAVAILABLE', reason=str(error)); reasons.append('PART_MATERIAL_UNAVAILABLE'); continue
            for i, rotation in enumerate(rotations):
                transform = np.eye(4); transform[:3,:3] = rotation
                transform[:3,3] = -(mesh.vertices @ rotation.T).min(axis=0)
                printed = mesh.copy(); printed.apply_transform(transform)
                row = dict(rotation_id=i, print_transform_mm=transform.tolist())
                try:
                    support = support_volume(printed, overhang_threshold_from_horizontal_deg=angle, bed_tolerance_mm=bed)
                    row.update(status='PASS', support=support); backends.add(support['ray_backend'])
                except SupportMeasurementError as error:
                    row.update(status='UNAVAILABLE', support=None, reason=str(error)); reasons.append('SUPPORT_POSE_UNAVAILABLE')
                item['orientations'].append(row)
            complete = len(item['orientations']) == 24 and all(r['status']=='PASS' for r in item['orientations'])
            item['status'] = 'PASS' if complete else 'UNAVAILABLE'
            if complete:
                best = min(item['orientations'], key=lambda r:(r['support']['support_volume_mm3'],r['rotation_id']))
                item.update(selected_rotation_id=best['rotation_id'], support=best['support'])
                selected.append(best['support'])
    total = aggregate(selected if not reasons and len(selected)==len(parts) else [])
    config['ray_backends'] = sorted(backends)
    return dict(protocol_id=SUPPORT_PROTOCOL, status='PASS' if total['status']=='PASS' else 'UNAVAILABLE',
        orientation_objective='support_volume_mm3', orientation_set='axis_aligned_24',
        **{k:total[k] for k in ('total_support_volume_mm3','total_volume_mm3','nsv_total')},
        part_count=len(parts), measurement_config_sha256=digest(config), measurement_config=config,
        asset_sha256=asset, reason_codes=list(dict.fromkeys(reasons)),
        evidence_ref=None, details=dict(items=details))
