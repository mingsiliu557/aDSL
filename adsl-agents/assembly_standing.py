"""Self-weight contact observation of independent material components, without welding."""
from __future__ import annotations
from dataclasses import asdict
import importlib.metadata
import json
import math
import time
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np

from .assembly_physics import cli, checker_spec as _spec, load_parts, finding
from .models import CheckerResult
from .utils.io import write_json

NAME='assembly_standing'
STANDING_PROTOCOL = 'material_component_standing_v1'
def checker_spec(timeout_seconds=900,**kwargs): return _spec(NAME,timeout_seconds,**kwargs)


def with_protocol(result):
    """Attach explicit unavailable observations to early/transport failures too."""
    result.assumptions['standing_protocol'] = STANDING_PROTOCOL
    result.assumptions['interface_observation_limit'] = (
        'Exit uses declared TabSlot query geometry at simulated relative poses; '
        'it does not prove that an actual tab exists. Topology is assessed separately.')
    result.metrics.setdefault('standing_observation', dict(status='UNAVAILABLE',
        observed_tipping=None, observed_interface_exit=None, peak_tilt_deg=None,
        final_tilt_deg=None, duration_s=None, component_tilt_verified=False,
        observed_component_tipping=None, max_component_tilt_deg=None))
    for name,value in (('component_tilt_verified',False),('observed_component_tipping',None),
                       ('max_component_tilt_deg',None)):
        result.metrics['standing_observation'].setdefault(name,value)
    result.metrics.setdefault('contact_diagnostic', dict(status='UNAVAILABLE',
        code='CONTACT_DIAGNOSTIC_UNAVAILABLE', maximum_penetration_mm=None,
        reference_bound_mm=None, affects_standing_verdict=False))
    return result


def _validate_physical_inputs(report, meshes):
    """Validate model construction/observation inputs, not interface geometry."""
    from adsl.core.assembly import TabSlot
    from adsl.core.assembly_topology import mm_matrix
    parts = report['parts']
    names = [p['id'] for p in parts]
    expected = [p['id'] for p in report.get('part_declarations', parts)]
    if (len(names) != len(set(names)) or len(expected) != len(set(expected))
            or set(names) != set(expected) or set(names) != set(meshes)
            or report['root_id'] not in meshes):
        raise ValueError('INVALID_PHYSICAL_INPUT: incomplete or ambiguous rigid bodies')
    scale = float(report['mm_per_unit'])
    if not math.isfinite(scale) or scale <= 0:
        raise ValueError('INVALID_PHYSICAL_INPUT: invalid length scale')
    def frame(value):
        try:
            return mm_matrix(value, scale)  # Existing rigid-frame contract.
        except ValueError as error:
            raise ValueError(f'INVALID_PHYSICAL_INPUT: {error}') from error
    for part in parts:
        frame(part['assembly_transform'])
    connection_ids = set()
    for c in report['connections']:
        if (c['id'] in connection_ids or c['tab_part'] == c['slot_part']
                or c['tab_part'] not in meshes or c['slot_part'] not in meshes):
            raise ValueError('INVALID_PHYSICAL_INPUT: ambiguous connection observation')
        connection_ids.add(c['id'])
        try:
            TabSlot(**c['parameters'])  # Existing finite/dimension constraints only.
        except ValueError as error:
            raise ValueError(f'INVALID_PHYSICAL_INPUT: {error}') from error
        frame(c['tab_frame'])
        frame(c['slot_frame'])


def _component_triangle_digest(mesh):
    """Exact-coordinate tie breaker independent of face and vertex numbering."""
    import hashlib
    triangles = sorted(tuple(sorted(tuple(float(v) for v in point) for point in triangle))
                       for triangle in np.asarray(mesh.triangles, dtype=np.float64))
    return hashlib.sha256(np.asarray(triangles, dtype='<f8').tobytes()).hexdigest()


def _prepare_simulation_bodies(report, meshes):
    """Keep each local-mm material island as an independent simulation body."""
    import manifold3d as mf
    from adsl.core.assembly import TabSlot
    from adsl.core.assembly_topology import (checked, length_bound, material_components,
        mesh_solid, mm_matrix, query_solids, solid_mesh)
    _validate_physical_inputs(report, meshes)
    parts = {p['id']:p for p in report['parts']}
    reserved = set(parts)
    bodies, part_to_components, components_by_part, part_solids = {}, {}, {}, {}
    for part_id, part in parts.items():
        solid = checked(mesh_solid(meshes[part_id]))
        part_solids[part_id] = solid
        values = []
        for component in material_components(solid):
            mesh = solid_mesh(component)
            key = (tuple(component.bounding_box()), float(component.volume()),
                   _component_triangle_digest(mesh))
            values.append((key, component, mesh))
        values.sort(key=lambda row:row[0])
        ids, component_rows = [], []
        for index, (key, component, mesh) in enumerate(values):
            if len(values) == 1:
                component_id = part_id
                # Preserve the original one-body XML vertices and inertia exactly.
                mesh = meshes[part_id]
            else:
                base = f'{part_id}__material_{index:04d}'
                component_id, suffix = base, 0
                while component_id in reserved:
                    suffix += 1
                    component_id = f'{base}_{suffix}'
                reserved.add(component_id)
            ids.append(component_id)
            component_rows.append((component_id, component))
            bodies[component_id] = dict(part_id=part_id, mesh=mesh,
                assembly_transform_mm=mm_matrix(part['assembly_transform'], report['mm_per_unit']),
                volume_mm3=float(component.volume()), component_index=index,
                triangle_coordinates_sha256=key[2])
        part_to_components[part_id] = ids
        components_by_part[part_id] = component_rows
    root_part_id = report['root_id']
    # max returns the first item on a tie: the fixed component order above.
    root_component_id = max(part_to_components[root_part_id],
                            key=lambda component_id:bodies[component_id]['volume_mm3'])
    world_bounds = []
    for body in bodies.values():
        matrix, mesh = body['assembly_transform_mm'], body['mesh']
        vertices = mesh.vertices @ matrix[:3,:3].T + matrix[:3,3]
        world_bounds.append(np.array([vertices.min(axis=0), vertices.max(axis=0)]))
    bounds = np.array([np.min([b[0] for b in world_bounds], axis=0),
                       np.max([b[1] for b in world_bounds], axis=0)])
    diagonal = float(np.linalg.norm(bounds[1]-bounds[0]))
    if not math.isfinite(diagonal) or diagonal <= 0:
        raise ValueError('INVALID_PHYSICAL_INPUT: nonpositive assembled body scale')
    interfaces = {}
    for connection in report['connections']:
        tab_id, slot_id = connection['tab_part'], connection['slot_part']
        candidates = {}
        evidence = {}
        # Existing singleton tracking requires no additional Topology gate.
        for role, part_id in (('tab', tab_id), ('slot', slot_id)):
            if len(part_to_components[part_id]) == 1:
                candidates[role] = list(part_to_components[part_id])
                evidence[role] = dict(method='single_material_component',
                    candidate_component_ids=list(candidates[role]))
        query_failed = False
        if len(candidates) != 2:
            try:
                p = TabSlot(**connection['parameters'])
                expected_tab, slot = query_solids(p)
                tab_frame = mm_matrix(connection['tab_frame'], report['mm_per_unit'])
                slot_frame = mm_matrix(connection['slot_frame'], report['mm_per_unit'])
                tab_world = mm_matrix(parts[tab_id]['assembly_transform'], report['mm_per_unit']) @ tab_frame
                slot_world = mm_matrix(parts[slot_id]['assembly_transform'], report['mm_per_unit']) @ slot_frame
                local = np.linalg.inv(slot_world)
                actual = [checked(part_solids[name].transform((local @ mm_matrix(
                    parts[name]['assembly_transform'], report['mm_per_unit']))[:3,:]))
                          for name in (tab_id, slot_id)]
                eps = max(length_bound(solid_mesh(s)) for s in (*actual, expected_tab, slot))
                eps = max(eps, 16*np.finfo(np.float32).eps*max(
                    1, np.abs(slot_world[:3,3]).max(), np.abs(tab_world[:3,3]).max()))
                volume_tol = eps*max(solid_mesh(slot).area, solid_mesh(expected_tab).area)
                if 'tab' not in candidates:
                    query = checked(expected_tab.transform(tab_frame[:3,:]))
                    hits = []
                    for component_id, component in components_by_part[tab_id]:
                        hit = float(checked(component ^ query).volume())
                        hits.append(dict(component_id=component_id, intersection_mm3=hit,
                                         hit=bool(hit > volume_tol)))
                    candidates['tab'] = [row['component_id'] for row in hits if row['hit']]
                    evidence['tab'] = dict(method='tab_query_material_intersection',
                        frame='part_local', unit='mm', length_tolerance_mm=eps,
                        volume_tolerance_mm3=volume_tol, hits=hits,
                        candidate_component_ids=list(candidates['tab']))
                if 'slot' not in candidates:
                    w = p.width_mm+2*p.fit_offset_mm
                    t = p.thickness_mm+2*p.fit_offset_mm
                    band, probes = 4*eps, []
                    for axis, half, other in ((0,w/2,t), (1,t/2,w)):
                        for sign in (-1,1):
                            size = [other,other,min(p.slot_depth_mm,p.insertion_mm)]
                            size[axis], size[1-axis] = band, other
                            center = [0.,0.,size[2]/2]
                            center[axis] = sign*(half+band/2)
                            probe = mf.Manifold.cube(size,center=True).translate(center)
                            threshold = float(eps*solid_mesh(probe).area)
                            probes.append((axis, sign, checked(probe.transform(slot_frame[:3,:])), threshold))
                    hits = []
                    for component_id, component in components_by_part[slot_id]:
                        walls = []
                        for axis, sign, probe, threshold in probes:
                            hit = float(checked(component ^ probe).volume())
                            walls.append(dict(axis=axis, side=sign, intersection_mm3=hit,
                                uncertainty_mm3=threshold, hit=bool(hit > threshold)))
                        hits.append(dict(component_id=component_id, hit=any(w['hit'] for w in walls),
                                         wall_probes=walls))
                    # A component that meets several wall probes is still one owner.
                    candidates['slot'] = [row['component_id'] for row in hits if row['hit']]
                    evidence['slot'] = dict(method='four_lateral_outside_precision_bands',
                        frame='part_local', unit='mm', length_tolerance_mm=eps, band_mm=band,
                        hits=hits, candidate_component_ids=list(candidates['slot']))
            except (ValueError, RuntimeError, np.linalg.LinAlgError) as error:
                # Material/physical validation stays outside this observation boundary.
                # Keep completed endpoint evidence; an unresolved interface must not
                # prevent independent bodies or other interfaces from being observed.
                query_failed = True
                for role in ('tab','slot'):
                    if role not in candidates:
                        candidates[role] = []
                        evidence[role] = dict(method='material_query_unavailable',status='UNAVAILABLE',
                            error_type=type(error).__name__,reason=str(error)[:240],
                            candidate_component_ids=[])
        counts = [len(candidates[role]) for role in ('tab','slot')]
        status = ('UNAVAILABLE' if query_failed else 'AMBIGUOUS' if any(n > 1 for n in counts) else
                  'UNAVAILABLE' if any(n == 0 for n in counts) else 'BOUND')
        interfaces[connection['id']] = dict(
            tab_component_id=candidates['tab'][0] if len(candidates['tab']) == 1 else None,
            slot_component_id=candidates['slot'][0] if len(candidates['slot']) == 1 else None,
            tab_part_id=tab_id, slot_part_id=slot_id, status=status, ownership_evidence=evidence)
    return dict(bodies=bodies, part_to_components=part_to_components,
        root_part_id=root_part_id, root_component_id=root_component_id,
        root_selection='maximum net material volume in original root part; deterministic component order on ties',
        interfaces=interfaces, D_mm=diagonal, assembly_bounds_mm=bounds.tolist())


def _body_mapping(body_layout):
    """JSON artifact for body identity, root choice and interface ownership."""
    bodies = {}
    for component_id, body in body_layout['bodies'].items():
        bodies[component_id] = {key:(value.tolist() if isinstance(value,np.ndarray) else value)
            for key,value in body.items() if key != 'mesh'}
    return {key:(bodies if key == 'bodies' else value) for key,value in body_layout.items()}


def collision_proxies(mesh, config):
    import coacd
    import trimesh
    coacd.set_log_level('warn')
    # CoACD real_metric documents metres; keep its native numerical scale.
    params=dict(config)
    forbidden={'preprocess_mode','real_metric','extrude','decimate','apx_mode'} & params.keys()
    if forbidden: raise ValueError('CoACD preprocessing/repair and approximation mode are frozen by adapter')
    params['threshold']=params.pop('threshold_mm')*.001
    values=coacd.run_coacd(coacd.Mesh(np.asarray(mesh.vertices)*.001,np.asarray(mesh.faces)),
        preprocess_mode='off',real_metric=True,extrude=False,decimate=False,apx_mode='ch',**params)
    proxies=[trimesh.Trimesh(np.asarray(v)*1000,f,process=False) for v,f in values]
    if not proxies or any(not p.is_volume for p in proxies):
        raise ValueError('COLLISION_PROXY_UNVERIFIED: non-volume convex proxy')
    return proxies


def verify_proxies(mesh, proxies, clearance_mm):
    """Deterministic surface samples + symmetric-difference volume, not a proof."""
    import trimesh
    import manifold3d as mf
    from adsl.core.assembly_topology import mesh_solid, solid_mesh, checked, length_bound
    exact=mesh_solid(mesh)
    approx=checked(mf.Manifold.batch_boolean([mesh_solid(p) for p in proxies],mf.OpType.Add))
    union=solid_mesh(approx)
    distances=[]
    for a,b in ((mesh,union),(union,mesh)):
        pts=np.vstack((a.vertices,a.triangles_center))
        for chunk in np.array_split(pts,max(1,math.ceil(len(pts)/2048))):
            distances.extend(trimesh.proximity.closest_point(b,chunk)[1])
    error=float(max(distances,default=float('inf')))
    numeric=max(length_bound(mesh),length_bound(union))
    # For a slot this is a quarter of the fixed clearance, not a tunable per-case gate.
    limit=max(numeric,clearance_mm/4) if clearance_mm is not None else numeric
    difference=float(checked(exact-approx).volume()+checked(approx-exact).volume())
    return dict(accepted=bool(error<=limit and difference<=limit*(mesh.area+union.area)),
        sampled_boundary_error_mm=error,error_limit_mm=limit,symmetric_difference_mm3=difference,
        sample_method='all vertices and face centres, bidirectional; bounded-volume cross-check; not exact Hausdorff',
        convex_count=len(proxies)),approx


def flex_contact_settings(report, config):
    """Explicit shell thickness; never consume a declared positive fit clearance.

    Both mating surfaces have radius r. Use r = gap/20 (at most 0.01 mm)
    unless explicitly configured, with zero margin/gap. No geometry changes.
    Zero/negative clearance cannot be represented without shell interference.
    """
    clearances=[float(c['parameters']['fit_offset_mm']) for c in report['connections']]
    if clearances and min(clearances)<=0:
        raise ValueError('RIGID_FLEX_CONTACT_UNVERIFIED: positive clearance required for finite shell radius')
    options=config.get('rigid_flex',{})
    radius=float(options.get('radius_mm',min(.01,min(clearances)/20) if clearances else .01))
    margin=float(options.get('margin_mm',0.))
    if not math.isfinite(radius) or radius<=0 or not math.isfinite(margin) or margin!=0:
        raise ValueError('RIGID_FLEX_CONTACT_UNVERIFIED: finite positive radius and zero margin required')
    if clearances and 2*radius>=min(clearances):
        raise ValueError('RIGID_FLEX_CONTACT_UNVERIFIED: paired shell radii consume fit clearance')
    return dict(radius_mm=radius,margin_mm=margin,gap_mm=0.,
        paired_shell_thickness_mm=2*radius,
        minimum_fit_clearance_mm=min(clearances) if clearances else None,
        remaining_minimum_clearance_mm=min(clearances)-2*radius if clearances else None,
        choice='min(0.01 mm, minimum positive single-sided fit / 20), or explicit radius_mm',
        representation='rigid dim=2 surface flex; not volumetric FEM; no remeshing')


def _legacy_flex_contact_options():
    """Keep old collision defaults explicit without emitting a removed option."""
    try:
        import mujoco
    except ModuleNotFoundError as error:
        if error.name != 'mujoco':
            raise
        # Building diagnostic XML alone does not require the optional solver.
        return {'internal':'false'}
    # MuJoCo removed both this model field and its MJCF attribute together.
    return {'internal':'false'} if hasattr(mujoco.MjModel,'flex_internal') else {}


def xml_model(report,meshes,proxies,config,density, *, body_layout):
    from adsl.core.assembly_topology import mm_matrix
    from scipy.spatial.transform import Rotation
    root=ET.Element('mujoco',model='independent_assembly_self_weight')
    ET.SubElement(root,'compiler',angle='radian')
    ET.SubElement(root,'option',timestep=str(config['timestep_seconds']),gravity='0 0 -9.81',integrator='implicitfast')
    default=ET.SubElement(root,'default')
    ET.SubElement(default,'geom',friction=' '.join(map(str,config['friction'])),margin='0',gap='0')
    assets=ET.SubElement(root,'asset');world=ET.SubElement(root,'worldbody')
    ET.SubElement(world,'geom',name='floor',type='plane',size='10 10 .1',rgba='.8 .8 .8 1')
    rigid_flex=config.get('collision_backend','coacd')=='rigid_flex'
    contact=flex_contact_settings(report,config) if rigid_flex else None
    legacy_contact=_legacy_flex_contact_options() if rigid_flex else {}
    deformable=ET.SubElement(root,'deformable') if rigid_flex else None
    bodies=body_layout['bodies']
    transforms={n:np.asarray(b['assembly_transform_mm'],dtype=float) for n,b in bodies.items()}
    lowest=min(float((b['mesh'].vertices@transforms[n][:3,:3].T+transforms[n][:3,3])[:,2].min()) for n,b in bodies.items())
    def values(a): return ' '.join(f'{float(v):.17g}' for v in np.asarray(a).ravel())
    for i,(name,body_spec) in enumerate(bodies.items()):
        mesh=body_spec['mesh']
        matrix=transforms[name].copy();matrix[2,3]-=lowest;matrix[:3,3]*=.001
        q=Rotation.from_matrix(matrix[:3,:3]).as_quat()[[3,0,1,2]]
        body=ET.SubElement(world,'body',name=name,pos=values(matrix[:3,3]),quat=values(q))
        ET.SubElement(body,'freejoint',name=f'free_{name}')
        # Per-part material inertia, NEVER the proxy-volume sum. Inter-part
        # interference is reported separately by Topology.
        physical=mesh.copy();physical.apply_scale(.001);physical.density=density
        inertia=physical.moment_inertia
        if (not math.isfinite(float(physical.mass)) or physical.mass <= 0
                or not np.isfinite(physical.center_mass).all()
                or not np.isfinite(inertia).all()
                or np.any(np.linalg.eigvalsh(inertia) <= 0)):
            raise ValueError(f'INVALID_PHYSICAL_INPUT: invalid mass/inertia for {name}')
        ET.SubElement(body,'inertial',pos=values(physical.center_mass),mass=str(physical.mass),
            fullinertia=values([inertia[0,0],inertia[1,1],inertia[2,2],inertia[0,1],inertia[0,2],inertia[1,2]]))
        if rigid_flex:
            # Body-local vertices remain float64 text. Display geoms cannot
            # collide: ordinary MuJoCo mesh collisions would convexify slots.
            pid=f'display_{i}'
            ET.SubElement(assets,'mesh',name=pid,vertex=values(mesh.vertices*.001),
                face=' '.join(map(str,np.asarray(mesh.faces).ravel())))
            ET.SubElement(body,'geom',type='mesh',mesh=pid,contype='0',conaffinity='0',
                density='0',rgba=values([.25+.1*(i%4),.45,.7,1]))
            flex=ET.SubElement(deformable,'flex',name=f'contact_{i}',body=name,dim='2',
                radius=str(contact['radius_mm']*.001),vertex=values(mesh.vertices*.001),
                element=' '.join(map(str,np.asarray(mesh.faces).ravel())),rgba='0 0 0 0')
            ET.SubElement(flex,'contact',contype='1',conaffinity='1',condim='3',
                **legacy_contact,selfcollide='none',margin='0',gap='0',
                friction=values(config['friction']))
            continue
        for j,proxy in enumerate(proxies[name]):
            pid=f'proxy_{i}_{j}'
            ET.SubElement(assets,'mesh',name=pid,vertex=values(proxy.vertices*.001),
                face=' '.join(map(str,np.asarray(proxy.faces).ravel())))
            ET.SubElement(body,'geom',type='mesh',mesh=pid,rgba=values([.25+.1*(i%4),.45,.7,1]))
    return ET.tostring(root,encoding='unicode')


def _initial_tipping_reference(model, data, *, part_body_ids, root_part_id,
                               floor_geom_id, flex_part_ids, component_to_part=None,
                               root_component_id=None):
    """Copy the initial solver state without evaluating geometry or advancing it."""
    reference = dict(root_part_id=root_part_id, initial_root_rotation=None,
        gravity_m_s2=None, parts=[], floor_contacts_world_m=[], reference_error=None)
    try:
        root_key=root_component_id or root_part_id
        component_to_part=component_to_part or {n:n for n in part_body_ids}
        if (not part_body_ids or root_key not in part_body_ids
                or len(set(part_body_ids.values())) != len(part_body_ids)
                or any(i <= 0 for i in part_body_ids.values())):
            raise ValueError('Initial print-body IDs were incomplete or ambiguous.')
        gravity = np.asarray(model.opt.gravity, dtype=float)
        if (gravity.shape != (3,) or not np.isfinite(gravity).all()
                or np.any(gravity[:2] != 0) or gravity[2] >= 0):
            raise ValueError('Initial gravity was not finite, vertical and downward.')
        reference['gravity_m_s2'] = float(-gravity[2])
        rotation = np.asarray(data.xmat[part_body_ids[root_key]], dtype=float).reshape(3, 3).copy()
        if not np.isfinite(rotation).all():
            raise ValueError('Initial root rotation was not finite.')
        reference['initial_root_rotation'] = rotation
        aggregated={}
        for component_id, body_id in part_body_ids.items():
            part_id=component_to_part[component_id]
            mass = float(model.body_mass[body_id])
            com = np.asarray(data.xipos[body_id], dtype=float).copy()
            if not isinstance(part_id, str) or not part_id or not math.isfinite(mass) or mass <= 0:
                raise ValueError('Initial print-body identity or mass was invalid.')
            if com.shape != (3,) or not np.isfinite(com).all():
                raise ValueError(f'Initial world centre of mass was unavailable for {part_id}.')
            entry=aggregated.setdefault(part_id, dict(mass=0., weighted_com=np.zeros(3),count=0,first_com=com))
            entry['mass']+=mass; entry['weighted_com']+=mass*com; entry['count']+=1
        reference['parts']=[dict(part_id=n, mass_kg=r['mass'],
            com_world_m=r['first_com'] if r['count']==1 else r['weighted_com']/r['mass']) for n,r in aggregated.items()]
        if floor_geom_id < 0 or floor_geom_id >= len(model.geom_bodyid):
            raise ValueError('The floor geom ID was unavailable.')
        body_ids = set(part_body_ids.values())
        for contact in data.contact:
            if contact.efc_address < 0:
                continue
            geoms, flexes = contact.geom, contact.flex
            for side in (0, 1):
                if int(geoms[side]) != floor_geom_id or int(flexes[side]) >= 0:
                    continue
                other = 1 - side
                flex_id, geom_id = int(flexes[other]), int(geoms[other])
                is_part = (flex_part_ids.get(flex_id) in part_body_ids if flex_id >= 0 else
                    0 <= geom_id < len(model.geom_bodyid)
                    and int(model.geom_bodyid[geom_id]) in body_ids)
                if is_part:
                    point = np.asarray(contact.pos, dtype=float).copy()
                    if point.shape != (3,) or not np.isfinite(point).all():
                        raise ValueError('An active initial floor contact was not finite.')
                    reference['floor_contacts_world_m'].append(point)
                    break
        if not reference['floor_contacts_world_m']:
            raise ValueError('No active initial floor contact was available.')
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as error:
        # Only extraction of the new optional diagnostic is recoverable here.
        reference['reference_error'] = str(error)[:240]
    return reference


def _localize_tipping(*, reference, first_tipping, exits):
    """Initial gravity-moment ranking in the first observed tipping direction.

    This is a diagnostic of the root-tilt event, not a causal or stability proof.
    The full output contains only strict JSON values; no state is mutated.
    """
    result = dict(method='initial_directional_gravity_moment_v1', status='FALLBACK',
        reason_code='REFERENCE_UNAVAILABLE', reason=None, causal_claim=False,
        root_part_id=reference.get('root_part_id'), first_tipping_time_s=None,
        direction_xy=None, support_boundary_m=None, net_moment_nm=None,
        positive_moment_sum_nm=None, candidates=[], part_contributions=[])
    try:
        if not isinstance(result['root_part_id'], str) or not result['root_part_id']:
            result['root_part_id'] = None
            raise ValueError('The observed root part ID was unavailable.')
        if first_tipping is None:
            raise ValueError('The first tipping sample was unavailable.')
        tip_time = float(first_tipping['time_seconds'])
        if not math.isfinite(tip_time) or tip_time < 0:
            raise ValueError('The first tipping sample time was invalid.')
        result['first_tipping_time_s'] = tip_time
        exit_times = [(float(t), str(name)) for name, t in exits.items()]
        if any(not math.isfinite(t) or t < 0 for t, _ in exit_times):
            raise ValueError('An interface exit time was invalid.')
        if exit_times and min(exit_times)[0] <= tip_time:
            exit_time, name = min(exit_times)
            result.update(reason_code='INTERFACE_EXIT_FIRST',
                reason=f'Interface {name} exited at {exit_time:g} s; first tipping sample is {tip_time:g} s.')
            return result
        if reference.get('reference_error'):
            raise ValueError(str(reference['reference_error'])[:240])
        initial = np.asarray(reference['initial_root_rotation'], dtype=float)
        tipped = np.asarray(first_tipping['root_rotation'], dtype=float)
        if (initial.shape != (3, 3) or tipped.shape != (3, 3)
                or not np.isfinite(initial).all() or not np.isfinite(tipped).all()):
            raise ValueError('Initial or first-tipping root rotation was unavailable.')
        horizontal = (tipped @ initial.T)[:2, 2]
        norm = float(np.linalg.norm(horizontal))
        if not math.isfinite(norm) or norm <= np.finfo(float).eps:
            raise ValueError('The observed tipping direction could not be normalized.')
        direction = horizontal / norm
        contacts = np.asarray(reference['floor_contacts_world_m'], dtype=float)
        if contacts.ndim != 2 or contacts.shape[1:] != (3,) or not len(contacts) or not np.isfinite(contacts).all():
            raise ValueError('No finite initial floor contacts were available.')
        boundary = float(np.max(contacts[:, :2] @ direction))
        gravity = float(reference['gravity_m_s2'])
        if not math.isfinite(boundary) or not math.isfinite(gravity) or gravity <= 0:
            raise ValueError('Initial gravity or support boundary was invalid.')
        parts = reference['parts']
        names = [p['part_id'] for p in parts]
        if (not parts or any(not isinstance(n, str) or not n for n in names)
                or len(set(names)) != len(names) or result['root_part_id'] not in names):
            raise ValueError('Initial print-body contributions were incomplete or ambiguous.')
        contributions = []
        for part in parts:
            mass = float(part['mass_kg'])
            com = np.asarray(part['com_world_m'], dtype=float)
            if not math.isfinite(mass) or mass <= 0 or com.shape != (3,) or not np.isfinite(com).all():
                raise ValueError('An initial mass or world centre of mass was invalid.')
            lever = float(direction @ com[:2] - boundary)
            moment = mass * gravity * lever
            if not math.isfinite(lever) or not math.isfinite(moment):
                raise ValueError('An initial gravity-moment contribution was not finite.')
            contributions.append(dict(part_id=part['part_id'], mass_kg=mass,
                com_world_m=com.tolist(), lever_m=lever, moment_nm=moment))
        positive = sorted((p for p in contributions if p['moment_nm'] > 0),
                          key=lambda p: (-p['moment_nm'], p['part_id']))
        net = math.fsum(p['moment_nm'] for p in contributions)
        positive_sum = math.fsum(p['moment_nm'] for p in positive)
        result.update(direction_xy=direction.tolist(), support_boundary_m=boundary,
            net_moment_nm=net, positive_moment_sum_nm=positive_sum, part_contributions=contributions)
        if not positive:
            result.update(reason_code='NO_POSITIVE_CONTRIBUTION',
                reason='No positive gravity-moment contribution was found in the observed direction.')
            return result
        result.update(status='RANKED', reason_code=None, reason=None,
            candidates=[dict(part_id=p['part_id'], moment_nm=p['moment_nm'],
                positive_share=p['moment_nm']/positive_sum) for p in positive[:3]])
    except (AttributeError, IndexError, KeyError, TypeError, ValueError, OverflowError) as error:
        result.update(reason=str(error)[:240], candidates=[], part_contributions=[])
    return result


def _normalized_tipping_localization(value, *, root_part_id, part_ids):
    """Reject malformed optional diagnostics without changing the verdict."""
    try:
        # Also reject nonfinite/NumPy values in legacy mock or recovered results.
        normalized = json.loads(json.dumps(value, allow_nan=False))
        if (normalized['method'] != 'initial_directional_gravity_moment_v1'
                or normalized['root_part_id'] != root_part_id or normalized['causal_claim'] is not False):
            raise ValueError('The tipping localization identity was invalid.')
        scalar_keys = ('first_tipping_time_s', 'support_boundary_m', 'net_moment_nm', 'positive_moment_sum_nm')
        numeric = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)
        scalars = [normalized[k] for k in scalar_keys]
        direction = normalized['direction_xy']
        if (any(v is not None and not numeric(v) for v in scalars)
                or direction is not None and (not isinstance(direction, list) or len(direction) != 2
                    or not all(numeric(v) for v in direction))):
            raise ValueError('The tipping localization numeric summary was invalid.')
        candidates = normalized['candidates']
        if not isinstance(candidates, list):
            raise ValueError('The tipping localization candidate list was invalid.')
        if normalized['status'] == 'RANKED':
            if (any(v is None for v in scalars) or direction is None
                    or normalized['first_tipping_time_s'] < 0 or normalized['positive_moment_sum_nm'] <= 0):
                raise ValueError('The ranked tipping summary was incomplete.')
            names = [p['part_id'] for p in candidates]
            if (not 1 <= len(names) <= 3 or len(set(names)) != len(names)
                    or any(n not in part_ids for n in names)
                    or normalized['reason_code'] is not None or normalized['reason'] is not None
                    or any(not numeric(p['moment_nm']) or p['moment_nm'] <= 0
                           or not numeric(p['positive_share'])
                           or not 0 < p['positive_share'] <= 1 for p in candidates)):
                raise ValueError('The ranked tipping targets were invalid.')
        elif (normalized['status'] != 'FALLBACK' or candidates != []
                or normalized['reason_code'] not in ('INTERFACE_EXIT_FIRST', 'REFERENCE_UNAVAILABLE', 'NO_POSITIVE_CONTRIBUTION')
                or not isinstance(normalized['reason'], str) or not normalized['reason']):
            raise ValueError('The tipping localization fallback was invalid.')
        return normalized
    except (KeyError, TypeError, ValueError, OverflowError) as error:
        return _localize_tipping(reference=dict(root_part_id=root_part_id,
            reference_error=str(error)[:240]), first_tipping=None, exits={}) | {
                'reason': f'Invalid tipping localization: {str(error)[:200]}'}


def simulate(xml,report,config,output, *, body_layout):
    import mujoco
    import manifold3d as mf
    from adsl.core.assembly_topology import mm_matrix,query_solids,solid_mesh,length_bound,checked
    from adsl.core.assembly import TabSlot
    started=time.monotonic()
    try:
        model=mujoco.MjModel.from_xml_string(xml);data=mujoco.MjData(model);mujoco.mj_forward(model,data)
    except mujoco.FatalError as error:
        raise ValueError(f'SIMULATION_UNVERIFIED: MuJoCo execution error: {error}') from error
    bodies=body_layout['bodies']
    ids={n:mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,n) for n in bodies}
    if any(i <= 0 for i in ids.values()) or len(set(ids.values())) != len(ids):
        raise ValueError('INVALID_PHYSICAL_INPUT: simulation component bodies unavailable')
    addresses={}
    for name,body_id in ids.items():
        joint=int(model.body_jntadr[body_id])
        if joint < 0:
            raise ValueError('INVALID_PHYSICAL_INPUT: component free joint unavailable')
        addresses[name]=dict(body_id=body_id,joint_id=joint,
            qpos_address=int(model.jnt_qposadr[joint]),dof_address=int(model.jnt_dofadr[joint]))
        bodies[name].update(addresses[name])
    component_to_part={n:b['part_id'] for n,b in bodies.items()}
    flex_parts={i:mujoco.mj_id2name(model,mujoco.mjtObj.mjOBJ_BODY,
        int(model.flex_vertbodyid[model.flex_vertadr[i]])) for i in range(model.nflex)}
    def contacts():
        rows=[]
        for c in data.contact:
            component_ids=[flex_parts.get(int(i),'static_or_geom') for i in c.flex]
            rows.append(dict(distance_mm=float(c.dist)*1000,position_mm=(c.pos*1000).tolist(),
                geom_ids=c.geom.tolist(),flex_ids=c.flex.tolist(),element_ids=c.elem.tolist(),
                component_ids=component_ids,
                part_ids=[component_to_part.get(n,n) for n in component_ids]))
        return dict(count=len(rows),minimum_distance_mm=min((r['distance_mm'] for r in rows),default=None),
            flex_flex_count=sum(all(i>=0 for i in r['flex_ids']) for r in rows),
            flex_geom_count=sum(sum(i>=0 for i in r['flex_ids'])==1 for r in rows),contacts=rows)
    initial_contacts=contacts();write_json(output/'initial_contacts.json',initial_contacts)
    last_contacts=initial_contacts
    max_contacts=int(data.ncon);max_flex_contacts=initial_contacts['flex_flex_count']
    root_name=body_layout['root_component_id'];root_id=ids[root_name]
    initial={n:data.xmat[i].reshape(3,3).copy() for n,i in ids.items()}
    tipping_reference = _initial_tipping_reference(model,data,part_body_ids=ids,
        root_part_id=report['root_id'],root_component_id=root_name,component_to_part=component_to_part,
        floor_geom_id=mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_GEOM,'floor'),flex_part_ids=flex_parts)
    frames={};tabs={};cavities={};observation_errors=[]
    for c in report['connections']:
        if body_layout['interfaces'][c['id']]['status'] != 'BOUND':
            observation_errors.append(dict(connection_id=c['id'],reason='INTERFACE_COMPONENT_UNAVAILABLE'))
            continue
        frames[c['id']]=(mm_matrix(c['tab_frame'],report['mm_per_unit']),mm_matrix(c['slot_frame'],report['mm_per_unit']))
        tabs[c['id']]=query_solids(TabSlot(**c['parameters']))[0]
        p=c['parameters']
        cavities[c['id']]=mf.Manifold.cube((p['width_mm']+2*p['fit_offset_mm'],
            p['thickness_mm']+2*p['fit_offset_mm'],p['slot_depth_mm']),True).translate((0,0,p['slot_depth_mm']/2))
    def pose(n):
        m=np.eye(4);m[:3,:3]=data.xmat[ids[n]].reshape(3,3);m[:3,3]=data.xpos[ids[n]]*1000;return m
    measurements={n:dict(component_id=n,part_id=component_to_part[n],peak_tilt_deg=None,
        final_tilt_deg=None,first_tipping_seconds=None) for n in ids}
    trajectory=[];exits={};last=[];first_tipping=None;simulation_complete=True
    steps=math.ceil(config['duration_seconds']/model.opt.timestep)
    stride=max(1,round(.02/model.opt.timestep))
    for k in range(steps+1):
        if k:
            previous_time=float(data.time)
            try:
                mujoco.mj_step(model,data)
            except mujoco.FatalError as error:
                if not trajectory:
                    raise ValueError(f'SIMULATION_UNVERIFIED: MuJoCo execution error: {error}') from error
                observation_errors.append(dict(reason='SIMULATION_UNVERIFIED',detail=str(error)[:240]));simulation_complete=False;break
            if (not math.isfinite(float(data.time)) or data.time<=previous_time
                    or not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all()):
                observation_errors.append(dict(reason='SIMULATION_UNVERIFIED',detail='nonfinite state or MuJoCo time reset'))
                simulation_complete=False;break
        if k%stride and k!=steps: continue
        max_contacts=max(max_contacts,int(data.ncon));last_contacts=contacts()
        max_flex_contacts=max(max_flex_contacts,last_contacts['flex_flex_count'])
        sample={}
        for name,body_id in ids.items():
            current=data.xmat[body_id].reshape(3,3)
            if not np.isfinite(current).all() or not np.isfinite(initial[name]).all():
                observation_errors.append(dict(component_id=name,reason='COMPONENT_ROTATION_UNAVAILABLE'))
                simulation_complete=False;sample[name]=None;continue
            tilt=math.degrees(math.acos(np.clip((current@initial[name].T)[2,2],-1,1)))
            sample[name]=tilt;entry=measurements[name]
            entry['peak_tilt_deg']=max(entry['peak_tilt_deg'] or 0.,tilt);entry['final_tilt_deg']=tilt
            if entry['first_tipping_seconds'] is None and tilt > config['tilt_threshold_deg']:
                entry['first_tipping_seconds']=float(data.time)
                if name==root_name:
                    first_tipping=dict(time_seconds=float(data.time),root_rotation=current.copy())
        joints=[]
        for c in report['connections']:
            cid=c['id'];binding=body_layout['interfaces'][cid]
            if cid not in frames:
                joints.append(dict(connection_id=cid,status='UNAVAILABLE',exited=None));continue
            try:
                tf,sf=frames[cid]
                rel=np.linalg.inv(pose(binding['slot_component_id'])@sf)@pose(binding['tab_component_id'])@tf
                tab=checked(tabs[cid].transform(rel[:3,:]));cavity=cavities[cid]
                inserted=checked(tab^cavity);bound=length_bound(solid_mesh(tab))*solid_mesh(cavity).area
                occupied=float(inserted.volume());exited=bool(occupied<=bound)
                interval=None if inserted.is_empty() else np.asarray(inserted.bounding_box()).reshape(2,3)[:,2].tolist()
                if exited: exits.setdefault(cid,float(data.time))
                joints.append(dict(connection_id=cid,status='PASS',exited=exited,exit_axial_mm=float(-rel[2,3]),
                    lateral_offset_mm=float(np.linalg.norm(rel[:2,3])),
                    relative_angle_deg=math.degrees(math.acos(np.clip((np.trace(rel[:3,:3])-1)/2,-1,1))),
                    insertion_volume_mm3=occupied,volume_uncertainty_mm3=bound,
                    effective_insertion_interval_mm=interval,slot_frame_world_mm=(pose(binding['slot_component_id'])@sf).tolist()))
            except (ValueError, np.linalg.LinAlgError) as error:
                observation_errors.append(dict(connection_id=cid,reason='INTERFACE_OBSERVATION_UNAVAILABLE',detail=str(error)[:240]))
                joints.append(dict(connection_id=cid,status='UNAVAILABLE',exited=None))
        linear=max((float(np.linalg.norm(data.qvel[a['dof_address']:a['dof_address']+3])) for a in addresses.values()),default=0.)
        angular=max((float(np.linalg.norm(data.qvel[a['dof_address']+3:a['dof_address']+6])) for a in addresses.values()),default=0.)
        if data.time>=config['duration_seconds']-config['settle_window_seconds']:last.append((linear,angular))
        trajectory.append(dict(time_seconds=float(data.time),tilt_deg=sample[root_name],component_tilts=sample,
            linear_speed_m_s=linear,angular_speed_rad_s=angular,interfaces=joints,qpos=data.qpos.tolist()))
        write_json(output/'stage.json',dict(stage='self_weight_simulation',status='RUNNING',
            simulated_seconds=float(data.time),wall_seconds=time.monotonic()-started,contacts=int(data.ncon)))
    if not trajectory:
        raise ValueError('OBSERVATION_UNAVAILABLE: no valid simulation sample')
    np.savez(output/'trajectory.npz',time=np.array([r['time_seconds'] for r in trajectory]),
        qpos=np.array([r['qpos'] for r in trajectory],dtype=np.float64))
    write_json(output/'trajectory.json',trajectory);write_json(output/'final_contacts.json',last_contacts)
    final=trajectory[-1];root=measurements[root_name]
    verified=(simulation_complete and round(final['time_seconds'],10)>=config['duration_seconds']
        and all(len([r for r in trajectory if r['component_tilts'][n] is not None])==len(trajectory) for n in ids))
    peaks=[r['peak_tilt_deg'] for r in measurements.values() if r['peak_tilt_deg'] is not None]
    component_tipped=any(p > config['tilt_threshold_deg'] for p in peaks)
    settled=bool(last) and max(v[0] for v in last)<=config['settle_linear_m_s'] and max(v[1] for v in last)<=config['settle_angular_rad_s']
    result=dict(observation_duration_seconds=config['duration_seconds'],simulation_wall_seconds=time.monotonic()-started,
        initial_contact_count=initial_contacts['count'],initial_minimum_contact_distance_mm=initial_contacts['minimum_distance_mm'],
        max_contact_count=max_contacts,max_interpart_flex_contacts=max_flex_contacts,
        simulated_duration_seconds=round(final['time_seconds'],10),assessment_time_seconds=round(final['time_seconds'],10),
        assessment_scope='Free material bodies; full gravity observation, first events do not stop simulation',
        final_tipped=root['final_tilt_deg'] is not None and root['final_tilt_deg']>config['tilt_threshold_deg'],
        final_exited_interfaces=[c['connection_id'] for c in final['interfaces'] if c['exited'] is True],
        peak_tilt_deg=root['peak_tilt_deg'],final_tilt_deg=root['final_tilt_deg'],
        tipped=root['peak_tilt_deg'] is not None and root['peak_tilt_deg']>config['tilt_threshold_deg'],
        first_tipping_seconds=root['first_tipping_seconds'],exits=exits,settled=settled,final_interfaces=final['interfaces'],
        final_linear_speed_m_s=final['linear_speed_m_s'],final_angular_speed_rad_s=final['angular_speed_rad_s'],
        component_tilts=list(measurements.values()),component_tilt_verified=verified,
        observed_component_tipping=True if component_tipped else False if verified else None,
        max_component_tilt_deg=max(peaks) if peaks else None,observation_errors=observation_errors,
        interface_retention_verified=simulation_complete and not observation_errors,
        interface_method='Declared TabSlot geometry at bound material component poses; no pullout-force claim')
    if result['tipped']:
        result['tipping_localization']=_localize_tipping(reference=tipping_reference,first_tipping=first_tipping,exits=exits)
    write_json(output/'body_mapping.json',_body_mapping(body_layout))
    return result,trajectory


def _component_tipping_findings(result, body_layout, threshold):
    """Filter sampled event order before selecting one representative per part."""
    root=body_layout['root_component_id'];root_time=result.get('first_tipping_seconds')
    root_tipped=result.get('tipped') is True;by_part={}
    for row in result.get('component_tilts',[]):
        peak=row.get('peak_tilt_deg')
        if row.get('component_id')==root or not isinstance(peak,(int,float)) or isinstance(peak,bool) or not math.isfinite(peak) or peak<=threshold:
            continue
        when=row.get('first_tipping_seconds')
        if not root_tipped: order='ROOT_NOT_OBSERVED'
        elif root_time is None or when is None: order='UNORDERED'
        elif when < root_time: order='BEFORE_ROOT'
        else: continue
        part=body_layout['bodies'][row['component_id']]['part_id']
        by_part.setdefault(part,[]).append(dict(row,event_order=order))
    findings=[]
    for part,rows in by_part.items():
        selected=min(rows,key=lambda r:(-r['peak_tilt_deg'],r.get('first_tipping_seconds') if r.get('first_tipping_seconds') is not None else math.inf,r['component_id']))
        domain={k:selected.get(k) for k in ('component_id','peak_tilt_deg','first_tipping_seconds','event_order')}
        domain.update(part_id=part,tilt_threshold_deg=threshold,root_first_tipping_seconds=root_time)
        findings.append(finding(NAME,'SELF_WEIGHT_COMPONENT_TIPPING',
            f"{part}: material component {selected['component_id']} sampled peak tilt {selected['peak_tilt_deg']:.4g} deg; limit {threshold}",
            part_ids=[part],domain=domain))
    return findings


def save_frames(report,meshes,trajectory,output, *, body_layout):
    """Actual simulated body poses, viewed with original material geometry."""
    from scipy.spatial.transform import Rotation
    try:
        import matplotlib
        matplotlib.use('Agg')
        import matplotlib.pyplot as plt
        from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    except ImportError as error:
        write_json(output/'plot_status.json',{'status':'UNAVAILABLE','reason':str(error)})
        return []
    if not trajectory: return []
    indices=sorted({0,len(trajectory)-1,max(range(len(trajectory)),key=lambda i:trajectory[i]['tilt_deg'] if trajectory[i]['tilt_deg'] is not None else -1),
        next((i for i,r in enumerate(trajectory) if any(c['exited'] for c in r['interfaces'])),len(trajectory)-1)})
    paths=[]
    for i in indices:
        row=trajectory[i];q=np.asarray(row['qpos']);placed=[]
        for body in body_layout['bodies'].values():
            mesh=body['mesh'];offset=body['qpos_address'];pose=q[offset:offset+7]
            rot=Rotation.from_quat(pose[[4,5,6,3]]).as_matrix()
            placed.append((mesh.vertices@rot.T+pose[:3]*1000)[mesh.faces])
        points=np.concatenate([t.reshape(-1,3) for t in placed]);lo=points.min(axis=0);hi=points.max(axis=0)
        centre=(lo+hi)/2;span=max(hi-lo)*.65
        fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(projection='3d')
        for j,tri in enumerate(placed):
            ax.add_collection3d(Poly3DCollection(tri,alpha=.8,facecolor=plt.cm.tab10(j%10),linewidth=.05))
        xx,yy=np.meshgrid([centre[0]-span,centre[0]+span],[centre[1]-span,centre[1]+span])
        ax.plot_surface(xx,yy,np.zeros((2,2)),alpha=.15,color='gray')
        ax.set(xlim=(centre[0]-span,centre[0]+span),ylim=(centre[1]-span,centre[1]+span),
            zlim=(min(0,lo[2]),max(hi[2],1)*1.15),xlabel='X mm',ylabel='Y mm',zlabel='Z mm',
            title=f'MuJoCo self weight, t={row["time_seconds"]:.2f}s, root tilt={row["tilt_deg"]} deg')
        path=output/f'simulation_{i:04d}.png';fig.savefig(path,dpi=120);plt.close(fig);paths.append(str(path))
    return paths


def analyze(args,physics):
    return with_protocol(_analyze(args, physics))


def _analyze(args,physics):
    config=physics.get('standing',{});density=physics.get('material',{}).get('density_kg_m3')
    required=('duration_seconds','timestep_seconds','friction','tilt_threshold_deg','settle_window_seconds',
              'settle_linear_m_s','settle_angular_rad_s')
    backend=config.get('collision_backend','coacd')
    if backend not in ('coacd','rigid_flex'): raise ValueError('unsupported standing collision_backend')
    if backend=='coacd': required+=('coacd',)
    if density is None or any(k not in config for k in required):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NEEDS_SPEC: density/contact/settling settings')
    if (not math.isfinite(float(density)) or density<=0
            or any(not math.isfinite(float(config[k])) for k in required if k not in ('coacd','friction'))
            or len(config['friction']) != 3
            or any(not math.isfinite(float(v)) or v < 0 for v in config['friction'])
            or config['timestep_seconds']<=0 or not 0<config['settle_window_seconds']<=config['duration_seconds']):
        raise ValueError('invalid standing configuration')
    report,rows,meshes=load_parts(args)
    def usable(row):
        if row.get('part_id') not in meshes: return False
        if row.get('status')=='PASS': return True
        return (row.get('status')=='FAIL' and row.get('code')=='INTERNAL_PART_DISCONNECTED'
            and all(code=='INTERNAL_PART_DISCONNECTED' for code in row.get('failures',[])))
    if not rows or not all(usable(r) for r in rows):
        return CheckerResult(checker=NAME,status='INDETERMINATE',
            summary='DEPENDENCY_MESH_UNAVAILABLE: valid material input required',metrics={'items':rows})
    _validate_physical_inputs(report,meshes)
    if any(c['parameters']['fit_offset_mm']<0 for c in report['connections']):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='RIGID_CONTACT_INTERFERENCE_UNSUPPORTED: elastic press fit not simulated')
    body_layout=_prepare_simulation_bodies(report,meshes)
    write_json(args.output/'body_mapping.json',_body_mapping(body_layout))
    proxies={};checks={}
    contact=flex_contact_settings(report,config) if backend=='rigid_flex' else None
    if contact: write_json(args.output/'rigid_flex_contact.json',contact)
    for name,body in body_layout['bodies'].items() if backend=='coacd' else ():
        mesh=body['mesh'];part=body['part_id']
        write_json(args.output/'stage.json',{'stage':'collision_decomposition','part_id':part,'component_id':name,'status':'RUNNING'})
        proxies[name]=collision_proxies(mesh,config['coacd'])
        gaps=[c['parameters']['fit_offset_mm'] for c in report['connections'] if part in (c['tab_part'],c['slot_part'])]
        checks[name],_=verify_proxies(mesh,proxies[name],min(gaps) if gaps else None)
        for i,p in enumerate(proxies[name]): p.export(args.output/f'{name}.proxy_{i}.obj')
    if backend=='coacd':
        write_json(args.output/'collision_proxy_validation.json',{'components':checks})
        if not all(c['accepted'] for c in checks.values()):
            return CheckerResult(checker=NAME,status='INDETERMINATE',summary='COLLISION_PROXY_UNVERIFIED',metrics={'proxy_checks':checks},
                artifacts={'body_mapping':str(args.output/'body_mapping.json')})
    write_json(args.output/'stage.json',{'stage':'self_weight_simulation','status':'RUNNING'})
    xml=xml_model(report,meshes,proxies,config,density,body_layout=body_layout);(args.output/'model.xml').write_text(xml)
    result,trajectory=simulate(xml,report,config,args.output,body_layout=body_layout)
    write_json(args.output/'body_mapping.json',_body_mapping(body_layout))
    def numeric(value):
        return isinstance(value,(int,float)) and not isinstance(value,bool) and math.isfinite(value) and value>=0
    component_rows=result.get('component_tilts',[])
    known=[r for r in component_rows if r.get('component_id') in body_layout['bodies']
        and r.get('part_id')==body_layout['bodies'][r['component_id']]['part_id']]
    complete_components=(result.get('component_tilt_verified') is True
        and len(known)==len(body_layout['bodies'])
        and {r['component_id'] for r in known}==set(body_layout['bodies'])
        and all(numeric(r.get(k)) for r in known for k in ('peak_tilt_deg','final_tilt_deg')))
    component_peaks=[r['peak_tilt_deg'] for r in known if numeric(r.get('peak_tilt_deg'))]
    component_tipped=any(v>config['tilt_threshold_deg'] for v in component_peaks)
    complete=(complete_components and result.get('interface_retention_verified') is True
        and all(numeric(result.get(k)) for k in ('assessment_time_seconds','peak_tilt_deg','final_tilt_deg'))
        and result['assessment_time_seconds']>=config['duration_seconds'])
    component_verified=bool(complete_components and numeric(result.get('assessment_time_seconds'))
        and result['assessment_time_seconds']>=config['duration_seconds'])
    result['component_tilt_verified']=component_verified
    result['observed_component_tipping']=True if component_tipped else False if component_verified else None
    result['max_component_tilt_deg']=max(component_peaks) if component_peaks else None
    findings=[]
    if result.get('tipped') is True:
        localization=result.get('tipping_localization')
        if 'tipping_localization' in result:
            localization=_normalized_tipping_localization(localization,root_part_id=report['root_id'],part_ids=set(meshes))
            result['tipping_localization']=localization
        tipping=finding(NAME,'SELF_WEIGHT_TIPPING',
            f"Root observation peak {result.get('peak_tilt_deg')} deg; limit {config['tilt_threshold_deg']}",
            part_ids=[report['root_id']],domain=result)
        if localization is not None and localization['status']=='RANKED':
            tipping.region.part_names=[p['part_id'] for p in localization['candidates']]
        findings.append(tipping)
    findings.extend(_component_tipping_findings(result,body_layout,config['tilt_threshold_deg']))
    for cid,t in result.get('exits',{}).items():
        c=next(c for c in report['connections'] if c['id']==cid)
        final=next((r for r in result.get('final_interfaces',[]) if r['connection_id']==cid),None)
        findings.append(finding(NAME,'SELF_WEIGHT_INTERFACE_EXIT',
            f'{cid}: '+('still outside' if cid in result.get('final_exited_interfaces',[]) else 'back inside')+
            f' insertion region; first exit at {t:.4g} s (simulation continued)',
            part_ids=[c['tab_part'],c['slot_part']],domain={'connection_id':cid,'first_exit_seconds':t,
                'assessment_time_seconds':result.get('assessment_time_seconds'),'final_interface':final}))
    failed=(result.get('tipped') is True or component_tipped or bool(result.get('exits')))
    status='FAIL' if failed else 'PASS' if complete else 'INDETERMINATE'
    result['standing_observation']=dict(status='UNAVAILABLE' if status=='INDETERMINATE' else status,
        observed_tipping=True if result.get('tipped') is True else False if complete else None,
        observed_interface_exit=True if result.get('exits') else False if complete else None,
        peak_tilt_deg=result.get('peak_tilt_deg'),final_tilt_deg=result.get('final_tilt_deg'),
        duration_s=result.get('assessment_time_seconds'),component_tilt_verified=component_verified,
        observed_component_tipping=result['observed_component_tipping'],max_component_tilt_deg=result['max_component_tilt_deg'])
    contact_note=''
    if contact and 'initial_minimum_contact_distance_mm' in result:
        from adsl.core.assembly_topology import length_bound
        limit=contact['paired_shell_thickness_mm']+2*max(length_bound(m) for m in meshes.values())
        raw_depth=result['initial_minimum_contact_distance_mm']
        depth=max(0.,-(raw_depth or 0.))
        result['initial_contact_check']=dict(verified=depth<=limit,maximum_penetration_mm=depth,shell_plus_numeric_bound_mm=limit)
        result['contact_diagnostic']=dict(status='WARNING' if depth>limit else 'OK',
            code='INITIAL_CONTACT_DEPTH_EXCEEDS_REFERENCE' if depth>limit else 'INITIAL_CONTACT_DEPTH_WITHIN_REFERENCE',
            maximum_penetration_mm=depth,reference_bound_mm=limit,affects_standing_verdict=False)
        if depth>limit: contact_note='INITIAL_CONTACT_DEPTH_EXCEEDS_REFERENCE: contact warning only; standing verdict unchanged | '
    write_json(args.output/'stage.json',{'stage':'completed','status':status})
    result['simulation_frames']=save_frames(report,meshes,trajectory,args.output,body_layout=body_layout)
    reason=('OBSERVATION_UNAVAILABLE: complete standing/interface/component observation required | ' if not complete else '')
    return CheckerResult(checker=NAME,status=status,
        summary=contact_note+reason+f"After {result.get('assessment_time_seconds')} s self-weight: "
            f"root peak {result.get('peak_tilt_deg')} deg; component peak {result['max_component_tilt_deg']} deg; "
            f'standing observation {status}; settling diagnostic: '+
            ('settled' if result.get('settled') else 'motion not settled')+' (not a pass requirement)',
        metrics=result,findings=findings,
        violations=[dict(code=r['reason'],message=r.get('detail',''))
            for r in result.get('observation_errors',[]) if r.get('reason')=='SIMULATION_UNVERIFIED'],
        assumptions={'uniform_density_kg_m3':density,'external_load_stability':'NOT_EVALUATED',
            'standing_criterion':'No material component sampled tilt above configured limit and no declared interface exit; full observation required for PASS; settling diagnostic only',
            'contact':'independent material bodies, no weld; '+('rigid surface flex, display mesh collision disabled' if contact else 'CoACD proxies verified locally by sampling/queries'),
            'collision_backend':backend,'rigid_flex_contact':contact,'mujoco_version':importlib.metadata.version('mujoco'),
            'coacd_version':importlib.metadata.version('coacd') if backend=='coacd' else None,'configuration':config},
        artifacts={'trajectory':str(args.output/'trajectory.json'),'simulation':str(args.output/'model.xml'),
            'body_mapping':str(args.output/'body_mapping.json')})

if __name__=='__main__':cli(NAME,analyze)
