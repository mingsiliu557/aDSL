"""Area-weighted surface sampling for the offline facing-contact graph.

Distance queries use the actual target triangles. Samples estimate surface area;
triangle counts, AABB overlap and contact-point counts are not area measurements.
"""
from __future__ import annotations

import math

import numpy as np
import trimesh


CONTACT_AREA_CONFIG = dict(
    min_facing_area_ratio=1.5e-5,
    facing_normal_dot_max=0.0,
    facing_area_reduction='min',
    area_algorithm='area_weighted_surface_sampling_v1',
    sampling_domain='source_surface_clipped_to_target_aabb_plus_delta_outward_one_ulp',
    samples_per_direction=65536,
    seed=0,
    query_batch_size=1024,
    sampling_interval='wilson_95_percent_marginal',
    nearest_surface='trimesh.proximity.closest_point; returned face normal',
)


def _clip_triangle(triangle, lower, upper):
    """Clip one flat source face to a box, preserving its cyclic vertex order."""
    polygon = [point.copy() for point in triangle]
    for axis in range(3):
        for bound, keep_greater in ((lower[axis],True),(upper[axis],False)):
            if not polygon:
                return []
            clipped = []
            previous = polygon[-1]
            previous_inside = previous[axis] >= bound if keep_greater else previous[axis] <= bound
            for current in polygon:
                current_inside = current[axis] >= bound if keep_greater else current[axis] <= bound
                if current_inside != previous_inside:
                    fraction = (bound-previous[axis])/(current[axis]-previous[axis])
                    intersection = previous + fraction*(current-previous)
                    intersection[axis] = bound
                    clipped.append(intersection)
                if current_inside:
                    clipped.append(current)
                previous, previous_inside = current, current_inside
            polygon = clipped
    return [np.array([polygon[0],polygon[i],polygon[i+1]]) for i in range(1,len(polygon)-1)]


def _sampling_surface(source, target, delta):
    # Any source point within delta of the target lies in this expanded box.
    # Clip only the integration domain; no saved/source geometry is changed.
    # The box never establishes contact: each sample still needs a true nearest
    # surface query and an opposing target face normal.
    triangles, normals = source.triangles, source.face_normals
    lower = np.nextafter(target.bounds[0]-delta,-np.inf)
    upper = np.nextafter(target.bounds[1]+delta,np.inf)
    active = np.flatnonzero(np.all(triangles.max(axis=1)>=lower,axis=1)
                           & np.all(triangles.min(axis=1)<=upper,axis=1))
    patches, patch_normals = [], []
    for i in active:
        for patch in _clip_triangle(triangles[i],lower,upper):
            area = np.linalg.norm(np.cross(patch[1]-patch[0],patch[2]-patch[0]))/2
            if not math.isfinite(area):
                raise ValueError('nonfinite sampling surface area')
            if area > 0:
                patches.append(patch); patch_normals.append(normals[i])
    if not patches:
        return None, np.empty((0,3))
    mesh = trimesh.Trimesh(vertices=np.asarray(patches).reshape(-1,3),
        faces=np.arange(len(patches)*3).reshape(-1,3),process=False)
    return mesh, np.asarray(patch_normals)


def _wilson(hits, count):
    # A marginal statistical interval, not a guaranteed geometric bound. In
    # particular, zero hits retains a positive upper endpoint for nonempty area.
    z = 1.959963984540054
    p = hits/count
    denominator = 1+z*z/count
    center = (p+z*z/(2*count))/denominator
    radius = z*math.sqrt(p*(1-p)/count+z*z/(4*count*count))/denominator
    # The analytic endpoint is exactly 0/1 when all draws miss/hit. Preserve
    # it rather than letting roundoff exclude the observed endpoint itself.
    return (0. if hits==0 else max(0.,center-radius),
            1. if hits==count else min(1.,center+radius))


def _directional(source, target, delta, threshold):
    surface, normals = _sampling_surface(source,target,delta)
    count = CONTACT_AREA_CONFIG['samples_per_direction']
    seed = CONTACT_AREA_CONFIG['seed']
    batch_size = CONTACT_AREA_CONFIG['query_batch_size']
    if (isinstance(count,bool) or not isinstance(count,int) or count<=0
            or isinstance(batch_size,bool) or not isinstance(batch_size,int) or batch_size<=0):
        raise ValueError('invalid surface sampling configuration')
    region_area = 0. if surface is None else float(surface.area)
    if not math.isfinite(region_area) or region_area < 0:
        raise ValueError('invalid sampling region area')
    if region_area == 0:
        return dict(area_estimate_mm2=0.,area_interval_mm2=[0.,0.],
            sampling_area_mm2=0.,sample_count=0,hit_count=0,seed=seed,
            is_exact_zero=True,threshold_in_sampling_interval=False,
            stop_reason='empty_possible_contact_region')
    # Default sample_surface weights each triangle by area, not by face count.
    # Every draw is uniform over the clipped source surface. Retriangulation
    # can change the realized random points, but not the target distribution.
    points, face_ids = trimesh.sample.sample_surface(surface,count,seed=seed)
    source_normals = normals[face_ids]
    target_normals = target.face_normals
    if not np.isfinite(points).all() or not np.isfinite(source_normals).all():
        raise ValueError('nonfinite surface sample or normal')
    hits = 0
    for start in range(0,count,batch_size):
        stop = min(start+batch_size,count)
        _, distances, target_faces = trimesh.proximity.closest_point(target,points[start:stop])
        if (not np.isfinite(distances).all() or np.any(distances<0)
                or np.any(target_faces<0) or np.any(target_faces>=len(target_normals))):
            raise ValueError('invalid facing-area nearest-triangle query')
        nearest_normals = target_normals[target_faces]
        if not np.isfinite(nearest_normals).all():
            raise ValueError('nonfinite nearest-surface normal')
        dots = np.einsum('ij,ij->i',source_normals[start:stop],nearest_normals)
        hits += int(np.count_nonzero((distances<=delta)
                    & (dots<CONTACT_AREA_CONFIG['facing_normal_dot_max'])))
    estimate = region_area*(hits/count)
    interval = [region_area*x for x in _wilson(hits,count)]
    return dict(area_estimate_mm2=estimate,area_interval_mm2=interval,
        sampling_area_mm2=region_area,sample_count=count,hit_count=hits,seed=seed,
        is_exact_zero=False,threshold_in_sampling_interval=interval[0]<=threshold<=interval[1],
        stop_reason='fixed_surface_samples_completed')


def facing_area(a, b, *, delta_mm, minimum_area_mm2):
    """Estimate both directional areas and apply the frozen minimum rule.

    PASS means the sampling measurement completed, not an exact-area proof.
    The marginal sampling intervals do not form a joint 95% guarantee. The
    two directional estimators are unbiased; taking their minimum need not be.
    """
    if (not np.isfinite([delta_mm,minimum_area_mm2]).all()
            or delta_mm<0 or minimum_area_mm2<=0):
        raise ValueError('invalid facing-area scale')
    directions = [_directional(a,b,delta_mm,minimum_area_mm2),
                  _directional(b,a,delta_mm,minimum_area_mm2)]
    estimate = min(x['area_estimate_mm2'] for x in directions)
    interval = [min(x['area_interval_mm2'][side] for x in directions) for side in (0,1)]
    return dict(status='PASS',edge=estimate>=minimum_area_mm2,
        minimum_facing_area_mm2=minimum_area_mm2,facing_area_estimate_mm2=estimate,
        facing_area_interval_mm2=interval,
        threshold_in_sampling_interval=interval[0]<=minimum_area_mm2<=interval[1],
        directional_areas=directions,method=CONTACT_AREA_CONFIG['area_algorithm'],
        decision_basis='estimated_area; statistical intervals are not certified geometric bounds',
        reason=None)
