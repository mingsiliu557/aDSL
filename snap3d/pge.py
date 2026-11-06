"""Deterministic pairwise PGE with per-cut sliver cleanup and rollback."""
import itertools
import numpy as np
from .common import component_count
from .vendor import mesh_solid, solid_mesh, checked
from .vendor.mesh_validity import MeshEvaluationError


def _cut(before, cutter, *, sliver_thickness_mm):
    before_count=component_count(solid_mesh(before))
    before_components=[s for s in before.decompose() if s.volume()>0]
    candidate=checked(before-cutter)
    children=[s for s in candidate.decompose() if s.volume()>0]
    by_parent=[[] for _ in before_components]
    for index,child in enumerate(children):
        # Identify the pre-cut island that split; do not reassign cavities.
        parent=max(range(len(before_components)),
                   key=lambda i:float(checked(child^before_components[i]).volume()))
        by_parent[parent].append(index)
    removed=[]
    for indices in by_parent:
        if len(indices)<=1:continue
        # One principal survivor inherits the pre-cut component. Only extra
        # children are new fragments; existing isolated small parts stay intact.
        principal=max(indices,key=lambda i:(float(children[i].volume()),-i))
        removed.extend(i for i in indices if i!=principal and
                       2*float(children[i].volume())/float(solid_mesh(children[i]).area)<sliver_thickness_mm)
    for index in removed:
        candidate=checked(candidate-children[index])
    mesh=solid_mesh(candidate)
    before_overlap=float(checked(before^cutter).volume())
    proposed_overlap=float(checked(candidate^cutter).volume())
    count_error=None
    try:
        after_count=component_count(mesh) if mesh.is_volume else len(children)
    except MeshEvaluationError as error:
        metrics=error.diagnostic.get('metrics',{})
        if any(value for key,value in metrics.items() if key.startswith('nonfinite_') or key=='invalid_indices'):
            raise
        # A non-manifold cut is a local rejected edit, not a bad input case.
        count_error=error.diagnostic;after_count=len(children)
    reasons=[]
    if proposed_overlap>before_overlap:reasons.append('OVERLAP_INCREASED')
    if not mesh.is_volume:reasons.append('NOT_CLOSED_VOLUME')
    if count_error is not None:reasons.append('INVALID_CUT_MATERIAL')
    if after_count!=before_count:reasons.append('COMPONENT_COUNT_CHANGED')
    retained=not reasons
    removed_volume=sum(float(children[i].volume()) for i in removed)
    return (candidate if retained else before),dict(
        retained=retained,rollback_reasons=reasons,component_count_diagnostic=count_error,
        slivers_removed_count=len(removed),slivers_removed_volume_mm3=removed_volume,
        slivers_removed_volumes_mm3=[float(children[i].volume()) for i in removed],
        slivers_removed_thicknesses_mm=[2*float(children[i].volume())/float(solid_mesh(children[i]).area) for i in removed],
        retained_slivers_removed_count=len(removed) if retained else 0,
        retained_slivers_removed_volume_mm3=removed_volume if retained else 0.,
        component_count_before=before_count,candidate_component_count=after_count,
        component_count_after=after_count if retained else before_count,
        overlap_before_mm3=before_overlap,candidate_overlap_after_mm3=proposed_overlap,
        overlap_after_mm3=proposed_overlap if retained else before_overlap)


def pge(parts, *, config, D=None):
    ids=sorted(parts)
    if not ids:raise ValueError('PGE_EMPTY_INPUT')
    if any(not parts[i].is_volume for i in ids):raise ValueError('PGE_INPUT_NOT_CLOSED_VOLUME')
    solids={i:mesh_solid(parts[i]) for i in ids}
    volumes={i:float(solids[i].volume()) for i in ids};pairs=[]
    if any(not np.isfinite(v) or v<=0 for v in volumes.values()):raise ValueError('PGE_INVALID_VOLUME')
    # The real pipeline always passes its frozen assembly D. The fallback
    # preserves the standalone API used by small geometry unit tests.
    if D is None:
        bounds=np.asarray([parts[i].bounds for i in ids])
        D=float(np.linalg.norm(bounds[:,1].max(axis=0)-bounds[:,0].min(axis=0)))
    resolution=config.get('editing_grid_resolution',256)
    sliver_cells=config.get('sliver_thickness_cells',1.)
    if (not np.isfinite(D) or D<=0 or isinstance(resolution,bool) or
            not isinstance(resolution,int) or resolution<=0 or
            not np.isfinite(sliver_cells) or sliver_cells<=0):raise ValueError('PGE_INVALID_GRID')
    cell_volume=(D/resolution)**3;sliver_limit=sliver_cells*D/resolution
    # Assignment scores and insertion tests remain based on original operands.
    for i,j in itertools.combinations(ids,2):
        overlap=checked(solids[i]^solids[j]);v=float(overlap.volume())
        if v<=0:continue
        center=solid_mesh(overlap).center_mass;f={k:v/volumes[k] for k in (i,j)};q={}
        for k in (i,j):
            radius=np.linalg.norm(parts[k].vertices-parts[k].bounds.mean(axis=0),axis=1).max()
            q[k]=float(np.linalg.norm(center-parts[k].center_mass)/radius)
        def score(a,b):
            return 2*q[a]+q[a]-q[b]+config['wf']*(f[a]-f[b])-config['ws']*f[a]+config['wv']*np.log(volumes[b]/volumes[a])
        si,sj=score(i,j),score(j,i)
        if max(f.values())>=.05 and max(f.values())>=3*min(f.values()):
            trim=max((i,j),key=lambda k:f[k]);reason='insertion_fraction'
        elif abs(q[i]-q[j])>=.4:
            trim=max((i,j),key=lambda k:q[k]);reason='eccentricity'
        else:trim=i if si>sj else j;reason='score'
        pairs.append(dict(a=i,b=j,trim=trim,margin=float(abs(si-sj)),scores=[float(si),float(sj)],reason=reason))

    def evaluate():
        # Replay from original material after an ownership flip. Replaying the
        # full deterministic sequence also updates every dependent later cut.
        output=solids.copy();cuts=[]
        for index,pair in enumerate(pairs):
            trim=pair['trim'];other=pair['b'] if pair['a']==trim else pair['a']
            output[trim],record=_cut(output[trim],output[other],sliver_thickness_mm=sliver_limit)
            cuts.append(dict(pair=[pair['a'],pair['b']],pair_index=index,trim=trim,**record))
        losses={i:max(0.,1-float(output[i].volume())/volumes[i]) for i in ids}
        return output,losses,cuts

    changed=set();history=[]
    while True:
        output,losses,cuts=evaluate()
        history.append(dict(losses=losses.copy(),assignments=[p['trim'] for p in pairs],cuts=cuts))
        bad={i for i,x in losses.items() if x>config['max_loss']}
        history[-1]['volume_cap_satisfied']=not bad
        if not bad:break
        eligible=[k for k,p in enumerate(pairs) if p['trim'] in bad and k not in changed]
        if not eligible:
            history[-1]['warning']='PGE_VOLUME_LOSS_CAP_UNSATISFIED'
            break
        k=min(eligible,key=lambda k:(pairs[k]['margin'],pairs[k]['a'],pairs[k]['b']))
        p=pairs[k];p['trim']=p['b'] if p['trim']==p['a'] else p['a'];changed.add(k)

    residual=[]
    for i,j in itertools.combinations(ids,2):
        volume=float(checked(output[i]^output[j]).volume())
        residual.append(dict(a=i,b=j,volume_mm3=volume,volume_cells=volume/cell_volume))
    total=sum(item['volume_mm3'] for item in residual)
    return {i:solid_mesh(output[i]) for i in ids},dict(
        pairs=pairs,history=history,cuts=cuts,
        volumes_before=volumes,volumes_after={i:float(output[i].volume()) for i in ids},
        D_mm=D,editing_grid_resolution=resolution,cell_volume_mm3=cell_volume,
        sliver_thickness_cells=sliver_cells,sliver_thickness_limit_mm=sliver_limit,
        volume_cap_satisfied=not bad,
        warnings=[] if not bad else ['PGE_VOLUME_LOSS_CAP_UNSATISFIED'],
        rolled_back_cut_count=sum(not c['retained'] for c in cuts),
        retained_cut_count=sum(c['retained'] for c in cuts),
        slivers_removed_count=sum(c['retained_slivers_removed_count'] for c in cuts),
        slivers_removed_volume_mm3=sum(c['retained_slivers_removed_volume_mm3'] for c in cuts),
        residual_overlaps=residual,residual_overlap_volume_mm3=total,
        residual_overlap_volume_cells=total/cell_volume)
