"""Budget-only quadric simplification after repair; unchanged originals on failure."""
import numpy as np
import trimesh


def _quadric(mesh, target):
    return mesh.simplify_quadric_decimation(face_count=target)


def _close(mesh):
    import pymeshfix
    tool=pymeshfix.MeshFix(mesh.vertices,mesh.faces)
    tool.repair(joincomp=False,remove_smallest_components=False)
    return trimesh.Trimesh(tool.points,tool.faces,process=False)


def _targets(counts, limit):
    # Integer largest-remainder allocation: deterministic ties by part ID.
    total=sum(counts.values());targets={k:limit*n//total for k,n in counts.items()}
    order=sorted(counts,key=lambda k:(-(limit*counts[k]%total),k))
    for key in order[:limit-sum(targets.values())]:targets[key]+=1
    return targets


def simplify_parts(parts, *, max_faces_per_case):
    if isinstance(max_faces_per_case,bool) or not isinstance(max_faces_per_case,int) or max_faces_per_case<1:
        raise ValueError('INVALID_MAX_FACES_PER_CASE')
    counts={key:len(mesh.faces) for key,mesh in parts.items()};total=sum(counts.values())
    targets=counts if total<=max_faces_per_case else _targets(counts,max_faces_per_case)
    output={};records=[]
    for key,original in parts.items():
        before=len(original.faces);result=original;fallback=False;reason=None;fixed=False
        if total>max_faces_per_case and targets[key]<before:
            try:
                candidate=_quadric(original.copy(),targets[key])
                if not candidate.is_volume:
                    fixed=True;candidate=_close(candidate)
                if not candidate.is_volume or not np.isfinite(candidate.vertices).all():
                    raise ValueError('SIMPLIFIED_MESH_NOT_VOLUME')
                result=candidate
            except (ValueError,RuntimeError) as error:
                fallback=True;reason=f'{type(error).__name__}: {error}'
        before_volume=float(original.volume) if original.is_volume else None
        after_volume=float(result.volume) if result.is_volume else None
        ratio=(after_volume-before_volume)/before_volume if before_volume is not None and after_volume is not None and before_volume>0 else None
        records.append(dict(part_id=key,faces_before=before,target_faces=targets[key],faces_after=len(result.faces),
            volume_before_mm3=before_volume,volume_after_mm3=after_volume,volume_change_ratio=ratio,
            is_volume=bool(result.is_volume),pymeshfix_applied=fixed,fallback=fallback,reason=reason,
            unchanged=bool(result is original)))
        output[key]=result
    after=sum(len(mesh.faces) for mesh in output.values())
    return output,dict(max_faces_per_case=max_faces_per_case,total_faces_before=total,total_faces_after=after,
        simplification_requested=total>max_faces_per_case,within_budget=after<=max_faces_per_case,
        fallback_parts=[r['part_id'] for r in records if r['fallback']],parts=records,
        deviation='compute-budget preprocessing; the paper did not simplify X-Part outputs')
