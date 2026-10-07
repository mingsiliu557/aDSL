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


def _unit_face_normals(points):
    # Scale both edges before the cross product, then the cross before its
    # norm: a nonzero tiny face must not become zero by underflowing area^2.
    with np.errstate(over='ignore', invalid='ignore', divide='ignore', under='ignore'):
        edges = points[:,1:] - points[:,:1]
        finite = np.isfinite(edges).all(axis=(1,2))
        scale = np.max(np.abs(edges),axis=(1,2))
        safe_scale = np.where(finite & (scale > 0),scale,1.)
        scaled = edges/safe_scale[:,None,None]
        normal = np.cross(scaled[:,0],scaled[:,1])
        normal_scale = np.max(np.abs(normal),axis=1)
        nonzero = finite & np.isfinite(normal).all(axis=1) & (normal_scale > 0)
        normal /= np.where(nonzero,normal_scale,1.)[:,None]
        normal /= np.where(nonzero,np.linalg.norm(normal,axis=1),1.)[:,None]
    return normal,finite,nonzero


def _face_orientation_data(source_vertices, target_vertices, faces, *, source_faces=None):
    faces = np.asarray(faces,dtype=np.int64)
    previous = faces if source_faces is None else np.asarray(source_faces,dtype=np.int64)
    if faces.ndim != 2 or faces.shape[1] != 3 or previous.shape != faces.shape:
        raise ValueError('corresponding triangle indices must have equal (n,3) shape')
    source = np.asarray(source_vertices,dtype=np.float64)[previous]
    target = np.asarray(target_vertices,dtype=np.float64)[faces]
    if len(faces):
        source_normal,source_finite,source_nonzero = _unit_face_normals(source)
        target_normal,target_finite,target_nonzero = _unit_face_normals(target)
        with np.errstate(invalid='ignore'):
            dot = np.einsum('ij,ij->i',source_normal,target_normal)
    else:
        source_normal = target_normal = np.empty((0,3))
        source_finite = target_finite = source_nonzero = target_nonzero = np.empty(0,dtype=bool)
        dot = np.empty(0)
    comparable = source_nonzero & target_nonzero & np.isfinite(dot)
    flipped = comparable & (dot < 0)
    orthogonal = comparable & (dot == 0)
    target_zero = target_finite & ~target_nonzero
    bad = ~(comparable & (dot > 0))
    metrics = dict(valid=bool(not bad.any()),triangle_count=len(faces),
        flipped_faces=int(flipped.sum()),orthogonal_faces=int(orthogonal.sum()),
        source_zero_normals=int((source_finite & ~source_nonzero).sum()),
        target_zero_normals=int(target_zero.sum()),
        nonfinite_source_normals=int((~source_finite).sum()),
        nonfinite_target_normals=int((~target_finite).sum()),
        uncomparable_faces=int((~comparable).sum()),
        minimum_normal_dot=float(dot[comparable].min()) if comparable.any() else None,
        failing_face_indices=np.flatnonzero(bad)[:8].tolist())
    return dict(metrics=metrics,bad=bad,flipped=flipped,orthogonal=orthogonal,
        target_zero=target_zero,comparable=comparable,source_normal=source_normal,
        target_normal=target_normal,dot=dot)


def face_orientation_metrics(source_vertices, target_vertices, faces, *, source_faces=None):
    """Compare every corresponding geometric face normal, without angle slack.

    ``source_faces`` is used only when a local contraction changes indices;
    otherwise both vertex arrays use the exact same face indices.
    """
    return _face_orientation_data(source_vertices,target_vertices,faces,
        source_faces=source_faces)['metrics']


def _defect_counts(data):
    return dict(bad_faces=int(data['bad'].sum()),zero_or_collapsed_faces=int(data['target_zero'].sum()),
        flipped_faces=int(data['flipped'].sum()),orthogonal_faces=int(data['orthogonal'].sum()),
        uncomparable_faces=int((~data['comparable']).sum()))


def _minimum_height(points):
    edges = points[1:]-points[0]
    scale = float(np.abs(edges).max())
    if not scale:
        return 0.
    scaled = edges/scale
    longest = max(np.linalg.norm(scaled[0]),np.linalg.norm(scaled[1]),
                  np.linalg.norm(scaled[1]-scaled[0]))
    return float(scale*np.linalg.norm(np.cross(scaled[0],scaled[1]))/longest)


def _float32_ulps(vertices):
    values = np.asarray(vertices,dtype=np.float32)
    with np.errstate(over='ignore',invalid='ignore'):
        ulps = np.abs(np.spacing(values).astype(np.float64))
        toward_zero = np.abs(values.astype(np.float64)-np.nextafter(values,np.float32(0)).astype(np.float64))
    return np.where(np.isfinite(ulps),ulps,toward_zero)


def _finite_diagnostics(record):
    count=0
    def visit(value):
        nonlocal count
        if isinstance(value,(float,np.floating)) and not np.isfinite(value):
            count += 1
            return None
        if isinstance(value,dict): return {key:visit(item) for key,item in value.items()}
        if isinstance(value,(list,tuple)): return [visit(item) for item in value]
        return value
    result=visit(record)
    result['nonfinite_diagnostic_values']=count
    return result


def _defect_evidence(vertices,rounded,faces,materials,data,mm_per_unit,*,index_context):
    def kinds(index):
        result=[]
        if data['target_zero'][index]: result.append('ZERO_OR_COLLAPSED_FACE')
        if data['flipped'][index]: result.append('GEOMETRIC_NORMAL_FLIP')
        if data['orthogonal'][index]: result.append('ORTHOGONAL_NORMAL')
        if not data['comparable'][index]: result.append('NORMAL_UNCOMPARABLE')
        return result

    def normals(index):
        comparable = bool(data['comparable'][index])
        return dict(source_normal=data['source_normal'][index].tolist(),
            target_normal=data['target_normal'][index].tolist(),
            normal_dot=float(data['dot'][index]) if comparable else None)

    indices = sorted(np.flatnonzero(data['bad']),
        key=lambda index:(not bool(data['flipped'][index] or data['orthogonal'][index]),int(index)))
    if not indices:
        return dict(frame='target_local',unit='scene_units',index_context=index_context,
            total_bad_faces=0,recorded_bad_faces=0,truncated=False,faces=[],nonfinite_diagnostic_values=0)
    adjacency = _edges(faces)
    evidence=[]
    for index in indices[:8]:
        face = faces[index]
        ring_indices = np.flatnonzero(np.isin(faces,face).any(axis=1))
        ring_vertices = np.unique(faces[ring_indices])
        edge_details=[]
        for a,b in zip(face,np.roll(face,-1)):
            a,b=int(a),int(b)
            neighbors_a,link_a=_vertex_link(faces,a)
            neighbors_b,link_b=_vertex_link(faces,b)
            adjacent=adjacency[tuple(sorted((a,b)))]
            opposite={int(v) for triangle in faces[adjacent] for v in triangle if v not in (a,b)}
            edge_details.append(dict(vertex_indices=[a,b],adjacent_triangle_indices=adjacent,
                length_scene_units=float(np.linalg.norm(vertices[a]-vertices[b])),
                length_mm=float(np.linalg.norm(vertices[a]-vertices[b]))*mm_per_unit,
                target_coincident=bool(np.array_equal(rounded[a],rounded[b])),
                closed_link_condition=bool(len(adjacent)==2 and neighbors_a & neighbors_b==opposite and not link_a & link_b),
                common_link_vertices=sorted(neighbors_a & neighbors_b),opposite_vertices=sorted(opposite),
                common_link_edges=[list(edge) for edge in sorted(link_a & link_b)]))
        heights=dict(source=_minimum_height(vertices[face]),target=_minimum_height(rounded[face]))
        ulps=_float32_ulps(rounded[face])
        row=dict(triangle_index=int(index),material_id=int(materials[index]),
            source_vertex_indices=[int(v) for v in face],defect_kinds=kinds(index),
            edited_vertices_local_float64=vertices[face].tolist(),target_vertices_local=rounded[face].tolist(),
            minimum_height_scene_units=heights,
            minimum_height_mm={key:value*mm_per_unit for key,value in heights.items()},
            float32_ulp_scene_units=ulps.tolist(),float32_ulp_mm=(ulps*mm_per_unit).tolist(),
            edges=edge_details,frame='target_local',unit='scene_units',index_context=index_context,
            one_ring=dict(triangle_indices=ring_indices.tolist(),vertex_indices=ring_vertices.tolist(),
                edited_vertices_local_float64=vertices[ring_vertices].tolist(),
                target_vertices_local=rounded[ring_vertices].tolist(),
                faces=[dict(triangle_index=int(j),source_vertex_indices=faces[j].tolist(),
                    material_id=int(materials[j]),defect_kinds=kinds(j),**normals(j)) for j in ring_indices]),
            **normals(index))
        evidence.append(row)
    return _finite_diagnostics(dict(frame='target_local',unit='scene_units',index_context=index_context,
        total_bad_faces=len(indices),recorded_bad_faces=len(evidence),truncated=len(indices)>len(evidence),faces=evidence))


def require_face_orientation(source_vertices, target_vertices, faces, **context):
    """Reject a cast unless all source/target face normals stay comparable."""
    from .mesh_validity import MeshEvaluationError
    metrics = face_orientation_metrics(source_vertices,target_vertices,faces)
    if not metrics['valid']:
        raise MeshEvaluationError('TARGET_PRECISION_FACE_ORIENTATION_INVALID',
            'float32 conversion changed or collapsed a corresponding geometric face normal',
            stage='target_precision',failure_kind='target_precision',
            face_orientation=metrics,**context)
    return metrics


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
                        expected_components=None, mm_per_unit=1.0):
    """Return rounded vertices, faces, material IDs and verified repair record.

    Edits remain in float64 until validation. Contraction error is measured
    against every original vertex, including previously collapsed clusters;
    diagonal-change error uses a conservative projected-quad height bound.
    """
    from .mesh_validity import MeshEvaluationError, mesh_metrics, validate_mesh
    if not np.isfinite(mm_per_unit) or mm_per_unit <= 0:
        raise ValueError('mm_per_unit must be finite and positive')
    original = np.array(vertices, dtype=np.float64, copy=True)
    current_faces = np.array(faces, dtype=np.int64, copy=True)
    materials = np.array(face_ids, dtype=np.uint64, copy=True)
    _,source_metrics = validate_mesh(original, current_faces, face_ids=materials)
    original_euler = len(np.unique(current_faces))-len(_edges(current_faces))+len(current_faces)
    if expected_components is None:
        expected_components = source_metrics['material_components']
    if not np.isfinite(displacement_budget) or displacement_budget < 0:
        raise ValueError('positive finite local displacement budget required')
    with np.errstate(over='ignore',invalid='ignore'):
        rounded = original.astype(np.float32).astype(np.float64)
    before = mesh_metrics(rounded, current_faces)
    if not np.isfinite(rounded).all():
        raise MeshEvaluationError('TARGET_PRECISION_NONFINITE',
            'nonfinite float32 coordinates cannot be repaired by local edge edits',
            stage='target_precision',failure_kind='target_precision',metrics=before)
    initial_data = _face_orientation_data(original,rounded,current_faces)
    orientation = initial_data['metrics']
    if before['valid'] and orientation['valid']:
        cast_distance = float(np.linalg.norm(rounded-original,axis=1).max())
        if cast_distance > displacement_budget:
            raise MeshEvaluationError('LOCAL_PRECISION_REPAIR_REJECTED',
                'unchanged cast exceeds fixed geometric displacement budget',
                stage='target_precision',failure_kind='target_precision')
        return rounded, current_faces, materials, dict(status='UNCHANGED', operations=[],
            metrics_before=before, metrics_after=before,
            face_orientation=orientation,
            bad_face_count_before=0,bad_face_count_after=0,
            defect_counts_before=_defect_counts(initial_data),defect_counts_after=_defect_counts(initial_data),
            precision_defects_before=_defect_evidence(original,rounded,current_faces,materials,
                initial_data,mm_per_unit,index_context='initial_input_mesh'),
            remaining_bad_faces=[],remaining_bad_faces_total=0,
            remaining_bad_faces_recorded=0,remaining_bad_faces_truncated=False,mm_per_unit=mm_per_unit,
            maximum_vertex_cast_displacement=cast_distance,
            surface_displacement_upper_bound=cast_distance)
    vertices = original.copy()
    representatives = np.arange(len(vertices), dtype=np.int64)
    operations, rejected = [], Counter()
    flip_bound = 0.0
    initial_bad = int(initial_data['bad'].sum())
    initial_evidence = _defect_evidence(original,rounded,current_faces,materials,initial_data,
        mm_per_unit,index_context='initial_input_mesh')
    # Each accepted local operation strictly removes at least one distinct bad
    # face, so this finite bound never needs tolerance escalation.
    operation_limit = initial_bad
    for _ in range(operation_limit):
        rounded = vertices.astype(np.float32).astype(np.float64)
        crosses = _crosses(rounded, current_faces)
        current_data = _face_orientation_data(vertices,rounded,current_faces)
        bad_mask = current_data['bad']
        current_bad_count = int(bad_mask.sum())
        bad = np.flatnonzero(bad_mask)
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
                source_orientation = face_orientation_metrics(vertices,proposed_vertices,new_faces,
                    source_faces=current_faces[affected][survive])
                if not source_orientation['valid']:
                    rejected['orientation_or_collapse'] += 1
                    continue
                keys = {tuple(sorted(int(v) for v in face)) for face in new_faces}
                untouched = np.delete(current_faces,affected,axis=0)
                if len(keys) != len(new_faces) or any(tuple(sorted(int(v) for v in face)) in keys for face in untouched):
                    rejected['duplicate_face'] += 1
                    continue
                new_cast = _crosses(proposed_rounded,new_faces)
                old_zero = int(np.all(crosses[affected] == 0,axis=1).sum())
                new_zero = int(np.all(new_cast == 0,axis=1).sum())
                proposal_data = _face_orientation_data(proposed_vertices,proposed_rounded,new_faces)
                old_bad = int(bad_mask[affected].sum())
                new_bad = int(proposal_data['bad'].sum())
                if new_bad >= old_bad:
                    rejected['no_precision_improvement'] += 1
                    continue
                # A previous bad face may remain for the next finite step; an
                # originally healthy corresponding face must never turn bad.
                formerly_healthy = ~bad_mask[affected][survive]
                if np.any(formerly_healthy & proposal_data['bad']):
                    rejected['cast_orientation'] += 1
                    rejected['healthy_face_became_defective'] += 1
                    continue
                current_faces = np.vstack((untouched,new_faces))
                materials = np.concatenate((np.delete(materials,affected),materials[affected][survive]))
                representatives = proposed_reps
                vertices = proposed_vertices
                operations.append(dict(operation='edge_contraction', keep_vertex=keep, removed_vertex=drop,
                    position_kind=position_kind,removed_faces=2, zero_area_before=old_zero, zero_area_after=new_zero,
                    bad_face_count_before=current_bad_count,
                    bad_face_count_after=current_bad_count-old_bad+new_bad,
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
            proposal_data = _face_orientation_data(vertices,rounded,proposed)
            source_normals,_,source_nonzero = _unit_face_normals(vertices[proposed])
            if not source_nonzero.all() or np.any(source_normals @ normal <= 0):
                rejected['flip_orientation_or_precision'] += 1
                continue
            old_bad = int(bad_mask[adjacent].sum())
            new_bad = int(proposal_data['bad'].sum())
            if new_bad >= old_bad:
                rejected['no_precision_improvement'] += 1
                continue
            # Outside this two-face patch every face is unchanged. If either
            # prior patch face was healthy, strict decrease forces both new
            # faces to be healthy; no healthy region can gain a defect.
            current_faces[adjacent] = proposed
            flip_bound += height_bound
            operations.append(dict(operation='diagonal_flip', edge_before=[a,b], edge_after=[c,d],
                zero_area_before=int(np.all(crosses[adjacent] == 0,axis=1).sum()),
                zero_area_after=int(proposal_data['target_zero'].sum()),
                bad_face_count_before=current_bad_count,
                bad_face_count_after=current_bad_count-old_bad+new_bad,
                surface_displacement_upper_bound=height_bound))
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
    final_data = _face_orientation_data(vertices[used],final_vertices,final_faces)
    # Detailed records use the current pre-compaction vertex IDs, shared with
    # the corresponding float64 source, while counts cover every final face.
    current_final_data = _face_orientation_data(vertices,rounded,current_faces)
    final_evidence = _defect_evidence(vertices,rounded,current_faces,materials,current_final_data,
        mm_per_unit,index_context='current_local_edit_mesh')
    remaining = [row for row in final_evidence['faces'] if 'ZERO_OR_COLLAPSED_FACE' in row['defect_kinds']]
    report = dict(method='local_precision_edge_repair',status='REJECTED',
        operation_limit=operation_limit,operations=operations,rejected_candidates=dict(rejected),
        metrics_before=before,metrics_after=metrics,remaining_precision_degeneracies=remaining,
        bad_face_count_before=initial_bad,bad_face_count_after=int(final_data['bad'].sum()),
        defect_counts_before=_defect_counts(initial_data),defect_counts_after=_defect_counts(final_data),
        precision_defects_before=initial_evidence,remaining_bad_faces=final_evidence['faces'],
        remaining_bad_faces_total=final_evidence['total_bad_faces'],
        remaining_bad_faces_recorded=final_evidence['recorded_bad_faces'],
        remaining_bad_faces_truncated=final_evidence['truncated'],
        remaining_bad_faces_nonfinite_diagnostic_values=final_evidence['nonfinite_diagnostic_values'],mm_per_unit=mm_per_unit,
        euler_characteristic_before=int(original_euler),
        euler_characteristic_after=int(len(used)-len(_edges(final_faces))+len(final_faces)),
        maximum_original_vertex_displacement=contraction_distance,
        diagonal_flip_surface_displacement_bound=flip_bound,
        maximum_vertex_cast_displacement=cast_distance,
        surface_displacement_upper_bound=error_bound,displacement_budget=displacement_budget)
    report['face_orientation'] = final_data['metrics']
    try:
        _,source_after = validate_mesh(vertices[used],final_faces,face_ids=materials,expected_components=expected_components)
        report['metrics_after_local_edit_float64'] = source_after
        require_face_orientation(vertices[used],final_vertices,final_faces)
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
