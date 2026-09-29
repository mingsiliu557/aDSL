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
    return ring if (area2 > 0) == ccw else tuple(reversed(ring))


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
