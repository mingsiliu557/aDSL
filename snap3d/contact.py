import itertools
import numpy as np
import trimesh
from scipy.ndimage import distance_transform_edt
from trimesh.collision import CollisionManager
from .vendor.contact_area import facing_area, _sampling_surface
from .vendor import mesh_solid,checked
from .connector import basis

def material_distance(a,b):
    overlap=checked(mesh_solid(a)^mesh_solid(b))
    if overlap.volume()>0: return 0.,'manifold_intersection'
    manager=CollisionManager(); manager.add_object('a',a)
    if manager.in_collision_single(b): return 0.,'fcl_collision'
    d=float(manager.min_distance_single(b))
    if not np.isfinite(d) or d<0: raise ValueError('INVALID_FCL_DISTANCE')
    return d,'fcl_triangle_distance'

def anchor(receiver,donor,*,D,delta,grid_ratio):
    surface,normals=_sampling_surface(receiver,donor,delta)
    if surface is None: raise ValueError('NO_CONTACT_PATCH')
    points,faces=trimesh.sample.sample_surface(surface,65536,seed=0)
    keep=[]
    for start in range(0,len(points),1024):
        p=points[start:start+1024];_,d,t=trimesh.proximity.closest_point(donor,p)
        n=normals[faces[start:start+1024]]
        keep.extend(((d<=delta)&(np.einsum('ij,ij->i',n,donor.face_normals[t])<0)).tolist())
    keep=np.asarray(keep); points=points[keep]; ns=normals[faces[keep]]
    if not len(points): raise ValueError('NO_CONTACT_SAMPLES')
    normal=ns.mean(axis=0)
    if np.linalg.norm(normal)<1e-6: raise ValueError('AMBIGUOUS_CONTACT_NORMAL')
    normal/=np.linalg.norm(normal); B=basis(-normal); center=points.mean(axis=0)
    uv=(points-center)@B[:,:2]; step=grid_ratio*D
    lo=uv.min(axis=0)-step; hi=uv.max(axis=0)+step
    sizes=np.ceil((hi-lo)/step).astype(int)+1
    if np.prod(sizes)>2_000_000: raise ValueError('CONTACT_RASTER_TOO_LARGE')
    ij=np.indices(tuple(sizes)).reshape(2,-1).T; grid=lo+ij*step
    guesses=center+grid@B[:,:2].T; valid=np.zeros(len(grid),dtype=bool); projected=np.empty_like(guesses); faceids=np.empty(len(grid),dtype=int)
    for start in range(0,len(grid),1024):
        q,dr,tr=trimesh.proximity.closest_point(receiver,guesses[start:start+1024])
        _,dd,td=trimesh.proximity.closest_point(donor,q)
        sl=slice(start,start+len(q)); projected[sl]=q;faceids[sl]=tr
        valid[sl]=(dr<=step)&(dd<=delta)&(np.einsum('ij,ij->i',receiver.face_normals[tr],donor.face_normals[td])<0)
    mask=valid.reshape(tuple(sizes)); mask[[0,-1],:]=False;mask[:,[0,-1]]=False
    if not mask.any(): raise ValueError('CONTACT_PATCH_BELOW_RASTER_RESOLUTION')
    distances=distance_transform_edt(mask); k=int(np.argmax(distances))
    return projected[k],-receiver.face_normals[faceids[k]],float(distances.ravel()[k]*step)

def contact_graph(parts, *, D, config, connector_config):
    edges=[]; pairs=[];delta=config['delta_ratio']*D
    for a,b in itertools.combinations(sorted(parts),2):
        distance,method=material_distance(parts[a],parts[b]);row=dict(a=a,b=b,distance_mm=distance,distance_method=method)
        if distance<=delta:
            area=facing_area(parts[a],parts[b],delta_mm=delta,minimum_area_mm2=config['area_ratio']*D*D);row['area']=area
            if area['edge']:
                donor,receiver=sorted([a,b],key=lambda i:(parts[i].volume,i))
                p,n,margin=anchor(parts[receiver],parts[donor],D=D,delta=delta,grid_ratio=config['grid_ratio'])
                edges.append(dict(id=f'{a}--{b}',a=a,b=b,donor=donor,receiver=receiver,anchor=p.tolist(),inward=n.tolist(),patch_inradius_mm=margin))
        pairs.append(row)
    theta={e['id']:[0.,0.,0.,0.,connector_config['min_radius_ratio']*D,connector_config['min_length_ratio']*D] for e in edges}
    return edges,theta,pairs
