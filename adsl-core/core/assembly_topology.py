"""Final print-mesh connectivity and local TabSlot evidence, not manufacture.

No source execution, Blender/OCC, proximity welding, mesh repair or cross-part
union. Inputs/outputs are local Z-up millimetres. Native calls run only in the
existing checker subprocess boundary (see agents.assembly_topology).
"""
from __future__ import annotations

import itertools
import numpy as np
import trimesh
import manifold3d as mf

from .assembly import TabSlot, InterfaceFrame
from .export.export_assembly import mesh_solid, solid_mesh


def length_bound(mesh):
    return float(16 * np.finfo(np.float32).eps * max(1, np.abs(mesh.vertices).max()))


def checked(solid):
    if solid.status() != mf.Error.NoError:
        raise ValueError(f'Manifold query failed: {solid.status()}')
    return solid


def union_print_mesh(mesh, face_groups=None):
    """Union oriented closed shells; negative cavity shells are not solids.

    Exact export duplicates only. A cavity must have an unambiguous containing
    positive shell; ambiguous overlapping containment is deliberately unverified.
    """
    if not len(mesh.faces) or not np.isfinite(mesh.vertices).all():
        raise ValueError('empty or nonfinite mesh')
    if np.any(mesh.area_faces <= 0):
        raise ValueError('zero-area triangle in exported mesh')
    groups=face_groups or [[0,len(mesh.faces)]]
    if [i for start,end in groups for i in range(start,end)] != list(range(len(mesh.faces))):
        raise ValueError('exported face groups do not partition this mesh')
    shells=[]
    for start,end in groups:
        # Preserve known exported object boundaries, especially shared faces.
        # Legacy exports without metadata use conservative surface decomposition.
        faces=mesh.faces[start:end]
        vertices,inverse=np.unique(mesh.vertices[faces].reshape(-1,3),axis=0,return_inverse=True)
        group=trimesh.Trimesh(vertices,inverse.reshape(-1,3),process=False)
        shells.extend(group.split(only_watertight=False,repair=False))
    positive, negative = [], []
    for shell in shells:
        if not shell.is_watertight or not shell.is_winding_consistent or shell.volume == 0:
            raise ValueError('open, nonmanifold, unoriented or zero-volume shell')
        if shell.volume < 0:
            shell = shell.copy(); shell.invert()
            negative.append(mesh_solid(shell))
        else:
            positive.append(mesh_solid(shell))
    if not positive:
        raise ValueError('no positive oriented material shell')
    tol = length_bound(mesh) * mesh.area
    holes = {i: [] for i in range(len(positive))}
    for cavity in negative:
        containers = [i for i, s in enumerate(positive) if checked(cavity-s).volume() <= tol]
        if not containers:
            raise ValueError('negative cavity shell has no containing material')
        parent = min(containers, key=lambda i: positive[i].volume())
        if any(checked(positive[parent]-positive[i]).volume() > tol for i in containers):
            raise ValueError('ambiguous cavity ownership among overlapping shells')
        holes[parent].append(cavity)
    materials = []
    for i, shell in enumerate(positive):
        for cavity in holes[i]:
            shell = checked(shell-cavity)
        materials.append(shell)
    union = checked(mf.Manifold.batch_boolean(materials, mf.OpType.Add))
    # Decompose returns signed boundary shells, including negative cavity walls.
    # Count positive material islands only; do not fill the cavity in the output.
    components = [s for s in union.decompose() if s.volume() > 0]
    if not components or any(s.volume() == 0 for s in union.decompose()):
        raise ValueError('union has no reliable positive material volume')
    return union, components, length_bound(mesh)


def read_print_mesh(path, print_transform):
    mesh = trimesh.load_mesh(path, process=False)
    if not isinstance(mesh, trimesh.Trimesh):
        raise ValueError('expected one print mesh file')
    mesh.apply_transform(np.linalg.inv(np.asarray(print_transform, dtype=float)))
    return mesh


def part_measurement(mesh, part_id, face_groups=None):
    solid, components, tolerance = union_print_mesh(mesh, face_groups)
    boxes = [np.asarray(c.bounding_box()).reshape(2,3).tolist() for c in components]
    row = dict(kind='part', part_id=part_id, status='PASS' if len(components)==1 else 'FAIL',
        code='CONNECTED_PRINT_PART' if len(components)==1 else 'INTERNAL_PART_DISCONNECTED',
        component_count=len(components), component_bounds_mm=boxes,
        representative_points_mm=[np.mean(b,axis=0).tolist() for b in boxes],
        bounds_mm=solid_mesh(solid).bounds.tolist(), length_tolerance_mm=tolerance,
        nearest_components=None)
    if len(components)>1:
        # Vertex-to-triangle witnesses, not an exact triangle-triangle minimum.
        # Keep a lower AABB distance too; never present samples as exact distance.
        witnesses = []
        for i,j in itertools.combinations(range(len(components)),2):
            a,b = solid_mesh(components[i]),solid_mesh(components[j])
            lower = np.linalg.norm(np.maximum(0,np.maximum(a.bounds[0]-b.bounds[1],b.bounds[0]-a.bounds[1])))
            witnesses.append((lower,i,j))
        lower,i,j = min(witnesses)
        a,b=solid_mesh(components[i]),solid_mesh(components[j])
        try:
            points=a.vertices[::max(1,len(a.vertices)//128)][:128]
            close,dist,_=trimesh.proximity.closest_point_naive(b,points)
            k=int(np.argmin(dist))
            row['nearest_components']=dict(component_indices=[i,j],
                aabb_distance_lower_bound_mm=float(lower),sampled_distance_upper_bound_mm=float(dist[k]),
                witness_points_mm=[points[k].tolist(),close[k].tolist()],
                method='minimum AABB pair, up to 128 vertex-to-surface samples; exact nearest pair not established')
        except Exception as error:
            row['distance_unavailable']=f'{type(error).__name__}: {str(error)[:160]}'
    return row, solid


def query_solids(parameters):
    """Same box/cut-plane recipe as TabSlot.geometry; no new body CSG."""
    recipe=parameters.geometry_recipe()
    tab=mf.Manifold.cube(recipe['tab_size'],center=True).translate(recipe['tab_center'])
    for point,normal,z in recipe['planes']:
        span=recipe['cut_span']
        cutter=mf.Manifold.cube((span,span,span),center=True).translate((span/2,0,0))
        matrix=InterfaceFrame(point,normal,z).matrix()
        tab=checked(tab-cutter.transform(matrix[:3,:]))
    slot=mf.Manifold.cube(recipe['slot_size'],center=True).translate(recipe['slot_center'])
    return checked(tab),checked(slot)


def mm_matrix(value, mm_per_unit):
    matrix=np.array(value,dtype=float,copy=True)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3],[0,0,0,1]):
        raise ValueError('invalid rigid transform')
    if not np.allclose(matrix[:3,:3].T@matrix[:3,:3],np.eye(3),atol=1e-8,rtol=0) or np.linalg.det(matrix[:3,:3])<0:
        raise ValueError('non-rigid or reflected assembly transform')
    matrix[:3,3]*=mm_per_unit
    return matrix


def interface_measurement(connection, parts, solids, mm_per_unit):
    """Measure actual meshes in receiver interface coordinates.

    Local wall bands are precision-sized probes, not a contact-rate criterion.
    Open/through slots need not have a floor or four walls. A PASS establishes
    occupancy, a local receiving boundary and placement, not mechanical capture.
    """
    c=connection; p=TabSlot(**c['parameters'])
    expected_tab,slot=query_solids(p)
    tab_id,slot_id=c['tab_part'],c['slot_part']
    slot_world=mm_matrix(parts[slot_id]['assembly_transform'],mm_per_unit) @ mm_matrix(c['slot_frame'],mm_per_unit)
    tab_world=mm_matrix(parts[tab_id]['assembly_transform'],mm_per_unit) @ mm_matrix(c['tab_frame'],mm_per_unit)
    local=np.linalg.inv(slot_world)
    actual={name:checked(solids[name].transform((local@mm_matrix(parts[name]['assembly_transform'],mm_per_unit))[:3,:]))
            for name in (tab_id,slot_id)}
    tab,receiver=actual[tab_id],actual[slot_id]
    # Source float32 bound propagated into interface-local coordinates.
    eps=max(length_bound(solid_mesh(s)) for s in (*actual.values(),expected_tab,slot))
    eps=max(eps,16*np.finfo(np.float32).eps*max(1,np.abs(slot_world[:3,3]).max(),np.abs(tab_world[:3,3]).max()))
    volume_tol=eps*max(solid_mesh(slot).area,solid_mesh(expected_tab).area)
    w,t,depth=p.width_mm+2*p.fit_offset_mm,p.thickness_mm+2*p.fit_offset_mm,p.slot_depth_mm
    cavity=mf.Manifold.cube((w,t,depth),center=True).translate((0,0,depth/2))
    missing=checked(expected_tab-tab).volume()
    occupied=checked(receiver^cavity).volume()
    overlap_solid=checked(tab^receiver^(slot+expected_tab))
    overlap=overlap_solid.volume()
    unexpected_overlap=checked(overlap_solid-expected_tab).volume() if p.fit_offset_mm<0 else overlap
    inserted=checked(tab^cavity)
    bounds=np.asarray(inserted.bounding_box()).reshape(2,3) if not inserted.is_empty() else None
    interval=bounds[:,2].tolist() if bounds is not None else None
    offset=float(np.linalg.norm(tab_world[:3,3]-slot_world[:3,3]))
    angle=float(np.arccos(np.clip((np.trace(tab_world[:3,:3].T@slot_world[:3,:3])-1)/2,-1,1)))
    row={k:c[k] for k in ('id','tab_part','slot_part','tab_port','slot_port','parameter_name','parameters')}
    row.update(kind='interface',connection_id=c['id'],status='PASS',code='INTERFACE_GEOMETRY_PAIRED',
        bounds_mm=solid_mesh(slot).bounds.tolist(),frame='slot_interface',unit='mm',
        frame_offset_mm=offset,frame_angle_radians=angle,missing_tab_mm3=float(missing),
        occupied_cavity_mm3=float(occupied),local_interference_mm3=float(overlap),
        undeclared_local_interference_mm3=float(unexpected_overlap),
        effective_insertion_interval_mm=interval,expected_insertion_mm=p.insertion_mm,
        length_tolerance_mm=eps,volume_tolerance_mm3=volume_tol,
        fit_kind='nominal_interference' if p.fit_offset_mm<0 else 'clearance',
        numeric_basis='16 * float32 epsilon * coordinate scale; query surface area * length bound',
        local_wall_evidence=[],root_connection='NOT_VERIFIED',failures=[])
    def fail(code): row['failures'].append(code)
    if offset>eps or angle*max(p.width_mm,p.thickness_mm,p.insertion_mm)>eps:
        fail('INTERFACE_MISREGISTERED')
    if missing>volume_tol: fail('TAB_MISSING_OR_MISPLACED')
    if occupied>volume_tol: fail('SLOT_BLOCKED_BY_MATERIAL')
    if interval is None or interval[1]-max(0,interval[0])<=eps: fail('NO_EFFECTIVE_INSERTION')
    if unexpected_overlap>volume_tol: fail('INTERFACE_LOCAL_INTERFERENCE')
    # Material close to the declared lateral cavity boundary is required. Far
    # receiver surfaces cannot turn a slot floating in empty space into PASS.
    band=4*eps
    for axis,half,other in ((0,w/2,t),(1,t/2,w)):
        for sign in (-1,1):
            size=[other,other,min(depth,p.insertion_mm)]; size[axis]=band; size[1-axis]=other
            center=[0.,0.,size[2]/2]; center[axis]=sign*(half+band/2)
            probe=mf.Manifold.cube(size,center=True).translate(center)
            hit=checked(receiver^probe).volume()
            if hit>eps*solid_mesh(probe).area:
                row['local_wall_evidence'].append(dict(axis=axis,side=sign,band_mm=band,
                    intersection_mm3=float(hit),equivalent_band_area_mm2=float(hit/band),
                    uncertainty_mm3=float(eps*solid_mesh(probe).area),
                    note='local precision-band proximity above representation bound, NOT exact contact area'))
    material_components=[s for s in tab.decompose() if s.volume()>0]
    if len(material_components)==1 and checked(tab-expected_tab).volume()>volume_tol and missing<=volume_tol:
        row['root_connection']='CONNECTED_TO_SAME_MATERIAL_COMPONENT'
    if row['failures']:
        row.update(status='FAIL',code=row['failures'][0])
    elif not row['local_wall_evidence'] or row['root_connection']=='NOT_VERIFIED':
        row.update(status='INDETERMINATE',code='INTERFACE_EVIDENCE_INSUFFICIENT')
    return row
