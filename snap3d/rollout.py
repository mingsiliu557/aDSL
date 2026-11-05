"""Independent fixed-ground rigid-body protocol. Inputs mm; MuJoCo SI."""
import itertools
import xml.etree.ElementTree as ET
import numpy as np
from scipy.spatial.transform import Rotation

PROTOCOL='snap3d_fixed_ground_mujoco_coacd_v1'

def angle(matrix):
    return float(np.linalg.norm(Rotation.from_matrix(matrix).as_rotvec()))

def rollout(parts, *, D, config, coacd_config, clearance_mm, fixed_ids=None,
            gravity=None, proxy_cache=None, retain_frames=True, initial_only=False):
    import mujoco
    ids=sorted(parts)
    if fixed_ids is None:
        fixed_ids=[i for i in ids if abs(parts[i].bounds[0,2])<=D*config['ground_ratio']]
    fixed_ids=list(fixed_ids)
    base=dict(protocol_id=PROTOCOL,fixed_ids=fixed_ids,status='UNAVAILABLE',reason=None)
    if not fixed_ids: return dict(base,reason='NO_GROUND_PARTS')
    if not set(fixed_ids)<=set(ids): return dict(base,reason='FIXED_ID_MISSING')
    free=[i for i in ids if i not in fixed_ids]
    if not free: return dict(base,reason='NO_FREE_PARTS')
    if proxy_cache is None or any(not proxy_cache.get(i) for i in ids):
        return dict(base,reason='PROXY_MISSING')
    root=ET.Element('mujoco',model='snap3d')
    ET.SubElement(root,'compiler',angle='radian',inertiafromgeom='false')
    g=config['gravity'] if gravity is None else gravity
    ET.SubElement(root,'option',timestep=str(config['dt']),gravity=f'0 0 {-g}',
                  integrator=config['integrator'],iterations=str(config['iterations']))
    asset=ET.SubElement(root,'asset'); world=ET.SubElement(root,'worldbody')
    ET.SubElement(world,'geom',name='ground',type='plane',size='1 1 .01',friction=' '.join(map(str,config['friction'])))
    proxies_by_id={}; proxies_count={}
    for k,i in enumerate(ids):
        mesh=parts[i]
        if not mesh.is_volume: return dict(base,reason='INVALID_INPUT',part_id=i)
        com=np.asarray(mesh.center_mass)*.001
        mass=float(mesh.volume)*1e-9*config['density']
        inertia=np.asarray(mesh.moment_inertia)*1e-15*config['density']
        if not np.isfinite(inertia).all() or np.linalg.eigvalsh(inertia).min()<=0:
            return dict(base,reason='INVALID_INERTIA',part_id=i)
        body=ET.SubElement(world,'body',name=f'part_{k}',pos=' '.join(map(str,com)))
        if i in free: ET.SubElement(body,'freejoint')
        ET.SubElement(body,'inertial',pos='0 0 0',mass=str(mass),
                      fullinertia=' '.join(map(str,[inertia[0,0],inertia[1,1],inertia[2,2],inertia[0,1],inertia[0,2],inertia[1,2]])))
        proxies=proxy_cache[i]
        proxies_by_id[i]=proxies; proxies_count[i]=len(proxies)
        for p,proxy in enumerate(proxies):
            name=f'mesh_{k}_{p}'
            ET.SubElement(asset,'mesh',name=name,vertex=' '.join(map(str,(np.asarray(proxy.vertices)*.001-com).ravel())),face=' '.join(map(str,np.asarray(proxy.faces).ravel())))
            ET.SubElement(body,'geom',name=f'geom_{k}_{p}',type='mesh',mesh=name,mass='0',
                          friction=' '.join(map(str,config['friction'])),margin='0',gap='0')
    xml=ET.tostring(root,encoding='unicode')
    try:
        model=mujoco.MjModel.from_xml_string(xml); data=mujoco.MjData(model); mujoco.mj_forward(model,data)
        depths=[max(0.,-float(c.dist)*1000) for c in data.contact
                if model.geom_bodyid[c.geom1]!=model.geom_bodyid[c.geom2]]
        initial_contact=dict(maximum_penetration_mm=max(depths,default=0.),
                             bound_mm=clearance_mm/4,contact_count=len(depths))
        initial_contact['pass']=initial_contact['maximum_penetration_mm']<=initial_contact['bound_mm']
        if not initial_contact['pass']:
            return dict(base,reason='PROXY_UNVERIFIED',initial_contact=initial_contact,
                        proxy_counts=proxies_count,simulation_performed=False)
        if initial_only:
            return dict(base,status='PASS',initial_contact=initial_contact,
                        proxy_counts=proxies_count,simulation_performed=False)
        bodies={i:mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,f'part_{k}') for k,i in enumerate(ids)}
        reverse={v:k for k,v in bodies.items()}
        def snapshot():
            return {i:dict(position_mm=(data.xpos[b]*1000).tolist(),rotation=data.xmat[b].reshape(3,3).tolist()) for i,b in bodies.items()}
        frames=[snapshot()]; max_depth=0.
        steps=int(round(config['duration']/config['dt']))
        for _ in range(steps):
            mujoco.mj_step(model,data)
            if not np.isfinite(data.qpos).all() or any(w.number for w in data.warning):
                return dict(base,reason='SIMULATION_ERROR',frames=frames)
            # mj_step's xpos may precede integrated qpos: explicitly synchronize.
            mujoco.mj_forward(model,data)
            frames.append(snapshot())
            max_depth=max(max_depth,max((-float(c.dist)*1000 for c in data.contact),default=0.))
        contacts=set()
        for c in data.contact:
            a=reverse.get(int(model.geom_bodyid[c.geom1])); b=reverse.get(int(model.geom_bodyid[c.geom2]))
            if a and b and a!=b: contacts.add(tuple(sorted((a,b))))
        metrics={}
        for i in ids:
            p0=np.array(frames[0][i]['position_mm']); r0=np.array(frames[0][i]['rotation'])
            metrics[i]=dict(max_displacement_mm=max(float(np.linalg.norm(np.array(f[i]['position_mm'])-p0)) for f in frames),
                           max_rotation_rad=max(angle(np.array(f[i]['rotation'])@r0.T) for f in frames))
        score=max(max(metrics[i]['max_displacement_mm']/(D*config['displacement_ratio']),
                      metrics[i]['max_rotation_rad']/np.deg2rad(config['rotation_deg'])) for i in free)
        return dict(base,status='PASS',reason=None,stable=score<=1.,global_score=score,
                    metrics=metrics,frames=frames if retain_frames else [],last_contact_pairs=[list(p) for p in sorted(contacts)],
                    peak_penetration_mm=max_depth,proxy_counts=proxies_count,xml=xml,
                    initial_contact=initial_contact)
    except (ValueError,RuntimeError) as error:
        return dict(base,reason='SIMULATION_ERROR',detail=str(error))

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
