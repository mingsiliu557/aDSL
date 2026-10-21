"""Centroid-ray support proxy in millimetres, not slicer material consumption.

No geometry is edited. A backend failure is never interpreted as a ray miss.
"""
from __future__ import annotations

import math
from numbers import Real

ALGORITHM_VERSION = 'centroid_downward_v1'
SUPPORT_POSE_POLICY = 'min_support_axis_aligned_24_v1'
SELF_HIT_RELATIVE_EPSILON = 64.0 * 2.220446049250313e-16


class SupportMeasurementError(ValueError):
    """Known unavailable mesh/ray measurement; callers may report INDETERMINATE."""


def support_options(config):
    """Validate without inserting defaults into a frozen configuration."""
    metric = config.get('support_metric', 'area')
    shape = config.get('shape_editable', False)
    if metric not in ('area', 'nsv'):
        raise ValueError('support_metric must be area or nsv')
    if type(shape) is not bool:
        raise ValueError('shape_editable must be bool')
    return metric, shape


def _nonnegative(value):
    return isinstance(value, Real) and not isinstance(value, bool) and math.isfinite(value) and value >= 0


def _ray_backend(mesh):
    try:
        from trimesh.ray.ray_pyembree import RayMeshIntersector
    except ImportError:
        try:
            import rtree  # noqa: F401 -- triangle intersector needs its spatial index
            from trimesh.ray.ray_triangle import RayMeshIntersector
        except ImportError as error:
            raise SupportMeasurementError('ray_backend_unavailable') from error
        return RayMeshIntersector(mesh), 'trimesh_triangle'
    return RayMeshIntersector(mesh), 'trimesh_embree'


def support_volume(mesh_mm, *, overhang_threshold_from_horizontal_deg, bed_tolerance_mm):
    import numpy as np
    from experiments.support_requirement_critical_surfaces.analyze import overhang_mask

    angle = overhang_threshold_from_horizontal_deg
    if not _nonnegative(angle) or not 0 < angle < 90 or not _nonnegative(bed_tolerance_mm):
        raise ValueError('invalid support measurement angle or bed tolerance')
    mesh = mesh_mm.copy()
    if (not len(mesh.faces) or not np.isfinite(mesh.vertices).all() or
            not mesh.is_watertight or not mesh.is_winding_consistent):
        raise SupportMeasurementError('mesh_not_finite_closed_oriented')
    volume = float(mesh.volume)
    if not math.isfinite(volume) or volume <= 0:
        raise SupportMeasurementError('mesh_volume_not_positive_finite')
    mesh.apply_translation((0, 0, -float(mesh.bounds[0, 2])))
    mask = overhang_mask(mesh, angle, bed_tolerance_mm)
    ids = np.flatnonzero(mask)
    projected = np.asarray(mesh.area_faces[ids] * np.abs(mesh.face_normals[ids, 2]))
    try:
        intersector, backend = _ray_backend(mesh)
    except (ImportError, OSError, RuntimeError) as error:
        raise SupportMeasurementError('ray_backend_unavailable: ' + type(error).__name__) from error
    centers = np.asarray(mesh.triangles_center[ids], dtype=np.float64)
    heights = centers[:, 2].copy()
    # A local numerical self-hit convention, not a manufacturing tolerance.
    epsilon = SELF_HIT_RELATIVE_EPSILON * max(float(np.max(mesh.extents)), np.finfo(float).tiny)
    if len(ids):
        directions = np.tile((0., 0., -1.), (len(ids), 1))
        try:
            locations, rays, faces = intersector.intersects_location(
                ray_origins=centers, ray_directions=directions, multiple_hits=True)
        except (ImportError, OSError, RuntimeError) as error:
            raise SupportMeasurementError('ray_backend_failed: ' + type(error).__name__) from error
        locations = np.asarray(locations, dtype=float).reshape((-1, 3))
        rays, faces = np.asarray(rays, dtype=int), np.asarray(faces, dtype=int)
        if not np.isfinite(locations).all():
            raise SupportMeasurementError('ray_backend_nonfinite_intersection')
        distances = centers[rays, 2] - locations[:, 2]
        valid = (faces != ids[rays]) & (distances > epsilon) & (locations[:, 2] >= -epsilon)
        np.minimum.at(heights, rays[valid], distances[valid])
    support = float(np.dot(projected, heights))
    if not _nonnegative(support):
        raise SupportMeasurementError('support_not_finite_nonnegative')
    return dict(status='PASS', support_volume_mm3=support, mesh_volume_mm3=volume,
        nsv=support / volume, risky_projected_area_mm2=float(projected.sum()),
        risky_face_count=int(len(ids)), ray_backend=backend)


def aggregate(rows):
    rows = list(rows)
    unavailable = dict(status='INDETERMINATE', total_support_volume_mm3=None,
        total_volume_mm3=None, nsv_total=None, total_risky_projected_area_mm2=None)
    if not rows:
        return {**unavailable, 'reason':'no_print_parts'}
    for row in rows:
        if (not isinstance(row, dict) or row.get('status') != 'PASS' or
                not all(_nonnegative(row.get(k)) for k in
                    ('support_volume_mm3', 'mesh_volume_mm3', 'risky_projected_area_mm2')) or
                row['mesh_volume_mm3'] <= 0):
            return {**unavailable, 'reason':'incomplete_print_part_measurement'}
    s = sum(r['support_volume_mm3'] for r in rows)
    v = sum(r['mesh_volume_mm3'] for r in rows)
    a = sum(r['risky_projected_area_mm2'] for r in rows)
    if not all(math.isfinite(x) for x in (s, v, a)):
        return {**unavailable, 'reason':'nonfinite_total'}
    return dict(status='PASS', total_support_volume_mm3=s, total_volume_mm3=v,
        nsv_total=s/v, total_risky_projected_area_mm2=a)


def best_support_pose(mesh_mm, *, overhang_threshold_from_horizontal_deg, bed_tolerance_mm):
    """Minimum ray S among the existing 24 rotations, with deterministic ties.

    For a fixed solid, V is rotation invariant, so minimizing S minimizes NSV.
    Every pose must be measurable; a failed query cannot silently disappear
    from the search. This requires no Dapper reference or voxel grid.
    """
    import numpy as np
    from .partition_score import rotations24

    rows = []
    for index, rotation in enumerate(rotations24()):
        transform = np.eye(4)
        transform[:3, :3] = rotation
        transform[:3, 3] = -(mesh_mm.vertices @ np.asarray(rotation).T).min(axis=0)
        printed = mesh_mm.copy()
        printed.apply_transform(transform)
        try:
            measured = support_volume(printed,
                overhang_threshold_from_horizontal_deg=overhang_threshold_from_horizontal_deg,
                bed_tolerance_mm=bed_tolerance_mm)
        except SupportMeasurementError as error:
            raise SupportMeasurementError(f'support_pose_{index}_unavailable: {error}') from error
        rows.append(dict(rotation_id=index, print_transform_mm=transform.tolist(), support=measured))
    best = min(rows, key=lambda row: (row['support']['support_volume_mm3'], row['rotation_id']))
    return dict(selected_rotation_id=best['rotation_id'],
        recommended_print_transform_mm=best['print_transform_mm'], support=best['support'],
        support_pose_policy=SUPPORT_POSE_POLICY, support_orientations=rows)
