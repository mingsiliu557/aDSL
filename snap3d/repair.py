"""Only simple near-planar small boundary loops; no remeshing or merging."""
import numpy as np
import trimesh
from .vendor import mesh_solid

def repair(mesh, *, D, config):
    result = mesh.copy()
    record = dict(repaired=False, original_volume_mm3=float(mesh.volume) if mesh.is_volume else None,
                  opening_count=0, maximum_opening_mm=0., patch_area_mm2=0.)
    if mesh.is_watertight:
        mesh_solid(mesh)
        return result, record
    edges = mesh.edges_sorted
    keys, counts = np.unique(edges, axis=0, return_counts=True)
    if np.any(counts > 2): raise ValueError('NONMANIFOLD_BOUNDARY')
    boundary = keys[counts == 1]
    adj = {}
    for a,b in boundary:
        adj.setdefault(int(a), []).append(int(b)); adj.setdefault(int(b), []).append(int(a))
    if not adj or any(len(v)!=2 for v in adj.values()): raise ValueError('BOUNDARY_NOT_SIMPLE_LOOPS')
    loops=[]; unseen=set(adj)
    while unseen:
        start=min(unseen); loop=[start]; prev=None; cur=start
        while True:
            nxt=next(x for x in adj[cur] if x!=prev)
            if nxt==start: break
            if nxt in loop: raise ValueError('SELF_INTERSECTING_BOUNDARY')
            loop.append(nxt); prev,cur=cur,nxt
        unseen.difference_update(loop); loops.append(loop)
    faces=list(mesh.faces.copy())
    # Closed loop orientation must oppose the existing directed boundary.
    directed={tuple(e) for e in mesh.edges}
    for loop in loops:
        pts=mesh.vertices[loop]; centered=pts-pts.mean(axis=0)
        diameter=float(np.linalg.norm(pts[:,None]-pts[None,:],axis=2).max())
        _,_,vt=np.linalg.svd(centered)
        record['maximum_opening_mm']=max(record['maximum_opening_mm'],diameter)
        if diameter > config['diameter_ratio']*D: raise ValueError('OPENING_TOO_LARGE')
        if np.abs(centered@vt[-1]).max()>config['planarity_ratio']*D: raise ValueError('OPENING_NONPLANAR')
        if (loop[0],loop[1]) in directed: loop.reverse(); pts=mesh.vertices[loop]
        xy=(pts-pts.mean(axis=0))@vt[:2].T
        # Ear clipping supports non-convex loops; rejects crossing edges.
        from shapely.geometry import Polygon
        poly=Polygon(xy)
        if not poly.is_valid: raise ValueError('BOUNDARY_SELF_INTERSECTION')
        sign=1 if poly.exterior.is_ccw else -1
        indices=list(range(len(loop))); patch=[]
        def cross(a,b,c): return np.cross(b-a,c-a)
        while len(indices)>3:
            for k,b in enumerate(indices):
                a,c=indices[k-1],indices[(k+1)%len(indices)]
                if sign*cross(xy[a],xy[b],xy[c])<=0: continue
                def inside(q):
                    return all(sign*cross(xy[u],xy[v],xy[q])>=0 for u,v in [(a,b),(b,c),(c,a)])
                if any(inside(q) for q in indices if q not in (a,b,c)): continue
                patch.append([loop[a],loop[b],loop[c]]); indices.pop(k); break
            else: raise ValueError('BOUNDARY_TRIANGULATION_FAILED')
        patch.append([loop[q] for q in indices]); faces.extend(patch)
        record['patch_area_mm2']+=float(trimesh.Trimesh(mesh.vertices,patch,process=False).area)
    result=trimesh.Trimesh(mesh.vertices.copy(),faces,process=False)
    if not result.is_volume: raise ValueError('REPAIR_NOT_CLOSED_VOLUME')
    mesh_solid(result)
    # Robust triangle intersections are checked using an FCL BVH on non-adjacent
    # original faces against each patch. Touch at shared vertices is excluded.
    from trimesh.collision import CollisionManager
    old_count=len(mesh.faces)
    for face in result.faces[old_count:]:
        nonadj=mesh.faces[~np.isin(mesh.faces,face).any(axis=1)]
        if not len(nonadj): continue
        manager=CollisionManager(); manager.add_object('original',trimesh.Trimesh(mesh.vertices,nonadj,process=False))
        if manager.in_collision_single(trimesh.Trimesh(mesh.vertices,[face],process=False)):
            raise ValueError('PATCH_INTERSECTS_ORIGINAL')
    record.update(repaired=True,opening_count=len(loops))
    return result,record
