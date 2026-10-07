"""Shared oriented Mesh64 validation and bounded output-precision conversion.

No Blender datablocks, global hole filling or component selection live here.
Closedness/Manifold construction are not a triangle self-intersection test;
that unimplemented check is explicitly reported as NOT_EVALUATED.
"""
from __future__ import annotations

from collections import defaultdict
import math
import numpy as np


class MeshEvaluationError(ValueError):
    """An execution error with additive, JSON-safe geometric evidence."""

    def __init__(self, code, message, *, stage='internal_evaluation',
                 failure_kind='geometry_evaluation', **context):
        self.diagnostic = dict(code=str(code), message=str(message), stage=stage,
                               failure_kind=failure_kind, **context)
        super().__init__(f'{code}: {message}')


def world_vertices(vertices, matrix):
    """Apply an affine matrix in NumPy float64, before any output cast."""
    matrix = np.asarray(matrix, dtype=np.float64)
    vertices = np.asarray(vertices, dtype=np.float64)
    if matrix.shape != (4, 4) or not np.isfinite(matrix).all():
        raise MeshEvaluationError('INVALID_TRANSFORM', 'finite 4x4 matrix required',
                                  stage='input_geometry', failure_kind='input_geometry')
    return vertices @ matrix[:3, :3].T + matrix[:3, 3]


def _arrays(vertices, faces):
    vertices = np.asarray(vertices, dtype=np.float64)
    faces = np.asarray(faces)
    if vertices.size == 0:
        vertices = vertices.reshape((0, 3))
    if faces.size == 0:
        faces = faces.reshape((0, 3))
    return vertices, faces


def mesh_metrics(vertices, faces, *, allow_empty=False):
    """Measure the actual triangles, merging only identical seam coordinates.

    Vertex manifoldness is a connected cycle in each vertex link, rather than
    edge incidence alone. Shells retain their original direction, including
    negatively oriented cavity walls.
    """
    try:
        vertices, faces = _arrays(vertices, faces)
    except (TypeError, ValueError) as error:
        return dict(valid=False, malformed_arrays=str(error), self_intersection='NOT_EVALUATED')
    if vertices.ndim == 0 or faces.ndim == 0:
        return dict(valid=False, malformed_arrays='vertices and triangle indices must be Nx3',
                    self_intersection='NOT_EVALUATED')
    row = dict(vertex_count=len(vertices), triangle_count=len(faces),
               self_intersection='NOT_EVALUATED',
               self_intersection_reason='No reliable complete triangle self-intersection detector is installed')
    if (vertices.ndim != 2 or vertices.shape[1] != 3 or
            faces.ndim != 2 or faces.shape[1] != 3):
        row.update(valid=False, malformed_arrays='vertices and triangle indices must be Nx3')
        return row
    row['nonfinite_coordinates'] = int((~np.isfinite(vertices)).sum())
    if not np.issubdtype(faces.dtype, np.integer):
        row.update(valid=False, invalid_indices='integer triangle indices required')
        return row
    row['invalid_indices'] = int(((faces < 0) | (faces >= len(vertices))).sum())
    if row['nonfinite_coordinates'] or row['invalid_indices']:
        row['valid'] = False
        return row
    if not len(faces):
        row.update(valid=bool(allow_empty), empty=True, zero_area_triangles=0,
                   duplicate_faces=0, boundary_edges=0, nonmanifold_edges=0,
                   nonmanifold_vertices=0, inconsistent_edges=0, shell_components=0,
                   signed_volume=0.0, surface_area=0.0)
        return row
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    triangles = inverse[faces]
    row['exact_coordinate_duplicates'] = len(vertices) - len(unique)
    row['duplicate_faces'] = int(len(triangles) - len(np.unique(np.sort(triangles, axis=1), axis=0)))
    # Local offsets avoid cancellation in the signed-volume sum.
    points = unique[triangles]
    with np.errstate(over='ignore', invalid='ignore'):
        crosses = np.cross(points[:, 1]-points[:, 0], points[:, 2]-points[:, 0])
    row['zero_area_triangles'] = int(np.all(crosses == 0, axis=1).sum())
    row['nonfinite_face_geometry'] = int((~np.isfinite(crosses)).sum())
    origin = unique[0]
    with np.errstate(over='ignore', invalid='ignore'):
        area = float(np.linalg.norm(crosses, axis=1).sum()/2)
        volume = float(np.einsum('ij,ij->i', points[:, 0]-origin, crosses).sum()/6)
    row['nonfinite_geometry_measurements'] = int(not math.isfinite(area)) + int(not math.isfinite(volume))
    row['surface_area'] = area if math.isfinite(area) else None
    row['signed_volume'] = volume if math.isfinite(volume) else None
    edges = np.concatenate([triangles[:, [0, 1]], triangles[:, [1, 2]], triangles[:, [2, 0]]])
    _, edge_ids, counts = np.unique(np.sort(edges, axis=1), axis=0, return_inverse=True, return_counts=True)
    winding = np.bincount(edge_ids, weights=np.where(edges[:, 0] < edges[:, 1], 1, -1))
    row['boundary_edges'] = int((counts == 1).sum())
    row['nonmanifold_edges'] = int((counts > 2).sum())
    row['inconsistent_edges'] = int(((counts == 2) & (winding != 0)).sum())
    incident = defaultdict(list)
    links = defaultdict(list)
    for i, (a, b, c) in enumerate(triangles):
        for v, x, y in ((a,b,c), (b,c,a), (c,a,b)):
            incident[int(v)].append(i)
            links[int(v)].append((int(x), int(y)))
    bad_vertices = 0
    for link in links.values():
        adjacency = defaultdict(list)
        for a, b in link:
            adjacency[a].append(b)
            adjacency[b].append(a)
        remaining = set(adjacency)
        if any(len(neighbors) != 2 for neighbors in adjacency.values()):
            bad_vertices += 1
            continue
        pending = [remaining.pop()]
        while pending:
            for neighbor in adjacency[pending.pop()]:
                if neighbor in remaining:
                    remaining.remove(neighbor)
                    pending.append(neighbor)
        if remaining:
            bad_vertices += 1
    row['nonmanifold_vertices'] = bad_vertices
    # Shell components count connected triangles; a cavity legitimately has
    # multiple shells and is still one material component in Manifold.
    parent = np.arange(len(triangles))
    def root(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i
    for ids in incident.values():
        first = root(ids[0])
        for i in ids[1:]:
            parent[root(i)] = first
    row['shell_components'] = len({root(i) for i in range(len(triangles))})
    row['empty'] = False
    defects = ('zero_area_triangles', 'nonfinite_face_geometry', 'nonfinite_geometry_measurements', 'duplicate_faces',
               'boundary_edges', 'nonmanifold_edges', 'nonmanifold_vertices', 'inconsistent_edges')
    row['valid'] = not any(row[k] for k in defects)
    return row


def _mesh_arrays(solid):
    raw = solid.to_mesh64()
    return (np.array(raw.vert_properties[:, :3], dtype=np.float64, order='C', copy=True),
            np.array(raw.tri_verts, dtype=np.uint64, order='C', copy=True), raw)


def checked_solid(solid, *, allow_empty=False, stage='internal_evaluation', **context):
    import manifold3d as mf
    if solid.status() != mf.Error.NoError:
        raise MeshEvaluationError('BOOLEAN_EVALUATION_FAILED',
            f'Manifold status {solid.status()}', stage=stage, solid_status=str(solid.status()), **context)
    if solid.is_empty():
        if allow_empty:
            return solid
        raise MeshEvaluationError('EMPTY_REQUIRED_GEOMETRY', 'required output is empty', stage=stage, **context)
    vertices, faces, _ = _mesh_arrays(solid)
    metrics = mesh_metrics(vertices, faces)
    volume = float(solid.volume())
    if not metrics['valid'] or not math.isfinite(volume) or volume <= 0:
        raise MeshEvaluationError('INVALID_EVALUATED_MESH', 'invalid oriented Mesh64 output',
            stage=stage, metrics=metrics, solid_volume=volume, **context)
    return solid


validate_solid = checked_solid


def validate_mesh(vertices, faces, *, allow_empty=False, stage='input_geometry',
                  expected_components=None, face_ids=None, **context):
    """Construct one oriented solid without turning cavity shells into fill."""
    import manifold3d as mf
    metrics = mesh_metrics(vertices, faces, allow_empty=allow_empty)
    if not metrics['valid']:
        raise MeshEvaluationError('INPUT_GEOMETRY_INVALID', 'mesh is not closed, manifold and consistently oriented',
            stage=stage, failure_kind='input_geometry', metrics=metrics, **context)
    vertices, faces = _arrays(vertices, faces)
    if not len(faces):
        return mf.Manifold(), metrics
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    kw = {} if face_ids is None else dict(face_id=np.asarray(face_ids, dtype=np.uint64))
    try:
        solid = mf.Manifold(mf.Mesh64(np.ascontiguousarray(unique, dtype=np.float64),
                                     np.ascontiguousarray(inverse[faces], dtype=np.uint64), **kw))
    except (ValueError, TypeError, RuntimeError) as error:
        raise MeshEvaluationError('INPUT_GEOMETRY_INVALID', str(error), stage=stage,
                                  failure_kind='input_geometry', metrics=metrics, **context) from error
    if solid.status() != mf.Error.NoError or solid.is_empty() or not math.isfinite(solid.volume()) or solid.volume() <= 0:
        raise MeshEvaluationError('INPUT_GEOMETRY_INVALID', f'Manifold rejected input: {solid.status()}',
            stage=stage, failure_kind='input_geometry', metrics=metrics,
            solid_status=str(solid.status()), **context)
    metrics['solid_status'] = str(solid.status())
    metrics['material_components'] = sum(float(piece.volume()) > 0 for piece in solid.decompose())
    if expected_components is not None and metrics['material_components'] != expected_components:
        raise MeshEvaluationError('MESH_COMPONENTS_CHANGED', 'material connectivity changed',
            stage=stage, metrics=metrics, expected_components=expected_components, **context)
    return solid, metrics


def target_mesh(solid_or_mesh, *, mm_per_unit=1.0, node_path='', **context):
    """Return (canonical local trimesh, recenter transform, conversion record).

    Vertices have already survived an actual float32 roundtrip. Their transform
    is kept in float64. GLB must emit this transform alongside the local mesh;
    STL has no transform field, so its print-local placement needs its own
    conversion and readback check. This function does not certify either file.
    """
    import trimesh
    if not np.isfinite(mm_per_unit) or mm_per_unit <= 0:
        raise ValueError('mm_per_unit must be finite and positive')
    context = dict(node_path=node_path, **context)
    if hasattr(solid_or_mesh, 'to_mesh64'):
        reference = checked_solid(solid_or_mesh, **context)
    elif hasattr(solid_or_mesh, 'vert_properties'):
        ids = np.asarray(solid_or_mesh.face_id)
        reference, _ = validate_mesh(np.asarray(solid_or_mesh.vert_properties)[:, :3],
            solid_or_mesh.tri_verts, face_ids=ids if len(ids) else None, **context)
    else:
        reference, _ = validate_mesh(solid_or_mesh.vertices, solid_or_mesh.faces, **context)
    rv, rf, raw = _mesh_arrays(reference)
    center = rv.min(axis=0) + (rv.max(axis=0)-rv.min(axis=0))/2
    local_reference = reference.translate(-center)
    lv, lf, _ = _mesh_arrays(local_reference)
    scale = max(1.0, float(np.abs(lv).max()))
    quantum = float(np.spacing(np.float32(scale)))
    bound = float(16*np.finfo(np.float32).eps*scale)
    reference_area, reference_volume = float(reference.surface_area()), float(reference.volume())
    components = sum(float(piece.volume()) > 0 for piece in reference.decompose())
    transform = np.eye(4, dtype=np.float64)
    transform[:3, 3] = center
    attempts = []
    for tolerance in (0.0, quantum, 2*quantum):
        try:
            candidate = local_reference if not tolerance else local_reference.simplify(tolerance)
            checked_solid(candidate, **context)
            v, f, raw = _mesh_arrays(candidate)
            with np.errstate(over='ignore', invalid='ignore'):
                rounded = v.astype(np.float32).astype(np.float64)
            cast_distance = float(np.linalg.norm(rounded-v, axis=1).max())
            measured, metrics = validate_mesh(rounded, f, stage='target_precision',
                expected_components=components, face_ids=raw.face_id, **context)
            geometry_bound = tolerance + cast_distance
            bounds_change = float(np.max(np.abs(np.array(measured.bounding_box())-np.array(local_reference.bounding_box()))))
            volume_change = abs(float(measured.volume())-reference_volume)
            volume_bound = reference_area*bound + 64*np.finfo(float).eps*reference_volume
            if (geometry_bound > bound or bounds_change > bound or volume_change > volume_bound):
                raise ValueError('precision conversion exceeds the fixed local geometric budget')
            row = dict(stage='target_precision', status='PASS', precision='float32',
                local_origin=center.tolist(), local_scale_scene_units=scale,
                simplify_tolerance_scene_units=tolerance,
                maximum_vertex_cast_displacement_mm=cast_distance*mm_per_unit,
                surface_displacement_upper_bound_mm=geometry_bound*mm_per_unit,
                displacement_budget_mm=bound*mm_per_unit,
                bounds_change_mm=bounds_change*mm_per_unit,
                volume_change_mm3=volume_change*mm_per_unit**3,
                volume_budget_mm3=volume_bound*mm_per_unit**3,
                shell_components=metrics['shell_components'], material_components=components,
                metrics=metrics, attempts=attempts, node_path=node_path,
                file_validation='NOT_EVALUATED',
                displacement_basis='Manifold simplify API bound plus triangle vertex float32 displacement')
            # Keep original triangle order and source-face IDs; no deletion or
            # proximity welding is performed on a rounded invalid mesh.
            mesh = trimesh.Trimesh(rounded, f.astype(np.int64), process=False)
            mesh.face_attributes['source_face_id'] = np.asarray(raw.face_id, dtype=np.uint64)
            mesh.metadata['target_precision'] = row
            return mesh, transform, row
        except (MeshEvaluationError, ValueError, RuntimeError) as error:
            attempts.append(dict(simplify_tolerance_scene_units=tolerance,
                diagnostic=error.diagnostic if isinstance(error, MeshEvaluationError) else str(error)))
    raise MeshEvaluationError('TARGET_PRECISION_UNREPRESENTABLE',
        'Mesh64 result is valid but no bounded float32 conversion passed validation',
        stage='target_precision', failure_kind='target_precision', attempts=attempts,
        internal_metrics=mesh_metrics(rv, rf), displacement_budget_mm=bound*mm_per_unit, **context)
