"""Independent fixed-ground rigid-body protocol. Inputs mm; MuJoCo SI."""
import hashlib
from pathlib import Path
from functools import lru_cache
import time
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation
from .vendor.rigid_flex import flex_contact_settings, xml_model

RIGID_FLEX_PROTOCOL='snap3d_fixed_ground_mujoco_rigid_flex_v1'


def _contact_bodies(model, contact, flex_bodies):
    """MuJoCo flex contacts use geom=-1; never index geom_bodyid with that ID."""
    geoms=getattr(contact,'geom',None)
    if geoms is None:geoms=[contact.geom1,contact.geom2]
    flexes=getattr(contact,'flex',[-1,-1])
    return tuple(int(model.geom_bodyid[int(g)]) if int(g)>=0 else flex_bodies.get(int(f))
                 for g,f in zip(geoms,flexes))


def _initial_contacts(model, data, flex_bodies, *, include_ground=True):
    rows=[]
    for c in data.contact:
        a,b=_contact_bodies(model,c,flex_bodies)
        if a is not None and b is not None and a!=b and (include_ground or (a>0 and b>0)):rows.append(c)
    return dict(maximum_penetration_mm=max((max(0.,-float(c.dist)*1000) for c in rows),default=0.),
                contact_count=len(rows),total_contact_count=int(data.ncon) if hasattr(data,'ncon') else len(data.contact),
                flex_flex_count=sum(all(int(i)>=0 for i in getattr(c,'flex',[-1,-1])) for c in rows))

def _rigid_flex_xml(parts, ids, fixed_ids, config, clearance_mm, gravity=None, *, normal_only_diagnostic=False):
    # Body-local material and millimetre assembly transform keep the existing
    # SNAP3D COM origin while delegating all XML/inertia construction to aDSL.
    bodies={}
    for k,part in enumerate(ids):
        mesh=parts[part]
        if not mesh.is_volume:raise ValueError('INVALID_INPUT')
        com=np.asarray(mesh.center_mass,dtype=float)
        local=mesh.copy();local.apply_translation(-com)
        matrix=np.eye(4);matrix[:3,3]=com
        bodies[f'part_{k}']=dict(mesh=local,assembly_transform_mm=matrix.tolist())
    report=dict(connections=[dict(parameters=dict(fit_offset_mm=clearance_mm))])
    source_config=dict(config,timestep_seconds=config['dt'],collision_backend='rigid_flex')
    contact=flex_contact_settings(report,source_config)
    root=ET.fromstring(xml_model(report,parts,{},source_config,config['density'],body_layout=dict(bodies=bodies)))
    removed_meshes=set()
    for body in root.findall('./worldbody/body'):
        for geom in list(body.findall('geom')):
            if geom.get('contype')=='0':
                if geom.get('mesh'):removed_meshes.add(geom.get('mesh'))
                body.remove(geom)
    referenced={node.get('mesh') for node in root.findall('.//*[@mesh]')}
    asset=root.find('asset')
    if asset is not None:
        for mesh in list(asset.findall('mesh')):
            if mesh.get('name') in removed_meshes and mesh.get('name') not in referenced:asset.remove(mesh)
    size=root.find('size')
    if size is None:size=ET.SubElement(root,'size')
    size.set('memory',str(config.get('memory','1G')))
    for k,part in enumerate(ids):
        if part in fixed_ids:
            body=root.find(f"./worldbody/body[@name='part_{k}']")
            for joint in list(body.findall('freejoint')):body.remove(joint)
    g=config['gravity'] if gravity is None else gravity
    root.find('option').set('gravity',f'0 0 {-g}')
    # The only extra collision override is the explicitly authorized diagnostic.
    # Its caller supplies zero friction; production model settings stay intact.
    if normal_only_diagnostic:
        if list(config['friction'])!=[0.,0.,0.]:
            raise ValueError('NORMAL_CONTACT_DIAGNOSTIC_REQUIRES_ZERO_FRICTION')
        for node in root.findall('./deformable/flex/contact'):node.set('condim','1')
    return ET.tostring(root,encoding='unicode'),contact


def angle(matrix):
    return float(np.linalg.norm(Rotation.from_matrix(matrix).as_rotvec()))


@lru_cache(maxsize=32)
def _saved_xml_hash(path,mtime_ns,size):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _model_evidence(xml,model_path):
    current=hashlib.sha256(xml.encode()).hexdigest()
    if model_path is None:return dict(model_sha256=current,xml_ref=None)
    path=Path(model_path);path.parent.mkdir(parents=True,exist_ok=True)
    if not path.exists():path.write_text(xml)
    stat=path.stat();saved=_saved_xml_hash(str(path.resolve()),stat.st_mtime_ns,stat.st_size)
    return dict(model_sha256=current,xml_ref=dict(path=path.name,sha256=saved,
        relative_to='case_root',scope='first_case_model',matches_current_model=saved==current))

def rollout(parts, *, D, config, clearance_mm, fixed_ids=None,
            gravity=None, retain_frames=True, initial_only=False, normal_only_diagnostic=False,model_path=None):
    import mujoco
    started=time.monotonic()
    backend=config.get('collision_backend','rigid_flex')
    ids=sorted(parts)
    if fixed_ids is None:
        fixed_ids=[i for i in ids if parts[i].bounds[0,2]<=D*config['ground_ratio']]
    fixed_ids=list(fixed_ids)
    base=dict(protocol_id=RIGID_FLEX_PROTOCOL,
              collision_backend=backend,normal_only_diagnostic=normal_only_diagnostic,
              fixed_ids=fixed_ids,status='UNAVAILABLE',reason=None)
    if not set(fixed_ids)<=set(ids): return dict(base,reason='FIXED_ID_MISSING')
    free=[i for i in ids if i not in fixed_ids]
    if backend != 'rigid_flex':
        return dict(base,reason='UNSUPPORTED_COLLISION_BACKEND')
    try:
        xml,contact=_rigid_flex_xml(parts,ids,fixed_ids,config,clearance_mm,gravity,
                                   normal_only_diagnostic=normal_only_diagnostic)
    except ValueError as error:
        return dict(base,reason=str(error).split(':',1)[0],detail=str(error))
    base.update(_model_evidence(xml,model_path))
    backend_info=dict(flex_element_count=sum(len(parts[i].faces) for i in ids),
                      flex_element_counts={i:len(parts[i].faces) for i in ids},rigid_flex_contact=contact)
    completed_steps=0;initialization_wall_seconds=None;step_started=None
    try:
        model=mujoco.MjModel.from_xml_string(xml); data=mujoco.MjData(model); mujoco.mj_forward(model,data)
        flex_bodies={i:int(model.flex_vertbodyid[int(model.flex_vertadr[i])]) for i in range(getattr(model,'nflex',0))}
        flex_elements={i:int(model.flex_elemnum[i]) for i in range(getattr(model,'nflex',0))}
        initial_contact=_initial_contacts(model,data,flex_bodies)
        initial_contact.update(bound_mm=None,affects_verdict=False,**{'pass':None})
        backend_info=dict(flex_element_count=sum(flex_elements.values()),
                          flex_element_counts={i:flex_elements[k] for k,i in enumerate(ids)} if flex_elements else {},
                          rigid_flex_contact=contact)
        if initial_only:
            return dict(base,status='PASS',initial_contact=initial_contact,
                        **backend_info,simulation_performed=False,wall_seconds=time.monotonic()-started)
        bodies={i:mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,f'part_{k}') for k,i in enumerate(ids)}
        reverse={v:k for k,v in bodies.items()}
        def snapshot():
            out={}
            for i,b in bodies.items():
                velocity=np.zeros(6)
                mujoco.mj_objectVelocity(model,data,mujoco.mjtObj.mjOBJ_BODY,b,velocity,0)
                out[i]=dict(position_mm=(data.xpos[b]*1000).tolist(),rotation=data.xmat[b].reshape(3,3).tolist(),
                            linear_velocity_world_m_s=velocity[3:].tolist())
            return out
        frames=[snapshot()]; max_depth=0.;max_contacts=int(data.ncon)
        steps=int(round(config['duration']/config['dt']))
        initialization_wall_seconds=time.monotonic()-started;step_started=time.monotonic()
        for _ in range(steps):
            mujoco.mj_step(model,data)
            if not np.isfinite(data.qpos).all() or not np.isfinite(data.qvel).all() or any(w.number for w in data.warning):
                return dict(base,reason='SIMULATION_ERROR',frames=frames,**backend_info,
                    initial_contact=initial_contact,initial_contact_count=initial_contact['total_contact_count'],
                    max_contact_count=max(max_contacts,int(data.ncon)),last_contact_count=int(data.ncon),
                    simulated_seconds=float(data.time),wall_seconds=time.monotonic()-started,
                    initialization_wall_seconds=initialization_wall_seconds,step_count=completed_steps,
                    step_wall_seconds=time.monotonic()-step_started)
            # mj_step's xpos may precede integrated qpos: explicitly synchronize.
            mujoco.mj_forward(model,data)
            frames.append(snapshot())
            completed_steps+=1
            max_depth=max(max_depth,max((-float(c.dist)*1000 for c in data.contact),default=0.))
            max_contacts=max(max_contacts,int(data.ncon))
        step_wall_seconds=time.monotonic()-step_started
        contacts=set()
        for c in data.contact:
            body_a,body_b=_contact_bodies(model,c,flex_bodies)
            a=reverse.get(body_a); b=reverse.get(body_b)
            if a and b and a!=b: contacts.add(tuple(sorted((a,b))))
        metrics={}
        for i in ids:
            p0=np.array(frames[0][i]['position_mm']); r0=np.array(frames[0][i]['rotation'])
            metrics[i]=dict(max_displacement_mm=max(float(np.linalg.norm(np.array(f[i]['position_mm'])-p0)) for f in frames),
                           max_rotation_rad=max(angle(np.array(f[i]['rotation'])@r0.T) for f in frames),
                           max_linear_speed_m_s=max(float(np.linalg.norm(f[i]['linear_velocity_world_m_s'])) for f in frames))
        score=max((max(metrics[i]['max_displacement_mm']/(D*config['displacement_ratio']),
                      metrics[i]['max_rotation_rad']/np.deg2rad(config['rotation_deg'])) for i in free),default=0.)
        score=float(score)
        return dict(base,status='PASS',reason=None,stable=score<=1.,global_score=score,
                    metrics=metrics,frames=frames if retain_frames else [],last_contact_pairs=[list(p) for p in sorted(contacts)],
                    peak_penetration_mm=max_depth,**backend_info,
                    initialization_wall_seconds=initialization_wall_seconds,
                    step_count=completed_steps,step_wall_seconds=step_wall_seconds,
                    mean_step_wall_seconds=step_wall_seconds/completed_steps if completed_steps else None,
                    initial_contact=initial_contact,final_contact=_initial_contacts(model,data,flex_bodies),
                    final_interpart_contact=_initial_contacts(model,data,flex_bodies,include_ground=False),
                    initial_contact_count=int(initial_contact['total_contact_count']),
                    max_contact_count=max_contacts,final_contact_count=int(data.ncon),
                    simulated_seconds=float(data.time),wall_seconds=time.monotonic()-started,
                    max_linear_speed_m_s=max((metrics[i]['max_linear_speed_m_s'] for i in free),default=0.))
    except (ValueError,RuntimeError,getattr(mujoco,'FatalError',RuntimeError)) as error:
        return dict(base,reason='SIMULATION_ERROR',detail=str(error),error_type=type(error).__name__,
                    **backend_info,wall_seconds=time.monotonic()-started,
                    initialization_wall_seconds=initialization_wall_seconds,step_count=completed_steps,
                    step_wall_seconds=None if step_started is None else time.monotonic()-step_started)

def relative_score(result, a, b, *, D, config):
    def pose(frame):
        ra=np.asarray(frame[a]['rotation']); rb=np.asarray(frame[b]['rotation'])
        return ra.T@(np.asarray(frame[b]['position_mm'])-np.asarray(frame[a]['position_mm'])),ra.T@rb
    p0,r0=pose(result['frames'][0]); score=0.
    for f in result['frames']:
        p,r=pose(f)
        score=max(score,np.linalg.norm(p-p0)/(D*config['displacement_ratio']),angle(r@r0.T)/np.deg2rad(config['rotation_deg']))
    return float(score)

def select_edges(result, edges, *, D, config, attempted=()):
    if result['status']!='PASS': return []
    unstable={i for i,m in result['metrics'].items() if i not in result['fixed_ids'] and
              max(m['max_displacement_mm']/(D*config['displacement_ratio']),m['max_rotation_rad']/np.deg2rad(config['rotation_deg']))>1}
    groups={i:{i} for i in unstable}
    for a,b in result['last_contact_pairs']:
        if a in unstable and b in unstable:
            merged=groups[a]|groups[b]
            for i in merged: groups[i]=merged
    eligible=[]
    for e in edges:
        a,b=e['a'],e['b']
        crossing=(a in unstable and b not in groups[a]) or (b in unstable and a not in groups[b])
        if crossing and e['id'] not in attempted:
            eligible.append((relative_score(result,a,b,D=D,config=config),e['id']))
    return [eid for _,eid in sorted(eligible,key=lambda x:(-x[0],x[1]))]
