"""Cached coarse far field and analytic, independently checked connector zones.

All geometry is in mm. The omitted entrance layer [-c, c] is intentional.
Vendored CoACD is used unchanged, on immutable pre-connector PGE parts only.
"""
from functools import lru_cache
from pathlib import Path
import hashlib
import importlib.metadata
import multiprocessing as mp
import time

import manifold3d as mf
import numpy as np
import trimesh

from .common import digest, sha, union, write_json
from .connector import cylinder
from .vendor import checked, collision_proxies, mesh_solid, solid_mesh


def _proxy_child(connection, mesh, options):
    try:
        connection.send(('PASS', collision_proxies(mesh, options)))
    except Exception as error:
        connection.send(('ERROR', (type(error).__name__, str(error))))
    finally:
        connection.close()


def bounded_proxies(mesh, options, seconds=300):
    """Native work stays in a killable child; a timeout never changes precision."""
    context = mp.get_context('spawn')
    parent, child = context.Pipe(duplex=False)
    process = context.Process(target=_proxy_child, args=(child, mesh, options))
    process.start()
    child.close()
    try:
        if not parent.poll(seconds):
            raise RuntimeError(f'COACD_TIMEOUT: {seconds}s per part; precision unchanged')
        try:
            status, value = parent.recv()
        except EOFError as error:
            raise RuntimeError('COACD_ERROR: child exited without proxies') from error
        process.join(10)
        if status != 'PASS':
            raise RuntimeError(f'COACD_ERROR: {value}')
        return value
    finally:
        parent.close()
        if process.is_alive():
            process.terminate()
            process.join(10)
            if process.is_alive():
                process.kill()
                process.join()


def mesh_digest(mesh):
    h = hashlib.sha256()
    for array, dtype in [(mesh.vertices, '<f8'), (mesh.faces, '<i8')]:
        value = np.asarray(array, dtype=dtype)
        h.update(np.asarray(value.shape, dtype='<i8').tobytes())
        h.update(value.tobytes())
    return h.hexdigest()


def far_proxies(mesh, *, D, config, cache_dir):
    ratio = float(config.get('far_threshold_ratio', .01))
    if not np.isfinite(ratio) or not .01 <= ratio <= 1.:
        raise ValueError('INVALID_FAR_THRESHOLD_RATIO')
    options = dict(config)
    options.pop('far_threshold_ratio', None)
    options['threshold_mm'] = ratio * D
    identity = dict(version='snap3d_far_proxy_cache_v1', base_mesh_sha256=mesh_digest(mesh),
                    coacd=importlib.metadata.version('coacd'), options=options)
    key = digest(identity)
    directory = Path(cache_dir) / key
    manifest_path = directory / 'manifest.json'

    def read_cache():
        import json
        manifest = json.loads(manifest_path.read_text())
        if manifest['identity'] != identity or not manifest['proxies']:
            raise ValueError('CACHE_IDENTITY_MISMATCH')
        values = []
        for item in manifest['proxies']:
            name = item['path']
            if Path(name).name != name or sha(directory / name) != item['sha256']:
                raise ValueError('CACHE_HASH_MISMATCH')
            value = trimesh.load_mesh(directory / name, process=True)
            if not value.is_volume or not value.is_convex:
                raise ValueError('CACHE_INVALID_CONVEX_PROXY')
            values.append(value)
        return values

    try:
        values = read_cache()
        return values, dict(cache_key=key, cache_hit=True, threshold_mm=options['threshold_mm'],
                            coacd_elapsed_s=0., base_mesh_sha256=identity['base_mesh_sha256'])
    except (OSError, ValueError, KeyError, TypeError):
        pass
    started = time.monotonic()
    values = bounded_proxies(mesh, options)
    elapsed = time.monotonic() - started
    if not values or any(not v.is_volume or not v.is_convex for v in values):
        raise ValueError('COACD_INVALID_CONVEX_PROXY')
    directory.mkdir(parents=True, exist_ok=True)
    entries = []
    for index, value in enumerate(values):
        name = f'proxy_{index:04d}.stl'
        path = directory / name
        temporary = path.with_suffix('.tmp')
        temporary.write_bytes(value.export(file_type='stl'))
        temporary.replace(path)
        entries.append(dict(path=name, sha256=sha(path)))
    write_json(manifest_path, dict(identity=identity, proxies=entries, coacd_elapsed_s=elapsed))
    # First use and cache hits use exactly the same STL-quantized geometry.
    return read_cache(), dict(cache_key=key, cache_hit=False, threshold_mm=options['threshold_mm'],
                              coacd_elapsed_s=elapsed, base_mesh_sha256=identity['base_mesh_sha256'])


@lru_cache(maxsize=1)
def cylinder_angles():
    """Read, rather than assume, the actual Manifold 64-gon vertex convention."""
    mesh = solid_mesh(cylinder(1., 0., 1.))
    vertices = np.asarray(mesh.vertices)
    rim = vertices[np.isclose(vertices[:, 2], 0.) &
                   np.isclose(np.linalg.norm(vertices[:, :2], axis=1), 1.)]
    angles = np.sort(np.mod(np.arctan2(rim[:, 1], rim[:, 0]), 2 * np.pi))
    if len(angles) != 64:
        raise ValueError('MANIFOLD_CYLINDER_ANGLE_CONVENTION_CHANGED')
    return tuple(map(float, angles))


def sector_width(inner_radius, clearance):
    return max(k for k in [1, 2, 4, 8, 16, 32, 64]
               if inner_radius * (1 - np.cos(k * np.pi / 64)) <= clearance / 4)


def _frame(record):
    matrix = np.asarray(record['T'], dtype=float)
    if (matrix.shape != (4, 4) or not np.isfinite(matrix).all() or
            not np.allclose(matrix[3], [0, 0, 0, 1], atol=1e-12, rtol=0) or
            not np.allclose(matrix[:3, :3].T @ matrix[:3, :3], np.eye(3), atol=1e-9, rtol=0) or
            not np.isclose(np.linalg.det(matrix[:3, :3]), 1., atol=1e-9, rtol=0)):
        raise ValueError('INVALID_CONNECTOR_FRAME')
    return matrix


def _world(solid, record):
    return checked(solid.transform(_frame(record)[:3]))


def _annulus(inner, outer, low, high, k):
    angles = np.asarray(cylinder_angles())
    for start in range(0, 64, k):
        ids = np.arange(start, start + k + 1) % 64
        points = [[radius * np.cos(angles[j]), radius * np.sin(angles[j]), z]
                  for z in [low, high] for radius in [inner, outer] for j in ids]
        yield checked(mf.Manifold.hull_points(np.asarray(points)))


def analytic_blocks(record, *, k_override=None):
    r, l, c, h, t = (float(record[n]) for n in
                      ['radius', 'length', 'clearance', 'overhang', 'head_thickness'])
    outer = r + h + 3 * c
    headed = record.get('headed', True)
    k1 = sector_width(r + c, c) if k_override is None else k_override
    inner2 = r + h + 2 * c if headed else r + c
    k2 = sector_width(inner2, c) if k_override is None else k_override
    blocks = [('shaft', cylinder(r, -r, l))]
    if headed:
        blocks.append(('head', cylinder(r + c + h, l - t, l)))
    for role, inner, low, high, k in [('sleeve_L1', r + c, c, l-t-c, k1),
                                    ('sleeve_L2', inner2, l-t-c, l+c, k2)]:
        blocks.extend((role, solid) for solid in _annulus(inner, outer, low, high, k))
    blocks.append(('sleeve_L3', cylinder(outer, l+c, l+2*c)))
    return [(role, solid_mesh(_world(solid, record))) for role, solid in blocks], dict(
        edge=record['edge'], k1=k1, k2=k2,
        sagitta1_mm=(r+c)*(1-np.cos(k1*np.pi/64)),
        sagitta2_mm=inner2*(1-np.cos(k2*np.pi/64)),
        intrusion_bound_mm=c/4, entrance_proxy_omitted_local_z=[-c, c])


def zone(record):
    r, l, c, h = (float(record[n]) for n in ['radius', 'length', 'clearance', 'overhang'])
    radius = r + h + 3*c
    solid = _world(cylinder(radius, -c, l+2*c), record)
    matrix = _frame(record)
    angles = np.asarray(cylinder_angles())
    vertices = np.column_stack([radius*np.cos(angles), radius*np.sin(angles), np.zeros(64)])
    planes = []
    for a, b in zip(vertices, np.roll(vertices, -1, axis=0)):
        edge = b-a
        normal = np.array([edge[1], -edge[0], 0.])
        normal /= np.linalg.norm(normal)
        planes.append((normal, float(normal @ a)))
    planes.extend([(np.array([0., 0., -1.]), c), (np.array([0., 0., 1.]), l+2*c)])
    world_planes = [(matrix[:3, :3] @ n, float(d+(matrix[:3, :3] @ n) @ matrix[:3, 3]))
                    for n, d in planes]
    return solid, world_planes


def clip_convex(mesh, record, *, D):
    exclusion, planes = zone(record)
    bounds = solid_mesh(exclusion).bounds
    if np.any(mesh.bounds[1] < bounds[0]) or np.any(bounds[1] < mesh.bounds[0]):
        return [mesh], dict(discarded_count=0, discarded_volume_mm3=0.)
    remaining = mesh_solid(mesh)
    pieces = []
    discarded_count = 0
    discarded_volume = 0.
    for normal, offset in planes:
        if remaining.is_empty():
            break
        outside = checked(remaining.trim_by_plane(normal, offset))
        remaining = checked(remaining.trim_by_plane(-normal, -offset))
        if outside.is_empty():
            continue
        volume = float(outside.volume())
        if volume < 1e-9 * D**3:
            discarded_count += 1
            discarded_volume += volume
        else:
            pieces.append(solid_mesh(outside))
    return pieces, dict(discarded_count=discarded_count, discarded_volume_mm3=discarded_volume)


def build_zone_proxies(base, connectors, *, D, config, cache_dir):
    proxies = {part: [] for part in base}
    blocks = {part: [] for part in base}
    report = dict(far={}, zones=[], blocks=blocks, discarded_count=0, discarded_volume_mm3=0.)
    if len({v['edge'] for v in connectors}) != len(connectors):
        raise ValueError('DUPLICATE_CONNECTOR_RECORD')
    for part, mesh in base.items():
        far, report['far'][part] = far_proxies(mesh, D=D, config=config, cache_dir=cache_dir)
        for record in connectors:
            clipped = []
            for piece in far:
                values, stats = clip_convex(piece, record, D=D)
                clipped.extend(values)
                report['discarded_count'] += stats['discarded_count']
                report['discarded_volume_mm3'] += stats['discarded_volume_mm3']
            far = clipped
        proxies[part].extend(far)
        blocks[part].extend(dict(role='far', edge=None) for _ in far)
    for record in connectors:
        if record['donor'] not in base or record['receiver'] not in base:
            raise ValueError('CONNECTOR_PART_MISSING')
        values, stats = analytic_blocks(record)
        report['zones'].append(stats)
        for role, mesh in values:
            part = record['donor'] if role in {'shaft', 'head'} else record['receiver']
            proxies[part].append(mesh)
            blocks[part].append(dict(role=role, edge=record['edge']))
    if any(not values for values in proxies.values()):
        raise ValueError('PROXY_MISSING')
    report['proxy_counts'] = {part: len(v) for part, v in proxies.items()}
    return proxies, report


def _inset_cavity(record):
    r, l, c, h, t = (float(record[n]) for n in
                      ['radius', 'length', 'clearance', 'overhang', 'head_thickness'])
    inset = c/4
    throat = cylinder(r+c-inset, -c+inset, l+c-inset)
    if record.get('headed', True):
        pocket = cylinder(r+h+2*c-inset, l-t-c+inset, l+c-inset)
        throat = checked(throat+pocket)
    return _world(throat, record)


def verify_zone_proxies(parts, proxies, connectors, *, D, roles, rollout_config,
                        clearance_mm, fixed_ids=None):
    from .rollout import rollout
    tolerance = 1e-9 * D**3
    checks = []
    valid = set(parts) == set(proxies) and all(proxies.values())
    if not valid:
        return dict(status='FAIL', reason='PROXY_MISSING', connectors=[], initial_contact=None)
    solids = {part: [mesh_solid(m) for m in values] for part, values in proxies.items()}
    for record in connectors:
        if any(len(roles.get(p, [])) != len(v) for p, v in proxies.items()):
            raise ValueError('PROXY_ROLE_MISMATCH')
        cavity = _inset_cavity(record)
        intruders = []
        for part, values in solids.items():
            for index, solid in enumerate(values):
                role = roles[part][index]
                if (part == record['donor'] and role['edge'] == record['edge'] and
                        role['role'] in {'shaft', 'head'}):
                    continue
                hit = checked(solid ^ cavity)
                if not hit.is_empty():
                    intruders.append(hit)
        cavity_intersection = float(union(intruders).volume()) if intruders else 0.
        r, l, c, h, t = (float(record[n]) for n in
                         ['radius', 'length', 'clearance', 'overhang', 'head_thickness'])
        probe = checked(cylinder(r+c+h, l-t-2*c, l-t-c) - cylinder(r+c, l-t-2*c, l-t-c))
        missing = checked(_world(probe, record) - union(solids[record['receiver']]))
        shoulder_missing = float(missing.volume()) if not missing.is_empty() else 0.
        check = dict(edge=record['edge'], cavity_intersection_mm3=cavity_intersection,
                     cavity_pass=cavity_intersection <= tolerance,
                     shoulder_missing_mm3=shoulder_missing,
                     shoulder_pass=shoulder_missing <= tolerance, volume_tolerance_mm3=tolerance)
        valid &= check['cavity_pass'] and check['shoulder_pass']
        checks.append(check)
    initial = None
    if valid:
        initial = rollout(parts, D=D, config=rollout_config, coacd_config={},
                          clearance_mm=clearance_mm, fixed_ids=fixed_ids,
                          proxy_cache=proxies, initial_only=True)
        valid = initial['status'] == 'PASS'
    return dict(status='PASS' if valid else 'FAIL', reason=None if valid else 'PROXY_UNVERIFIED',
                connectors=checks, initial_contact=None if initial is None else initial.get('initial_contact'),
                initial_model_status=None if initial is None else initial['status'],
                initial_model_reason=None if initial is None else initial.get('reason'))
