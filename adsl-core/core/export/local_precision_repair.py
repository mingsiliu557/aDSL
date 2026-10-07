"""Bounded local contractions/diagonal flips before an actual float32 cast.

The source is a valid oriented float64 triangle mesh. Candidate contractions
obey the closed-triangle link condition; diagonal flips preserve a convex
projected quadrilateral. No search between unrelated vertices or shells occurs.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import numpy as np


def _crosses(vertices, faces):
    points = vertices[faces]
    return np.cross(points[:,1]-points[:,0], points[:,2]-points[:,0])


def _edges(faces):
    adjacency = defaultdict(list)
    for index, face in enumerate(faces):
        for a,b in zip(face, np.roll(face,-1)):
            adjacency[tuple(sorted((int(a),int(b))))].append(index)
    return adjacency


def _vertex_link(faces, vertex):
    triangles = faces[np.any(faces == vertex, axis=1)]
    opposite = [tuple(sorted(int(v) for v in face if v != vertex)) for face in triangles]
    return {v for edge in opposite for v in edge}, set(opposite)


def repair_float32_mesh(vertices, faces, face_ids, *, displacement_budget,
                        expected_components=None):
    """Return rounded vertices, faces, material IDs and verified repair record.

    Edits remain in float64 until validation. Contraction error is measured
    against every original vertex, including previously collapsed clusters;
    diagonal-change error uses a conservative projected-quad height bound.
    """
    from .mesh_validity import MeshEvaluationError, mesh_metrics, validate_mesh
    original = np.array(vertices, dtype=np.float64, copy=True)
    current_faces = np.array(faces, dtype=np.int64, copy=True)
    materials = np.array(face_ids, dtype=np.uint64, copy=True)
    _,source_metrics = validate_mesh(original, current_faces, face_ids=materials)
    original_euler = len(np.unique(current_faces))-len(_edges(current_faces))+len(current_faces)
    if expected_components is None:
        expected_components = source_metrics['material_components']
    if not np.isfinite(displacement_budget) or displacement_budget < 0:
        raise ValueError('positive finite local displacement budget required')
    rounded = original.astype(np.float32).astype(np.float64)
    before = mesh_metrics(rounded, current_faces)
    if before['valid']:
        cast_distance = float(np.linalg.norm(rounded-original,axis=1).max())
        if cast_distance > displacement_budget:
            raise MeshEvaluationError('LOCAL_PRECISION_REPAIR_REJECTED',
                'unchanged cast exceeds fixed geometric displacement budget',
                stage='target_precision',failure_kind='target_precision')
        return rounded, current_faces, materials, dict(status='UNCHANGED', operations=[],
            metrics_before=before, metrics_after=before,
            maximum_vertex_cast_displacement=cast_distance,
            surface_displacement_upper_bound=cast_distance)
    vertices = original.copy()
    representatives = np.arange(len(vertices), dtype=np.int64)
    operations, rejected = [], Counter()
    flip_bound = 0.0
    initial_bad = int(before.get('zero_area_triangles',0))
    # Each accepted local operation strictly removes at least one cast-zero
    # triangle, so this finite bound never needs tolerance escalation.
    operation_limit = initial_bad
    for _ in range(operation_limit):
        rounded = vertices.astype(np.float32).astype(np.float64)
        crosses = _crosses(rounded, current_faces)
        bad = np.flatnonzero(np.all(crosses == 0,axis=1))
        if not len(bad):
            break
        edge_faces = _edges(current_faces)
        candidate_edges = {tuple(sorted((int(a),int(b)))) for face in current_faces[bad]
                           for a,b in zip(face,np.roll(face,-1))}
        ordered = sorted(candidate_edges, key=lambda edge:(float(np.linalg.norm(vertices[edge[0]]-vertices[edge[1]])),edge))
        accepted = False
        for a,b in ordered:
            incident_edge = edge_faces[(a,b)]
            if len(incident_edge) != 2:
                rejected['edge_not_two_sided'] += 1
                continue
            affected = np.flatnonzero(np.any((current_faces == a)|(current_faces == b),axis=1))
            if len(np.unique(materials[affected])) != 1:
                rejected['material_boundary'] += 1
                continue
            neighbors_a, link_a = _vertex_link(current_faces,a)
            neighbors_b, link_b = _vertex_link(current_faces,b)
            opposite = {int(v) for face in current_faces[incident_edge] for v in face if v not in (a,b)}
            if neighbors_a & neighbors_b != opposite or link_a & link_b:
                rejected['link_condition'] += 1
                continue
            if np.linalg.norm(vertices[a]-vertices[b]) > displacement_budget:
                continue
            midpoint = (vertices[a]+vertices[b])/2
            positions = ((a,b,vertices[a],'source_endpoint'),
                         (b,a,vertices[b],'source_endpoint'),
                         (a,b,midpoint,'edge_midpoint'))
            for keep,drop,position,position_kind in positions:
                proposed_vertices = vertices.copy()
                proposed_vertices[keep] = position
                proposed_rounded = proposed_vertices.astype(np.float32).astype(np.float64)
                proposed_reps = representatives.copy()
                proposed_reps[proposed_reps == drop] = keep
                contraction_distance = float(np.linalg.norm(proposed_vertices[proposed_reps]-original,axis=1).max())
                cast_distance = float(np.linalg.norm(proposed_rounded-proposed_vertices,axis=1).max())
                if contraction_distance+flip_bound+cast_distance > displacement_budget:
                    rejected['displacement_budget'] += 1
                    continue
                replacement = current_faces[affected].copy()
                replacement[replacement == drop] = keep
                survive = np.array([len(set(face)) == 3 for face in replacement])
                new_faces = replacement[survive]
                if len(replacement)-len(new_faces) != 2:
                    rejected['unexpected_face_removal'] += 1
                    continue
                new_normal = _crosses(proposed_vertices,new_faces)
                old_normal = _crosses(vertices,current_faces[affected][survive])
                if np.any(np.all(new_normal == 0,axis=1)) or np.any(np.einsum('ij,ij->i',new_normal,old_normal) <= 0):
                    rejected['orientation_or_collapse'] += 1
                    continue
                keys = {tuple(sorted(int(v) for v in face)) for face in new_faces}
                untouched = np.delete(current_faces,affected,axis=0)
                if len(keys) != len(new_faces) or any(tuple(sorted(int(v) for v in face)) in keys for face in untouched):
                    rejected['duplicate_face'] += 1
                    continue
                old_bad = int(np.all(crosses[affected] == 0,axis=1).sum())
                new_cast = _crosses(proposed_rounded,new_faces)
                new_bad = int(np.all(new_cast == 0,axis=1).sum())
                if new_bad >= old_bad:
                    rejected['no_precision_improvement'] += 1
                    continue
                if np.any(np.einsum('ij,ij->i',new_cast,new_normal) < 0):
                    rejected['cast_orientation'] += 1
                    continue
                current_faces = np.vstack((untouched,new_faces))
                materials = np.concatenate((np.delete(materials,affected),materials[affected][survive]))
                representatives = proposed_reps
                vertices = proposed_vertices
                operations.append(dict(operation='edge_contraction', keep_vertex=keep, removed_vertex=drop,
                    position_kind=position_kind,removed_faces=2, zero_area_before=old_bad, zero_area_after=new_bad,
                    maximum_original_vertex_displacement=contraction_distance))
                accepted = True
                break
            if accepted:
                break
        if accepted:
            continue
        # No safe contraction: change only an affected local diagonal. Every
        # vertex remains unchanged and material seams remain constrained.
        for a,b in ordered:
            adjacent = edge_faces[(a,b)]
            if len(adjacent) != 2 or materials[adjacent[0]] != materials[adjacent[1]]:
                continue
            c = next(int(v) for v in current_faces[adjacent[0]] if v not in (a,b))
            d = next(int(v) for v in current_faces[adjacent[1]] if v not in (a,b))
            if c == d or tuple(sorted((c,d))) in edge_faces:
                rejected['flip_link_condition'] += 1
                continue
            face0 = current_faces[adjacent[0]]
            directed = any(int(face0[j]) == a and int(face0[(j+1)%3]) == b for j in range(3))
            if not directed:
                a,b = b,a
            proposed = np.array(((c,d,b),(d,c,a)),dtype=np.int64)
            normals = _crosses(vertices,current_faces[adjacent])
            normal = normals.sum(axis=0)
            size = np.linalg.norm(normal)
            if not size:
                continue
            normal /= size
            points = vertices[[c,a,d,b]]
            heights = (points-points[0]) @ normal
            height_bound = float(np.ptp(heights))
            projected_edges = np.roll(points,-1,axis=0)-points
            turns = np.einsum('ij,j->i',np.cross(projected_edges,np.roll(projected_edges,-1,axis=0)),normal)
            if np.any(turns <= 0):
                rejected['nonconvex_flip'] += 1
                continue
            cast_distance = float(np.linalg.norm(rounded-vertices,axis=1).max())
            contraction_distance = float(np.linalg.norm(vertices[representatives]-original,axis=1).max())
            if contraction_distance+flip_bound+height_bound+cast_distance > displacement_budget:
                rejected['flip_displacement_budget'] += 1
                continue
            new_normal = _crosses(vertices,proposed)
            new_cast = _crosses(rounded,proposed)
            if np.any(new_normal @ normal <= 0) or np.any(new_cast @ normal <= 0):
                rejected['flip_orientation_or_precision'] += 1
                continue
            old_bad = int(np.all(crosses[adjacent] == 0,axis=1).sum())
            if not old_bad:
                continue
            current_faces[adjacent] = proposed
            flip_bound += height_bound
            operations.append(dict(operation='diagonal_flip', edge_before=[a,b], edge_after=[c,d],
                zero_area_before=old_bad, zero_area_after=0, surface_displacement_upper_bound=height_bound))
            accepted = True
            break
        if not accepted:
            break
    rounded = vertices.astype(np.float32).astype(np.float64)
    # Remove unused storage only; triangle deletion came exclusively from a
    # validated topological contraction, not arbitrary filtering after casting.
    used, inverse = np.unique(current_faces,return_inverse=True)
    final_vertices, final_faces = rounded[used],inverse.reshape((-1,3))
    metrics = mesh_metrics(final_vertices,final_faces)
    contraction_distance = float(np.linalg.norm(vertices[representatives]-original,axis=1).max())
    cast_distance = float(np.linalg.norm(rounded[used]-vertices[used],axis=1).max())
    error_bound = contraction_distance+flip_bound+cast_distance
    remaining = []
    final_crosses = _crosses(final_vertices,final_faces)
    source_edge_faces = _edges(current_faces)
    for index in np.flatnonzero(np.all(final_crosses == 0,axis=1))[:8]:
        face = current_faces[int(index)]
        edge_details = []
        for a,b in zip(face,np.roll(face,-1)):
            a,b = int(a),int(b)
            adjacent = source_edge_faces[tuple(sorted((a,b)))]
            neighbors_a,link_a = _vertex_link(current_faces,a)
            neighbors_b,link_b = _vertex_link(current_faces,b)
            opposite = {int(v) for triangle in current_faces[adjacent] for v in triangle if v not in (a,b)}
            edge_details.append(dict(vertex_indices=[a,b],
                length_scene_units=float(np.linalg.norm(vertices[a]-vertices[b])),
                target_coincident=bool(np.array_equal(rounded[a],rounded[b])),
                closed_link_condition=bool(len(adjacent)==2 and neighbors_a & neighbors_b == opposite and not link_a & link_b),
                common_link_vertices=sorted(neighbors_a & neighbors_b),opposite_vertices=sorted(opposite),
                common_link_edges=[list(edge) for edge in sorted(link_a & link_b)]))
        remaining.append(dict(triangle_index=int(index),material_id=int(materials[index]),
            source_vertex_indices=[int(v) for v in face],
            edited_vertices_local_float64=vertices[face].tolist(),target_vertices_local=rounded[face].tolist(),
            edges=edge_details,frame='target_local',unit='scene_units'))
    report = dict(method='local_precision_edge_repair',status='REJECTED',
        operation_limit=operation_limit,operations=operations,rejected_candidates=dict(rejected),
        metrics_before=before,metrics_after=metrics,remaining_precision_degeneracies=remaining,
        euler_characteristic_before=int(original_euler),
        euler_characteristic_after=int(len(used)-len(_edges(final_faces))+len(final_faces)),
        maximum_original_vertex_displacement=contraction_distance,
        diagonal_flip_surface_displacement_bound=flip_bound,
        maximum_vertex_cast_displacement=cast_distance,
        surface_displacement_upper_bound=error_bound,displacement_budget=displacement_budget)
    try:
        _,source_after = validate_mesh(vertices[used],final_faces,face_ids=materials,expected_components=expected_components)
        report['metrics_after_local_edit_float64'] = source_after
        validate_mesh(final_vertices,final_faces,face_ids=materials,expected_components=expected_components)
        if metrics.get('shell_components') != source_metrics.get('shell_components'):
            raise ValueError('local repair changed oriented shell connectivity')
        if report['euler_characteristic_after'] != original_euler:
            raise ValueError('local repair changed the boundary Euler characteristic')
        if set(materials.tolist()) != set(np.asarray(face_ids).tolist()):
            raise ValueError('local repair removed an entire source material region')
        if error_bound > displacement_budget:
            raise ValueError('local repair exceeds fixed geometric displacement budget')
    except (MeshEvaluationError,ValueError) as error:
        raise MeshEvaluationError('LOCAL_PRECISION_REPAIR_REJECTED',
            'local link-safe edits did not produce a valid bounded float32 result',
            stage='target_precision',failure_kind='target_precision',repair=report,
            validation_reason=str(error)) from error
    report['status'] = 'APPLIED'
    return final_vertices,final_faces,materials,report
