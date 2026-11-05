"""Circular mushroom head and a blind counterbore. Rebuild from immutable PGE."""
import itertools
import numpy as np
from scipy.spatial.transform import Rotation
import manifold3d as mf
from .vendor import mesh_solid,solid_mesh,checked
from .common import component_count,union

def basis(normal):
    n=np.asarray(normal,dtype=float); n=n/np.linalg.norm(n)
    seed=np.eye(3)[np.argmin(np.abs(n))]; u=np.cross(seed,n); u/=np.linalg.norm(u)
    return np.column_stack([u,np.cross(n,u),n])

def cylinder(radius,low,high):
    if high<=low or radius<=0: raise ValueError('INVALID_CONNECTOR_DIMENSION')
    return mf.Manifold.cylinder(high-low,radius,circular_segments=64).translate([0,0,low])

def shapes(*, radius,length,D,headed=True):
    c=.25*D/256; h=.75*D/256; t=D/256
    if length<=t+2*c: raise ValueError('INSUFFICIENT_NECK_LENGTH')
    # Interface at z=0, receiving material at z>0, peg material at z<0.
    shaft=cylinder(radius,-radius,length)
    head=cylinder(radius+c+h,length-t,length) if headed else None
    peg=checked(shaft+head) if head is not None else shaft
    throat=cylinder(radius+c,-c,length+c)
    pocket=cylinder(radius+h+2*c,length-t-c,length+c) if headed else None
    slot=checked(throat+pocket) if pocket is not None else throat
    return peg,slot

def rebuild(base, edges, parameters, *, D, config):
    solids={i:mesh_solid(m) for i,m in base.items()}; pegs={i:[] for i in base}; slots={i:[] for i in base}
    evidence=[]
    for e in edges:
        u,v,alpha,beta,r,l=map(float,parameters[e['id']])
        if not np.isfinite([u,v,alpha,beta,r,l]).all(): raise ValueError('NONFINITE_THETA')
        if r<config['min_radius_ratio']*D or l<config['min_length_ratio']*D: raise ValueError('CONNECTOR_BELOW_MINIMUM')
        receiver=e['receiver']; donor=e['donor']; frame=basis(e['inward'])
        guess=np.asarray(e['anchor'])+frame[:,0]*u+frame[:,1]*v
        import trimesh
        nearest,dist,tri=trimesh.proximity.closest_point(base[receiver],[guess])
        anchor=nearest[0]; normal=-base[receiver].face_normals[tri[0]]
        # Anchor must still face the donor and be within the original contact.
        other,dd,tt=trimesh.proximity.closest_point(base[donor],[anchor])
        if dd[0]>.005*D or np.dot(base[donor].face_normals[tt[0]],-normal)>=0:
            raise ValueError('ANCHOR_OUTSIDE_CONTACT')
        frame=basis(normal)
        rotation=Rotation.from_rotvec(frame[:,1]*beta).as_matrix()@Rotation.from_rotvec(frame[:,0]*alpha).as_matrix()
        frame=rotation@frame
        if np.dot(frame[:,2],normal)<np.cos(np.deg2rad(config['cone_deg'])): raise ValueError('AXIS_CONE_VIOLATION')
        T=np.column_stack([frame,anchor])
        peg,slot=shapes(radius=r,length=l,D=D)
        peg=checked(peg.transform(T)); slot=checked(slot.transform(T))
        # Verify material surrounding the whole blind pocket, not just its centre.
        c=config['clearance_ratio']*D; outer=cylinder(r+config['overhang_ratio']*D+3*c,c,l+2*c)
        outer=checked(outer.transform(T))
        if not checked(outer-solids[receiver]).is_empty(): raise ValueError('INSUFFICIENT_RECEIVER_MATERIAL')
        if checked(peg^solids[donor]).is_empty(): raise ValueError('PEG_NOT_ROOTED')
        for k in solids:
            if k not in (donor,receiver) and checked(peg^solids[k]).volume()>0:
                raise ValueError('THIRD_PART_INTERFERENCE')
        pegs[donor].append(peg); slots[receiver].append(slot)
        transform=np.eye(4); transform[:3]=T
        evidence.append(dict(edge=e['id'],anchor=anchor.tolist(),axis=frame[:,2].tolist(),
                             donor=donor,receiver=receiver,T=transform.tolist(),radius=r,length=l,
                             clearance=c,overhang=config['overhang_ratio']*D,
                             head_thickness=config['head_thickness_ratio']*D,headed=True))
    out={}
    for i in solids:
        solid=checked(solids[i]+union(pegs[i])) if pegs[i] else solids[i]
        if slots[i]: solid=checked(solid-union(slots[i]))
        mesh=solid_mesh(solid)
        if not mesh.is_volume or component_count(mesh)>component_count(base[i]): raise ValueError('CONNECTOR_DISCONNECTS_PART')
        out[i]=mesh
    for a,b in itertools.combinations(out,2):
        if checked(mesh_solid(out[a])^mesh_solid(out[b])).volume()>0: raise ValueError('ASSEMBLY_INTERSECTION')
    return out,evidence
