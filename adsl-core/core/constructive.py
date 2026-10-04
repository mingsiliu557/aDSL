"""Sampled profile construction. Native geometry is loaded only when evaluated."""
from __future__ import annotations

from dataclasses import dataclass
import math
import operator

import numpy as np

from .asset import Asset
from .primitives import primitive_record


def _manifold():
    try:
        import manifold3d
    except ImportError as error:
        raise ImportError('Constructive geometry requires adsl-core[geometry] (manifold3d==3.5.2).') from error
    return manifold3d


def _turn(a, b, c):
    return (b[0]-a[0])*(c[1]-a[1]) - (b[1]-a[1])*(c[0]-a[0])


def _on_segment(a, b, p):
    return (_turn(a, b, p) == 0 and min(a[0], b[0]) <= p[0] <= max(a[0], b[0])
            and min(a[1], b[1]) <= p[1] <= max(a[1], b[1]))


def _intersects(a, b, c, d):
    ab_c, ab_d, cd_a, cd_b = _turn(a,b,c), _turn(a,b,d), _turn(c,d,a), _turn(c,d,b)
    return (((ab_c > 0 > ab_d or ab_d > 0 > ab_c) and
             (cd_a > 0 > cd_b or cd_b > 0 > cd_a)) or
            _on_segment(a,b,c) or _on_segment(a,b,d) or
            _on_segment(c,d,a) or _on_segment(c,d,b))


def _edges(ring):
    return tuple(zip(ring, ring[1:] + ring[:1]))


def _inside(point, ring):
    # Boundary contact is excluded separately by the inclusive edge tests.
    x,y = point
    inside = False
    for a,b in _edges(ring):
        if (a[1] > y) != (b[1] > y) and x < a[0] + (y-a[1])*(b[0]-a[0])/(b[1]-a[1]):
            inside = not inside
    return inside


def _ring(points, label, *, ccw):
    try:
        ring = tuple(tuple(float(x) for x in p) for p in points)
    except (TypeError, ValueError) as error:
        raise ValueError(f'{label}: expected finite 2D points') from error
    if any(len(p) != 2 or not all(math.isfinite(x) for x in p) for p in ring):
        raise ValueError(f'{label}: expected finite 2D points')
    if len(ring) > 1 and ring[0] == ring[-1]:
        ring = ring[:-1]
    if len(ring) < 3 or len(set(ring)) != len(ring):
        raise ValueError(f'{label}: at least three distinct vertices, with no repeated internal vertex, required')
    edges = _edges(ring)
    for i,(a,b) in enumerate(edges):
        c = ring[(i+2) % len(ring)]
        if _turn(a,b,c) == 0 and (a[0]-b[0])*(c[0]-b[0])+(a[1]-b[1])*(c[1]-b[1]) > 0:
            raise ValueError(f'{label}: adjacent edges overlap at vertex {(i+1) % len(ring)}')
        for j in range(i+1, len(edges)):
            if j == i+1 or (i == 0 and j == len(edges)-1):
                continue
            if _intersects(a,b,*edges[j]):
                raise ValueError(f'{label}: self-intersection or self-contact between edges {i} and {j}')
    area2 = math.fsum(_turn(ring[0], ring[i], ring[i+1]) for i in range(1,len(ring)-1))
    if not math.isfinite(area2) or area2 == 0:
        raise ValueError(f'{label}: nonzero finite area required')
    return ring if (area2 > 0) == ccw else (ring[0], *reversed(ring[1:]))


@dataclass(frozen=True, init=False)
class Polygon:
    """One simple outer 2D ring and disjoint strictly internal holes; not an Asset."""
    points: tuple[tuple[float, float], ...]
    holes: tuple[tuple[tuple[float, float], ...], ...]

    def __init__(self, points, *, holes=()):
        outer = _ring(points, 'outer ring', ccw=True)
        inner = tuple(_ring(h, f'hole {i}', ccw=False) for i,h in enumerate(holes))
        for i,h in enumerate(inner):
            if any(_intersects(*a,*b) for a in _edges(outer) for b in _edges(h)) or not _inside(h[0],outer):
                raise ValueError(f'hole {i}: must lie strictly inside the outer ring without contact')
            for j,other in enumerate(inner[:i]):
                if (any(_intersects(*a,*b) for a in _edges(h) for b in _edges(other))
                        or _inside(h[0],other) or _inside(other[0],h)):
                    raise ValueError(f'holes {j} and {i}: overlap, nesting or contact is not supported')
        object.__setattr__(self, 'points', outer)
        object.__setattr__(self, 'holes', inner)

    def to_dict(self):
        return dict(points=self.points, holes=self.holes)


def _section(profile):
    if not isinstance(profile, Polygon):
        raise TypeError('profile must be a Polygon')
    return _manifold().CrossSection([profile.points, *profile.holes])


def _mesh_data(solid, operation):
    mf = _manifold()
    if solid.status() != mf.Error.NoError or solid.is_empty():
        raise ValueError(f'{operation}: empty or invalid solid ({solid.status()})')
    mesh = solid.to_mesh64()
    vertices = np.asarray(mesh.vert_properties[:, :3], dtype=np.float64)
    triangles = np.asarray(mesh.tri_verts)
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or len(vertices) < 4 or
            triangles.ndim != 2 or triangles.shape[1] != 3 or not len(triangles) or
            not np.isfinite(vertices).all() or triangles.min() < 0 or triangles.max() >= len(vertices)
            or not math.isfinite(solid.volume()) or solid.volume() <= 0):
        raise ValueError(f'{operation}: invalid or zero-volume mesh')
    return dict(vertices=tuple(tuple(float(x) for x in row) for row in vertices),
                triangles=tuple(tuple(int(x) for x in row) for row in triangles))


def _mesh_asset(solid, construction, color, alpha):
    params = _mesh_data(solid, construction['op'])
    params['construction'] = construction
    asset = Asset(label=construction['op'])
    asset.add_primitive(primitive_record('mesh', params, color, alpha))
    return asset


def linear_extrude(profile, height, *, scale_top=(1.0, 1.0), center=False, color=(1,1,1), alpha=None):
    """Extrude XY along +Z; scale the top around the profile origin."""
    height = float(height)
    if not math.isfinite(height) or height <= 0:
        raise ValueError('linear_extrude: height must be finite and positive')
    scale = (float(scale_top),)*2 if np.isscalar(scale_top) else tuple(float(x) for x in scale_top)
    if len(scale) != 2 or any(not math.isfinite(x) or x <= 0 for x in scale):
        raise ValueError('linear_extrude: scale_top must be a positive scalar or pair')
    solid = _section(profile).extrude(height, scale_top=scale)
    if center:
        solid = solid.translate((0,0,-height/2))
    return _mesh_asset(solid, dict(op='linear_extrude',profile=profile.to_dict(),height=height,
        scale_top=scale,center=bool(center)), color, alpha)


def rotate_extrude(profile, *, angle=360.0, segments=64, color=(1,1,1), alpha=None):
    """Revolve (r,z) about +Z, starting in the XZ plane; r must be nonnegative."""
    if not isinstance(profile, Polygon):
        raise TypeError('profile must be a Polygon')
    angle = float(angle)
    if not math.isfinite(angle) or not 0 < angle <= 360:
        raise ValueError('rotate_extrude: angle must be in (0, 360] degrees')
    try:
        count = operator.index(segments)
    except TypeError as error:
        raise ValueError('rotate_extrude: segments must be an integer >= 3') from error
    if isinstance(segments, bool) or count < 3:
        raise ValueError('rotate_extrude: segments must be an integer >= 3')
    if any(p[0] < 0 for ring in (profile.points,*profile.holes) for p in ring):
        raise ValueError('rotate_extrude: profile radius r must be nonnegative')
    # Manifold counts segments over the requested arc; values below three
    # select its automatic resolution. Keep our full-circle resolution explicit.
    arc_segments = max(3, math.ceil(count * angle / 360.0))
    solid = _section(profile).revolve(circular_segments=arc_segments, revolve_degrees=angle)
    return _mesh_asset(solid,dict(op='rotate_extrude',profile=profile.to_dict(),angle=angle,segments=count),color,alpha)


def hull(*shapes, color=(1,1,1), alpha=None):
    """Convex hull of copied Asset operands; evaluated during GLB export."""
    if not shapes or any(not isinstance(s,Asset) for s in shapes):
        raise ValueError('hull requires one or more Asset operands')
    asset = Asset(label='Hull')
    for i,shape in enumerate(shapes):
        asset.attach_part(f'op_{i}',shape.copy())
    asset.add_primitive(primitive_record('hull', {}, color, alpha))
    return asset



def _pchip_slopes(positions, values):
    """Shape-preserving derivatives along axis 0, with nonuniform knot spacing.

    Uses the weighted harmonic mean and limited endpoint estimates described in
    scipy.interpolate.PchipInterpolator; no SciPy runtime dependency is needed.
    """
    values = np.asarray(values, dtype=np.float64)
    gaps = np.diff(positions).reshape((-1,) + (1,) * (values.ndim - 1))
    secants = np.diff(values, axis=0) / gaps
    slopes = np.zeros_like(values)
    if len(values) == 2:
        slopes[:] = secants[0]
        return slopes
    left, right = secants[:-1], secants[1:]
    monotone = (left != 0) & (right != 0) & (np.sign(left) == np.sign(right))
    w_left = 2 * gaps[1:] + gaps[:-1]
    w_right = gaps[1:] + 2 * gaps[:-1]
    denominator = (np.divide(w_left, left, out=np.zeros_like(left), where=monotone)
                   + np.divide(w_right, right, out=np.zeros_like(right), where=monotone))
    np.divide(w_left + w_right, denominator, out=slopes[1:-1], where=monotone)

    def endpoint(h0, h1, d0, d1):
        estimate = ((2 * h0 + h1) * d0 - h0 * d1) / (h0 + h1)
        estimate = np.where(np.sign(estimate) != np.sign(d0), 0, estimate)
        return np.where((np.sign(d0) != np.sign(d1)) & (np.abs(estimate) > 3 * np.abs(d0)),
                        3 * d0, estimate)

    slopes[0] = endpoint(gaps[0], gaps[1], secants[0], secants[1])
    slopes[-1] = endpoint(gaps[-1], gaps[-2], secants[-1], secants[-2])
    return slopes


def _pchip_evaluate(positions, values, slopes, samples):
    """Evaluate the Hermite interpolant at a one-dimensional sample array."""
    positions = np.asarray(positions, dtype=np.float64)
    values = np.asarray(values, dtype=np.float64)
    interval = np.clip(np.searchsorted(positions, samples, side='right') - 1, 0, len(positions)-2)
    gaps = positions[interval+1] - positions[interval]
    fraction = (np.asarray(samples) - positions[interval]) / gaps
    expand = (-1,) + (1,) * (values.ndim-1)
    t, h = fraction.reshape(expand), gaps.reshape(expand)
    return ((2*t**3 - 3*t**2 + 1) * values[interval]
            + (t**3 - 2*t**2 + t) * h * slopes[interval]
            + (-2*t**3 + 3*t**2) * values[interval+1]
            + (t**3 - t**2) * h * slopes[interval+1])


def _sample_loft_rings(points, positions, interpolation, samples_per_span):
    """Return unique axial sample positions and corresponding (vertices, 2) rings."""
    points = np.asarray(points, dtype=np.float64)
    positions = np.asarray(positions, dtype=np.float64)
    samples = np.concatenate([
        np.linspace(a, b, samples_per_span, endpoint=False)
        for a, b in zip(positions[:-1], positions[1:])
    ] + [positions[-1:]])
    if not np.isfinite(samples).all() or np.any(np.diff(samples) <= 0):
        raise ValueError('loft: sampled positions collapse at float64 precision')
    if interpolation == 'smooth' and len(points) > 2:
        rings = _pchip_evaluate(positions, points, _pchip_slopes(positions, points), samples)
    else:
        rings = np.concatenate([
            (1-t[:, None, None])*points[i] + t[:, None, None]*points[i+1]
            for i in range(len(points)-1)
            for t in [(samples[i*samples_per_span:(i+1)*samples_per_span]-positions[i])
                      / (positions[i+1]-positions[i])]
        ] + [points[-1:]])
    # Original control rings remain exact even at non-binary knot coordinates.
    rings[::samples_per_span] = points
    return samples, rings


def _loft_cap(ring, label, mf):
    """Triangulate a CCW cap, retaining all collinear boundary subdivisions."""
    from collections import Counter

    try:
        cap_triangles = mf.triangulate([ring], epsilon=0)
    except Exception as error:
        raise ValueError(f'loft: {label} triangulation failed: {error}') from error
    faces = []
    for indices in cap_triangles:
        tri = tuple(int(i) for i in indices)
        if min(tri) < 0 or max(tri) >= len(ring):
            raise ValueError(f'loft: {label} triangulation has an invalid index')
        area2 = _turn(*(ring[i] for i in tri))
        if not math.isfinite(area2) or area2 < 0:
            raise ValueError(f'loft: {label} triangulation has invalid winding or area')
        if area2 > 0:
            faces.append(tri)
    # Some triangulators omit a collinear boundary vertex (or emit a zero-area
    # triangle there). Split the adjacent nondegenerate triangle along that edge.
    while True:
        counts = Counter(tuple(sorted(edge)) for tri in faces for edge in _edges(tri))
        replacement = None
        for face_index, tri in enumerate(faces):
            for offset in range(3):
                a, b, c = tri[offset:] + tri[:offset]
                if counts[tuple(sorted((a,b)))] != 1 or b == (a+1) % len(ring):
                    continue
                path = [a]
                while path[-1] != b:
                    path.append((path[-1]+1) % len(ring))
                if all(_on_segment(ring[a], ring[b], ring[i]) for i in path[1:-1]):
                    replacement = (face_index, [(u,v,c) for u,v in zip(path[:-1],path[1:])])
                    break
            if replacement is not None:
                break
        if replacement is None:
            break
        index, split = replacement
        faces[index:index+1] = split
    directed = Counter(edge for tri in faces for edge in _edges(tri))
    boundary = {edge for edge in directed if directed[edge] != directed[edge[::-1]]}
    expected = {(i,(i+1) % len(ring)) for i in range(len(ring))}
    if boundary != expected or any(directed[edge] != 1 for edge in boundary):
        raise ValueError(f'loft: {label} triangulation does not preserve its boundary')
    return faces


def loft(profiles, positions, *, axis='z', interpolation='smooth', samples_per_span=8,
         color=(1,1,1), alpha=None):
    """Cap and join parallel, hole-free Polygon sections by corresponding vertices.

    Coordinates (u,v,s) map to (u,v,s), (s,u,v), or (v,s,u) for z, x, or y.
    Smooth interpolation is coordinatewise PCHIP; samples remain a triangle mesh.
    """
    try:
        profiles = tuple(profiles)
    except TypeError as error:
        raise ValueError('loft: profiles must contain at least two Polygons') from error
    if len(profiles) < 2:
        raise ValueError('loft: profiles must contain at least two Polygons')
    count = None
    for i, profile in enumerate(profiles):
        if not isinstance(profile, Polygon):
            raise TypeError(f'loft: profile {i} must be a Polygon')
        if profile.holes:
            raise ValueError(f'loft: profile {i} has holes; only hole-free profiles are supported')
        if count is not None and len(profile.points) != count:
            raise ValueError(f'loft: profile {i} has {len(profile.points)} vertices; expected {count}')
        count = len(profile.points)
    try:
        positions = np.asarray(positions, dtype=np.float64)
    except (TypeError, ValueError) as error:
        raise ValueError('loft: positions must be finite and strictly increasing') from error
    if positions.ndim != 1 or len(positions) != len(profiles):
        raise ValueError('loft: positions must have one coordinate per profile')
    gaps = np.diff(positions)
    if not np.isfinite(positions).all() or not np.isfinite(gaps).all() or np.any(gaps <= 0):
        raise ValueError('loft: positions must be finite and strictly increasing')
    if axis not in ('x', 'y', 'z'):
        raise ValueError("loft: axis must be 'x', 'y' or 'z'")
    if interpolation not in ('linear', 'smooth'):
        raise ValueError("loft: interpolation must be 'linear' or 'smooth'")
    try:
        samples = operator.index(samples_per_span)
    except TypeError as error:
        raise ValueError('loft: samples_per_span must be a positive integer') from error
    if isinstance(samples_per_span, (bool, np.bool_)) or samples <= 0:
        raise ValueError('loft: samples_per_span must be a positive integer')
    axial, rings = _sample_loft_rings([p.points for p in profiles], positions, interpolation, samples)
    for index, points in enumerate(rings):
        span = min(index // samples, len(profiles)-2)
        label = f'loft: sampled ring {index} in span {span}'
        ring = tuple(map(tuple, points))
        checked_ring = _ring(ring, label, ccw=True)
        if len(checked_ring) != count:
            raise ValueError(f'{label}: collapsed boundary vertices')
        if checked_ring != ring:
            raise ValueError(f'{label}: winding flipped')
    vertices = np.column_stack((rings.reshape(-1,2), np.repeat(axial,count)))
    vertices = np.ascontiguousarray(vertices[:, {'z':(0,1,2), 'x':(2,0,1), 'y':(1,2,0)}[axis]])
    faces = []
    for row in range(len(rings)-1):
        for j in range(count):
            a, b = row*count+j, row*count+(j+1) % count
            faces.extend(((a,b,b+count), (a,b+count,a+count)))
    mf = _manifold()
    faces.extend((a,c,b) for a,b,c in _loft_cap(rings[0], 'first cap', mf))
    last = (len(rings)-1)*count
    faces.extend((a+last,b+last,c+last) for a,b,c in _loft_cap(rings[-1], 'last cap', mf))
    triangles = np.ascontiguousarray(faces, dtype=np.uint64)
    crosses = np.cross(vertices[triangles[:,1]]-vertices[triangles[:,0]],
                       vertices[triangles[:,2]]-vertices[triangles[:,0]])
    invalid = np.flatnonzero(~np.isfinite(crosses).all(axis=1) | ~np.any(crosses != 0, axis=1))
    if len(invalid):
        triangle = int(invalid[0])
        raise ValueError(f'loft: triangle {triangle} has zero or non-finite area')
    solid = mf.Manifold(mf.Mesh64(vertices, triangles))
    asset = _mesh_asset(solid, dict(op='loft', profiles=tuple(p.to_dict() for p in profiles),
        positions=tuple(float(p) for p in positions), axis=axis, interpolation=interpolation,
        samples_per_span=samples), color, alpha)
    # Manifold may simplify the input mesh. Check the actual serialized geometry
    # as well, rather than assuming the raw strip/cap checks cover its output.
    params = asset._primitives[0]['params']
    result_vertices = np.asarray(params['vertices'], dtype=np.float64)
    result_faces = np.asarray(params['triangles'], dtype=np.int64)
    result_crosses = np.cross(result_vertices[result_faces[:,1]]-result_vertices[result_faces[:,0]],
                              result_vertices[result_faces[:,2]]-result_vertices[result_faces[:,0]])
    invalid = np.flatnonzero(~np.isfinite(result_crosses).all(axis=1)
                             | ~np.any(result_crosses != 0, axis=1))
    if len(invalid):
        raise ValueError(f'loft: evaluated triangle {int(invalid[0])} has zero or non-finite area')
    return asset
