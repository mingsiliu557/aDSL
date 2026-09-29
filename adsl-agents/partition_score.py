"""Dapper (2015) FDM objective with independent beds, not a packing algorithm.

All occupancy uses positive material/cell intersection. Face-only contact does
not thicken grid-aligned surfaces. No face-size or support-volume threshold.
"""
from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

import numpy as np

OBJECTIVE = dict(method='dapper_fdm_2015', alpha=.3, r_vox=.1,
                 orientation_set='axis_aligned_24', layout='independent_bed')
SCHEMA = 1


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def objective_config(config):
    if config != OBJECTIVE:
        raise ValueError('partition_objective requires dapper_fdm_2015, alpha=0.3, r_vox=0.1, axis_aligned_24, independent_bed')
    return dict(OBJECTIVE)


def evaluation_hash(config):
    return digest(dict(config=objective_config(config), schema=SCHEMA,
        occupancy='positive_material_cell_intersection', grid='aabb_min_half_open', tie='rotation_id'))


def rotations24():
    values = []
    for axes in itertools.permutations(range(3)):
        for signs in itertools.product((-1, 1), repeat=3):
            r = np.eye(3, dtype=int)[list(axes)] * np.array(signs)[:, None]
            if round(np.linalg.det(r)) == 1:
                values.append(r)
    values.sort(key=lambda r: (not np.array_equal(r, np.eye(3)), tuple(r.ravel())))
    return values


def occupied_voxels(mesh_mm, pitch_mm, grid_origin_mm):
    """Return sorted integer XYZ cells; include interiors and preserve cavities."""
    import manifold3d as mf
    from adsl.core.assembly_topology import checked, mesh_solid
    h = float(pitch_mm)
    origin = np.asarray(grid_origin_mm, dtype=float)
    if not np.isfinite(h) or h <= 0 or origin.shape != (3,) or not np.isfinite(origin).all():
        raise ValueError('invalid voxel grid')
    solid = mesh_solid(mesh_mm)
    limits = (mesh_mm.bounds-origin)/h
    # Only remove arithmetic noise in integer index bounds, never in geometry.
    rounded = np.rint(limits)
    limits = np.where(np.abs(limits-rounded) <= 64*np.finfo(float).eps*np.maximum(1, np.abs(limits)), rounded, limits)
    lower, upper = np.floor(limits[0]).astype(int), np.ceil(limits[1]).astype(int)
    cell = mf.Manifold.cube((h, h, h))
    rows = []
    for index in itertools.product(*(range(a, b) for a, b in zip(lower, upper))):
        hit = checked(solid ^ cell.translate(origin+h*np.asarray(index)))
        volume = float(hit.volume())
        if not np.isfinite(volume):
            raise ValueError('voxel intersection has invalid volume')
        # Separate zero-volume contact shells before summing: cancellation in
        # a union of perpendicular contact faces can produce a positive residue.
        material = any(piece.volume() > 0 and
            np.all(np.asarray(piece.bounding_box())[3:] > np.asarray(piece.bounding_box())[:3])
            for piece in hit.decompose()) if volume != 0 else False
        if material:
            rows.append(index)
    return np.asarray(rows, dtype=np.int64).reshape(-1, 3)


def vertical_gap_count(occupied):
    cells = np.asarray(occupied, dtype=np.int64).reshape(-1, 3)
    if not len(cells):
        return 0
    if (cells[:, 2] < 0).any() or len(np.unique(cells, axis=0)) != len(cells):
        raise ValueError('bed occupancy requires unique nonnegative Z indices')
    _, inverse = np.unique(cells[:, :2], axis=0, return_inverse=True)
    top = np.full(int(inverse.max())+1, -1, dtype=np.int64)
    np.maximum.at(top, inverse, cells[:, 2])
    return int(np.sum(top+1)-len(cells))


def best_print_pose(mesh_mm, reference, rotations=None):
    h = reference['voxel_pitch_mm']
    results = []
    for index, rotation in enumerate(rotations if rotations is not None else rotations24()):
        transform = np.eye(4)
        transform[:3, :3] = rotation
        transform[:3, 3] = -(mesh_mm.vertices @ np.asarray(rotation).T).min(axis=0)
        printed = mesh_mm.copy(); printed.apply_transform(transform)
        occupied = occupied_voxels(printed, h, np.zeros(3))
        if not len(occupied):
            raise ValueError('nonempty print solid produced no occupied cells')
        results.append(dict(rotation_id=index, gap_voxels=vertical_gap_count(occupied),
            occupied_voxels=len(occupied), print_transform_mm=transform.tolist()))
    best = min(results, key=lambda r: (r['gap_voxels'], r['rotation_id']))
    return dict(gap_voxels=best['gap_voxels'], selected_rotation_id=best['rotation_id'],
        recommended_print_transform_mm=best['print_transform_mm'], orientations=results)


def score_partition(part_results, reference, *, part_count, config=OBJECTIVE):
    config = objective_config(config)
    complete = bool(reference and part_count > 0 and len(part_results) == part_count
        and all(r.get('status') == 'PASS' and r.get('gap_voxels') is not None for r in part_results))
    gap = sum(r['gap_voxels'] for r in part_results) if complete else None
    v = reference.get('reference_voxels') if reference else None
    return dict(**config, reference_sha256=reference.get('reference_sha256') if reference else None,
        evaluation_config_sha256=evaluation_hash(config),
        voxel_pitch_mm=reference.get('voxel_pitch_mm') if reference else None,
        reference_voxels=v, gap_voxels=gap, print_part_count=part_count,
        score=(v-gap)/part_count**config['alpha'] if complete else None, direction='maximize',
        numerator_nonpositive=(v-gap <= 0) if complete else None,
        gap_volume_mm3=gap*reference['voxel_pitch_mm']**3 if complete else None)


def compare_partition_scores(previous, candidate):
    a = previous.get('metrics', {}).get('partition_objective', {})
    b = candidate.get('metrics', {}).get('partition_objective', {})
    if previous.get('status') != 'PASS' or candidate.get('status') != 'PASS' or any(x.get('score') is None for x in (a,b)):
        return dict(conclusion='NOT_EVALUATED')
    if any(not a.get(k) or a[k] != b.get(k) for k in ('reference_sha256','evaluation_config_sha256','voxel_pitch_mm')):
        return dict(conclusion='NOT_COMPARABLE')
    delta = b['score']-a['score']
    tolerance = 64*np.finfo(float).eps*max(1, abs(a['score']), abs(b['score']))
    return dict(conclusion='IMPROVED' if delta > tolerance else 'WORSE' if delta < -tolerance else 'UNCHANGED',
                delta=delta, rounding_tolerance=tolerance)
