import itertools
import numpy as np
from .common import union, component_count
from .vendor import mesh_solid, solid_mesh, checked

def pge(parts, *, config):
    ids=sorted(parts); solids={i:mesh_solid(parts[i]) for i in ids}
    volumes={i:float(solids[i].volume()) for i in ids}; pairs=[]
    if any(v<=0 for v in volumes.values()): raise ValueError('PGE_INVALID_VOLUME')
    for i,j in itertools.combinations(ids,2):
        overlap=checked(solids[i]^solids[j]); v=float(overlap.volume())
        if v<=0: continue
        center=solid_mesh(overlap).center_mass; f={k:v/volumes[k] for k in (i,j)}; q={}
        for k in (i,j):
            # Deterministic AABB-centred enclosing sphere; not minimum sphere.
            radius=np.linalg.norm(parts[k].vertices-parts[k].bounds.mean(axis=0),axis=1).max()
            q[k]=float(np.linalg.norm(center-parts[k].center_mass)/radius)
        def score(a,b):
            return 2*q[a]+q[a]-q[b]+config['wf']*(f[a]-f[b])-config['ws']*f[a]+config['wv']*np.log(volumes[b]/volumes[a])
        si,sj=score(i,j),score(j,i)
        if max(f.values())>=.05 and max(f.values())>=3*min(f.values()):
            trim=max((i,j),key=lambda k:f[k]); reason='insertion_fraction'
        elif abs(q[i]-q[j])>=.4:
            trim=max((i,j),key=lambda k:q[k]); reason='eccentricity'
        else: trim=i if si>sj else j; reason='score'
        pairs.append(dict(a=i,b=j,trim=trim,margin=float(abs(si-sj)),scores=[float(si),float(sj)],reason=reason))
    def evaluate():
        output={}; losses={}
        for i in ids:
            cutters=[solids[p['b'] if p['a']==i else p['a']] for p in pairs if p['trim']==i]
            kept=checked(solids[i]-union(cutters)) if cutters else solids[i]
            losses[i]=max(0.,1-float(kept.volume())/volumes[i]); output[i]=kept
        return output,losses
    changed=set(); history=[]
    while True:
        output,losses=evaluate(); history.append(dict(losses=losses.copy(),assignments=[p['trim'] for p in pairs]))
        bad={i for i,x in losses.items() if x>config['max_loss']}
        if not bad: break
        eligible=[k for k,p in enumerate(pairs) if p['trim'] in bad and k not in changed]
        if not eligible: raise ValueError('PGE_VOLUME_LOSS_CAP_UNSATISFIED')
        k=min(eligible,key=lambda k:(pairs[k]['margin'],pairs[k]['a'],pairs[k]['b']))
        p=pairs[k]; p['trim']=p['b'] if p['trim']==p['a'] else p['a']; changed.add(k)
    meshes={i:solid_mesh(output[i]) for i in ids}
    for i,m in meshes.items():
        if not m.is_volume or component_count(m)>component_count(parts[i]): raise ValueError('PGE_DISCONNECTED_OR_INVALID')
    return meshes,dict(pairs=pairs,history=history,volumes_before=volumes,volumes_after={i:float(output[i].volume()) for i in ids})
