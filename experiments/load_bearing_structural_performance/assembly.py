"""Discrete-surface C3D10 parts in one ideal-bonded CalculiX system.

No OCC rebuild, whole-object union, buckling or mesh-repair fallback.
"""
from __future__ import annotations
from collections import defaultdict
import math
from pathlib import Path
import re
import time
import numpy as np
from adsl.agents.utils.io import write_json
from .analyze import parse_gmsh_inp,mesh_invalid_report,format_ids,run_ccx


def mesh_part(mesh, path, size_mm):
    import gmsh
    import trimesh
    started=time.monotonic()
    gmsh.initialize()
    try:
        gmsh.option.setNumber('General.NumThreads',1)
        gmsh.model.add(path.stem)
        surface=gmsh.model.addDiscreteEntity(2)
        gmsh.model.mesh.addNodes(2,surface,np.arange(1,len(mesh.vertices)+1),np.asarray(mesh.vertices).ravel()*.001)
        gmsh.model.mesh.addElementsByType(surface,2,np.arange(1,len(mesh.faces)+1),(np.asarray(mesh.faces)+1).ravel())
        gmsh.model.mesh.classifySurfaces(math.pi/4,True,True,math.pi)
        gmsh.model.mesh.createGeometry()
        surfaces=[tag for dim,tag in gmsh.model.getEntities(2)]
        edges={s:set(gmsh.model.getAdjacencies(2,s)[1]) for s in surfaces}
        # Separate boundary shells; inner loops remain voids instead of being filled.
        pending=set(surfaces);shells=[]
        while pending:
            group={pending.pop()}
            while True:
                found={s for s in pending if any(edges[s]&edges[t] for t in group)}
                if not found:break
                group|=found;pending-=found
            shells.append(sorted(group))
        tags,coords,_=gmsh.model.mesh.getNodes();xyz=dict(zip(tags,np.asarray(coords).reshape(-1,3)))
        loops=[]
        for group in shells:
            triangles=[]
            for s in group:
                types,_,conn=gmsh.model.mesh.getElements(2,s)
                for typ,nodes in zip(types,conn):
                    if typ!=2:raise ValueError('unexpected initial surface element type')
                    triangles.extend(np.asarray(nodes).reshape(-1,3))
            vertices=np.asarray([[xyz[n] for n in tri] for tri in triangles])
            volume=float(np.einsum('ij,ij->i',vertices[:,0],np.cross(vertices[:,1],vertices[:,2])).sum()/6)
            loops.append((volume,gmsh.model.geo.addSurfaceLoop(group)))
        outer=[loop for volume,loop in loops if volume>0]
        inner=[loop for volume,loop in loops if volume<0]
        if len(outer)!=1 or len(outer)+len(inner)!=len(loops):
            raise ValueError('ambiguous volume/cavity boundary shells; no automatic fill')
        volume=gmsh.model.geo.addVolume(outer+inner);gmsh.model.geo.synchronize()
        gmsh.model.addPhysicalGroup(3,[volume],1)
        gmsh.option.setNumber('Mesh.MeshSizeMin',size_mm*.001)
        gmsh.option.setNumber('Mesh.MeshSizeMax',size_mm*.001)
        gmsh.option.setNumber('Mesh.ElementOrder',2)
        gmsh.option.setNumber('Mesh.SecondOrderLinear',1)
        gmsh.model.mesh.generate(3)
        types,tags,_=gmsh.model.mesh.getElements(3)
        ids=[int(n) for typ,group in zip(types,tags) for n in group if typ==11]
        if any(typ!=11 for typ in types):raise ValueError('C3D10 required')
        quality=np.asarray(gmsh.model.mesh.getElementQualities(ids,'minSJ'))
        gmsh.write(str(path))
        nodes,elements=parse_gmsh_inp(path)
        metadata=dict(mesh_path=str(path),minimum_scaled_jacobian=float(quality.min()),
            invalid_jacobian_element_ids=[n for n,q in zip(ids,quality) if not np.isfinite(q) or q<=0],
            elapsed_seconds=time.monotonic()-started,element_type='C3D10',second_order_linear=1,
            mesh_size_mm=size_mm,nodes=len(nodes),elements=len(elements),gmsh_version=gmsh.__version__)
        invalid=mesh_invalid_report(nodes,elements,metadata)
        write_json(path.with_suffix('.quality.json'),dict(**metadata,invalid=invalid))
        if invalid:raise ValueError(f'MESH_INVALID: {path.stem}; see {path.with_suffix(".quality.json")}')
        return nodes,elements,metadata
    finally:gmsh.finalize()


# CalculiX C3D10 face numbering; Gmsh INP writer supplies Abaqus node order.
FACES=((0,1,2,4,5,6),(0,3,1,7,8,4),(1,3,2,8,9,5),(2,3,0,9,7,6))
def boundary_faces(nodes,elements):
    all_faces=defaultdict(list)
    for eid,conn in elements.items():
        for face,indices in enumerate(FACES,1):
            ids=[conn[i] for i in indices]
            opposite=next(n for n in conn[:4] if n not in ids[:3])
            all_faces[tuple(sorted(ids[:3]))].append(dict(element=eid,face=face,nodes=ids,opposite_node=opposite))
    return [rows[0] for rows in all_faces.values() if len(rows)==1]


def remap_parts(local,report):
    from adsl.core.assembly_topology import mm_matrix
    parts={p['id']:p for p in report['parts']};nodes={};elements={};mapping={}
    for name,(ln,le,_) in local.items():
        transform=mm_matrix(parts[name]['assembly_transform'],report['mm_per_unit']);transform[:3,3]*=.001
        nm={n:len(nodes)+i+1 for i,n in enumerate(ln)};em={e:len(elements)+i+1 for i,e in enumerate(le)}
        nodes.update({nm[n]:transform[:3,:3]@v+transform[:3,3] for n,v in ln.items()})
        mapped={em[e]:[nm[n] for n in ns] for e,ns in le.items()};elements.update(mapped)
        mapping[name]=dict(node_ids=list(nm.values()),element_ids=list(em.values()),
            faces=boundary_faces(nodes,mapped),components=parts[name].get('components',[]),
            assembly_transform_m=transform.tolist())
    return nodes,elements,mapping


def select_region(spec,nodes,mapping):
    names=[n for n,p in mapping.items() if (n==spec.get('part_id') if spec.get('part_id') else
        bool(spec.get('semantic_pattern')) and re.search(spec['semantic_pattern'],' '.join([n,*p['components']]),re.I))]
    if not names:raise ValueError('REGION_UNAVAILABLE: no part matches explicit region')
    if len(names)>1 and not spec.get('allow_multiple_parts',False):raise ValueError('REGION_AMBIGUOUS: multiple print parts')
    bounds=np.asarray(spec['bounds_mm'],dtype=float)*.001
    if bounds.shape!=(2,3) or not np.isfinite(bounds).all() or np.any(bounds[1]<=bounds[0]):raise ValueError('invalid region bounds')
    selected=[]
    for name in names:
        inv=np.linalg.inv(mapping[name]['assembly_transform_m']) if spec.get('frame')=='part_local' else np.eye(4)
        if spec.get('frame') not in ('part_local','assembly'):raise ValueError('region frame must be explicit')
        for f in mapping[name]['faces']:
            pts=np.asarray([nodes[n] for n in f['nodes'][:3]])@inv[:3,:3].T+inv[:3,3]
            # Entire face in region: avoid arbitrary centroid-based partial loads.
            if np.all(pts>=bounds[0]) and np.all(pts<=bounds[1]):
                if spec.get('normal') is not None:
                    normal=np.cross(pts[1]-pts[0],pts[2]-pts[0]);normal/=np.linalg.norm(normal)
                    opposite=inv[:3,:3]@nodes[f['opposite_node']]+inv[:3,3]
                    if normal@(opposite-pts[0])>0:normal=-normal
                    requested=np.asarray(spec['normal'],dtype=float)
                    if requested.shape!=(3,) or not np.isfinite(requested).all() or np.linalg.norm(requested)==0:
                        raise ValueError('region normal must be a finite nonzero vector')
                    if normal@(requested/np.linalg.norm(requested))<.999999:continue
                selected.append((name,f))
    if not selected:raise ValueError('REGION_UNAVAILABLE: no boundary face inside configured region')
    return selected


def tie_regions(report,nodes,mapping):
    from adsl.core.assembly_topology import mm_matrix
    result=[];used=set()
    for c in report['connections']:
        p=c['parameters'];gap=p['fit_offset_mm']*.001
        if gap<0:raise ValueError('INTERFACE_BINDING_UNVERIFIED: nominal interference unsupported for ideal tie')
        parts={r['id']:r for r in report['parts']}
        frame=mm_matrix(parts[c['slot_part']]['assembly_transform'],report['mm_per_unit'])@mm_matrix(c['slot_frame'],report['mm_per_unit'])
        frame[:3,3]*=.001;inv=np.linalg.inv(frame)
        eps=16*np.finfo(np.float32).eps*max(.001,float(np.abs(frame[:3,3]).max()),p['slot_depth_mm']*.001)
        sets={}
        for role in ('tab','slot'):
            faces=[]
            for f in mapping[c[role+'_part']]['faces']:
                pts=np.asarray([nodes[n] for n in f['nodes']])@inv[:3,:3].T+inv[:3,3]
                if role=='tab' and (pts[:,2].min() < -eps or pts[:,2].max() > p['insertion_mm']*.001+eps):continue
                if role=='slot' and (pts[:,2].max() < -eps or pts[:,2].min() > p['insertion_mm']*.001+eps):continue
                for axis,key in ((0,'width_mm'),(1,'thickness_mm')):
                    half=p[key]*.0005+(gap if role=='slot' else 0)
                    other=1-axis
                    other_half=p['thickness_mm' if axis==0 else 'width_mm']*.0005+(gap if role=='slot' else 0)
                    if role=='tab' and (pts[:,other].min() < -other_half-eps or pts[:,other].max() > other_half+eps):continue
                    if role=='slot' and (pts[:,other].max() < -other_half-eps or pts[:,other].min() > other_half+eps):continue
                    if any(np.max(np.abs(pts[:,axis]-sign*half))<=eps for sign in (-1,1)):
                        faces.append(f);break
            sets[role]=faces
        slaves=sorted({n for f in sets['tab'] for n in f['nodes']})
        if not slaves or not sets['slot']:raise ValueError(f'INTERFACE_BINDING_UNVERIFIED: {c["id"]}; no resolved mating face set')
        if used.intersection(slaves):raise ValueError('INTERFACE_BINDING_UNVERIFIED: duplicate dependent node')
        used.update(slaves)
        # No hidden 2.5%-element-size CalculiX default: fixed clearance + precision only.
        result.append(dict(connection_id=c['id'],tab_part=c['tab_part'],slot_part=c['slot_part'],
            slave_nodes=slaves,master_faces=sets['slot'],slave_faces=sets['tab'],position_tolerance_m=float(max(gap+eps,1.01e-10))))
    return result


def write_assembly_deck(path,nodes,elements,mapping,material,ties,support_specs,load_specs,include_gravity):
    lines=['*HEADING','aDSL ideal-bonded assembly; m N Pa','*NODE']
    # CalculiX free fields still have a 20-character numeric field limit.
    # Reuse the production deck's 12 significant digits, not 24-char exponents.
    lines += [f'{n}, '+', '.join(f'{v:.12g}' for v in xyz) for n,xyz in nodes.items()]
    for i,(name,part) in enumerate(mapping.items()):
        lines.append(f'*ELEMENT, TYPE=C3D10, ELSET=P{i}')
        lines += [f'{e}, '+', '.join(map(str,elements[e])) for e in part['element_ids']]
        mat=material.get('parts',{}).get(name,material)
        lines += [f'*MATERIAL, NAME=M{i}','*ELASTIC',f'{mat["youngs_modulus_pa"]:.12g}, {mat["poisson_ratio"]:.12g}',
                  '*DENSITY',f'{mat["density_kg_m3"]:.12g}',f'*SOLID SECTION, ELSET=P{i}, MATERIAL=M{i}','']
    lines += ['*NSET, NSET=NALL',*format_ids(nodes),'*ELSET, ELSET=EALL',*format_ids(elements)]
    support=[]
    for spec in support_specs:
        rows=select_region(spec,nodes,mapping);ids=sorted({n for _,f in rows for n in f['nodes']})
        dofs=spec['dofs']
        if not dofs or any(d not in (1,2,3) for d in dofs):raise ValueError('support DOFs must be 1..3')
        support += [(n,d) for n in ids for d in dofs]
    slaves={n for t in ties for n in t['slave_nodes']}
    if slaves & {n for n,d in support}:raise ValueError('INTERFACE_BINDING_UNVERIFIED: slave/support conflict')
    for i,tie in enumerate(ties):
        lines += [f'*SURFACE, NAME=SLAVE{i}, TYPE=NODE',*[str(n) for n in tie['slave_nodes']],
                  f'*SURFACE, NAME=MASTER{i}, TYPE=ELEMENT',
                  *[f'{f["element"]}, S{f["face"]}' for f in tie['master_faces']],
                  f'*TIE, NAME=T{i}, POSITION TOLERANCE={tie["position_tolerance_m"]:.12g}, ADJUST=NO',f'SLAVE{i}, MASTER{i}']
    lines+=['*BOUNDARY',*[f'{n}, {d}, {d}' for n,d in sorted(set(support))]]
    forces=defaultdict(lambda:np.zeros(3));loads=[]
    for spec in load_specs:
        rows=select_region(spec,nodes,mapping);weights=defaultdict(float)
        for _,f in rows:
            pts=np.asarray([nodes[n] for n in f['nodes'][:3]])
            area=np.linalg.norm(np.cross(pts[1]-pts[0],pts[2]-pts[0]))/2
            # Consistent uniform traction on a linear-sided quadratic triangle:
            # zero corner weight, area/3 at each mid-edge (exact resultant/centroid).
            for n in f['nodes'][3:]:weights[n]+=area/3
        force=np.asarray(spec['force_n'],dtype=float)
        if force.shape!=(3,) or not np.isfinite(force).all():raise ValueError('invalid load vector')
        total=sum(weights.values())
        for n,w in weights.items():forces[n]+=force*w/total
        centroid=sum((nodes[n]*w for n,w in weights.items()),np.zeros(3))/total
        loads.append(dict(force_n=force.tolist(),centroid_m=centroid.tolist(),area_m2=total,node_weights=dict(weights)))
    lines += ['*STEP','*STATIC']
    if include_gravity:lines+=['*DLOAD','EALL, GRAV, 9.81, 0., 0., -1.']
    lines+=['*CLOAD',*[f'{n}, {d+1}, {v:.12g}' for n,f in forces.items() for d,v in enumerate(f) if v],
        '*NODE FILE','U,RF','*EL FILE','S','*NODE PRINT, NSET=NALL','U,RF','*EL PRINT, ELSET=EALL','S','*END STEP']
    path.write_text('\n'.join(lines)+'\n')
    return dict(support_node_dofs=sorted(set(support)),loads=loads,
                nodal_forces={n:v.tolist() for n,v in forces.items()},ties=ties)


def result_fields(path):
    """Full DAT fields for local maxima and transfer checks (not model context)."""
    u,rf,stress={},{},{}
    mode=None
    for line in path.read_text().splitlines():
        if 'displacements (vx,vy,vz)' in line: mode='u';continue
        if 'forces (fx,fy,fz)' in line: mode='rf';continue
        if 'stresses (elem, integ.pnt.' in line:mode='stress';continue
        tokens=line.split()
        try:
            if mode in ('u','rf') and len(tokens)==4:
                n=int(tokens[0]);values=np.asarray([float(v.replace('D','E')) for v in tokens[1:]])
                (u if mode=='u' else rf)[n]=values
            elif mode=='stress' and len(tokens)==8:
                n=int(tokens[0]);s=np.asarray([float(v.replace('D','E')) for v in tokens[2:]])
                vm=float(np.sqrt(.5*((s[0]-s[1])**2+(s[1]-s[2])**2+(s[2]-s[0])**2)+3*np.sum(s[3:]**2)))
                stress[n]=max(stress.get(n,0),vm)
        except ValueError:mode=None
    return u,rf,stress


def transfer_check(nodes,elements,mapping,regions,material,include_gravity,u,rf):
    import trimesh
    force=sum((np.asarray(v) for v in regions['nodal_forces'].values()),np.zeros(3))
    mass=0.;body_forces=defaultdict(lambda:np.zeros(3))
    for name,p in mapping.items():
        density=material.get('parts',{}).get(name,material)['density_kg_m3']
        for e in p['element_ids']:
            corners=np.asarray([nodes[n] for n in elements[e][:4]])
            element_mass=abs(np.linalg.det(corners[1:]-corners[:1]))/6*density
            mass+=element_mass
            if include_gravity:
                for i,n in enumerate(elements[e]):body_forces[n][2]+=-9.81*element_mass*(-.05 if i<4 else .2)
    if include_gravity:force[2]-=9.81*mass
    support=regions['support_node_dofs']
    if any(n not in rf for n,d in support) or any(n not in u for n in nodes):
        raise ValueError('NUMERICAL_RESULT_UNVERIFIED: incomplete displacement/reaction output')
    reaction=np.zeros(3)
    # CalculiX RF = reactions + CLOAD + DLOAD at the node, not reactions alone.
    # Consistent body loads for linear-sided C3D10: corners -V/20, mid-edges V/5.
    for n,d in support:
        nodal=np.asarray(regions['nodal_forces'].get(n,regions['nodal_forces'].get(str(n),[0,0,0])))
        reaction[d-1]+=rf[n][d-1]-body_forces[n][d-1]-nodal[d-1]
    residual=reaction+force
    # DAT prints 7 significant digits; sum an explicit rounding bound, with
    # a solver residual floor of 1e-5 relative resultant (not a physical limit).
    rounding=sum((.5*10.**(np.floor(np.log10(max(abs(rf[n][d-1]),1e-300)))-6) for n,d in support))
    limit=max(rounding,1e-5*max(1.,np.linalg.norm(force)))
    compatibility=[]
    for tie in regions['ties']:
        # CalculiX uses triangulation to find a face, then attach_2d uses all
        # six face nodes for the quadratic MPC. Do not use linear interpolation.
        master=[f['nodes'] for f in tie['master_faces']]
        # Proximity queries use local mm, not tiny SI triangles: trimesh's
        # internal absolute tolerances must not change the nearest face.
        origin=np.asarray(nodes[master[0][0]])
        triangles=np.asarray([[(nodes[n]-origin)*1000 for n in ids[:3]] for ids in master])
        surface=trimesh.Trimesh(triangles.reshape(-1,3),np.arange(len(triangles)*3).reshape(-1,3),process=False)
        pts=np.asarray([(nodes[n]-origin)*1000 for n in tie['slave_nodes']])
        errors=[];dist=[];ambiguous=0
        numerical_mm=128*np.finfo(float).eps*max(1.,np.abs(triangles).max())
        radius=float(tie['position_tolerance_m'])*1000+numerical_mm
        tree=surface.triangles_tree
        for n,point in zip(tie['slave_nodes'],pts):
            ids=list(tree.intersection(np.r_[point-radius,point+radius]))
            if not ids:
                errors.append(float('inf'));dist.append(float('inf'));continue
            local=triangles[ids]
            closest=trimesh.triangles.closest_point(local,np.repeat(point[None,:],len(ids),axis=0))
            distances=np.linalg.norm(closest-point,axis=1)
            # At an interface corner two walls are equally near. Keep only
            # geometrically tied minima; either may be CalculiX's master face.
            chosen=np.flatnonzero(distances<=distances.min()+numerical_mm)
            bary=trimesh.triangles.points_to_barycentric(local[chosen],closest[chosen])
            candidates=[]
            for k,l in zip(chosen,bary):
                weights=np.r_[l*(2*l-1),4*l[0]*l[1],4*l[1]*l[2],4*l[2]*l[0]]
                displacement=weights@np.asarray([u[v] for v in master[ids[k]]])
                candidates.append(float(np.linalg.norm(u[n]-displacement)))
            errors.append(min(candidates));dist.append(float(distances.min())*.001)
            ambiguous+=len(chosen)>1
        max_u=max(float(np.linalg.norm(v)) for v in u.values())
        # Printed output/face projection check: report an approximate residual.
        allowed=max(1e-10,1e-4*max_u)
        compatibility.append(dict(connection_id=tie['connection_id'],slave_nodes=len(pts),
            max_projection_distance_m=max(dist),max_displacement_mismatch_m=max(errors),
            equidistant_master_nodes=ambiguous,
            displacement_tolerance_m=allowed,matched=bool(max(dist)<=float(tie['position_tolerance_m']) and max(errors)<=allowed)))
    return dict(reaction_n=reaction.tolist(),applied_force_n=force.tolist(),residual_n=residual.tolist(),
        reaction_tolerance_n=float(limit),reaction_balanced=bool(np.linalg.norm(residual)<=limit),
        interface_compatibility=compatibility,interfaces_compatible=all(r['matched'] for r in compatibility),
        numeric_basis='DAT 7-digit rounding plus relative linear solve/projection residual; not a physical acceptance threshold')


def field_report(output,nodes,elements,mapping,u,stress):
    rows=[]
    for name,p in mapping.items():
        un=max(p['node_ids'],key=lambda n:np.linalg.norm(u[n]))
        se=max(p['element_ids'],key=lambda e:stress.get(e,float('-inf')))
        rows.append(dict(part_id=name,max_displacement_mm=float(np.linalg.norm(u[un])*1000),node_id=un,
            displacement_position_mm=(nodes[un]*1000).tolist(),max_von_mises_mpa=stress[se]/1e6,element_id=se,
            stress_position_mm=(np.mean([nodes[n] for n in elements[se][:4]],axis=0)*1000).tolist()))
    write_json(output/'part_fields.json',rows)
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
    except ImportError as error:
        # Plotting cannot erase a completed numerical analysis.
        write_json(output/'plot_status.json',{'status':'UNAVAILABLE','reason':str(error)})
        return rows
    for label,positions,values in (
        ('displacement_mm',np.asarray(list(nodes.values()))*1000,np.array([np.linalg.norm(u[n])*1000 for n in nodes])),
        ('von_mises_mpa',np.asarray([np.mean([nodes[n] for n in conn[:4]],axis=0) for conn in elements.values()])*1000,
            np.array([stress[e]/1e6 for e in elements]))):
        fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(projection='3d')
        stride=max(1,len(values)//20000)
        points=ax.scatter(*positions[::stride].T,c=values[::stride],s=2,cmap='turbo')
        fig.colorbar(points,ax=ax,label=label);ax.set(xlabel='X mm',ylabel='Y mm',zlabel='Z mm',title='Ideal bonded; undeformed positions')
        fig.savefig(output/f'{label}.png',dpi=140);plt.close(fig)
    write_json(output/'plot_status.json',{'status':'COMPLETED'})
    return rows
