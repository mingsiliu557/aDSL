"""Self-weight contact simulation of independent print parts, not welded bodies."""
from __future__ import annotations
from dataclasses import asdict
import importlib.metadata
import math
from pathlib import Path
import xml.etree.ElementTree as ET
import numpy as np

from .assembly_physics import cli, checker_spec as _spec, load_parts, finding
from .models import CheckerResult
from .utils.io import write_json

NAME='assembly_standing'
def checker_spec(timeout_seconds=900,**kwargs): return _spec(NAME,timeout_seconds,**kwargs)


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


def xml_model(report,meshes,proxies,config,density):
    from adsl.core.assembly_topology import mm_matrix
    from scipy.spatial.transform import Rotation
    root=ET.Element('mujoco',model='independent_assembly_self_weight')
    ET.SubElement(root,'compiler',angle='radian')
    ET.SubElement(root,'option',timestep=str(config['timestep_seconds']),gravity='0 0 -9.81',integrator='implicitfast')
    default=ET.SubElement(root,'default')
    ET.SubElement(default,'geom',friction=' '.join(map(str,config['friction'])),margin='0',gap='0')
    assets=ET.SubElement(root,'asset');world=ET.SubElement(root,'worldbody')
    ET.SubElement(world,'geom',name='floor',type='plane',size='10 10 .1',rgba='.8 .8 .8 1')
    parts={p['id']:p for p in report['parts']}
    transforms={n:mm_matrix(parts[n]['assembly_transform'],report['mm_per_unit']) for n in meshes}
    lowest=min(float((m.vertices@transforms[n][:3,:3].T+transforms[n][:3,3])[:,2].min()) for n,m in meshes.items())
    def values(a): return ' '.join(f'{float(v):.17g}' for v in np.asarray(a).ravel())
    for i,(name,mesh) in enumerate(meshes.items()):
        matrix=transforms[name].copy();matrix[2,3]-=lowest;matrix[:3,3]*=.001
        q=Rotation.from_matrix(matrix[:3,:3]).as_quat()[[3,0,1,2]]
        body=ET.SubElement(world,'body',name=name,pos=values(matrix[:3,3]),quat=values(q))
        ET.SubElement(body,'freejoint',name=f'free_{name}')
        # Inertia from non-overlapping material, NEVER the proxy-volume sum.
        physical=mesh.copy();physical.apply_scale(.001);physical.density=density
        inertia=physical.moment_inertia
        ET.SubElement(body,'inertial',pos=values(physical.center_mass),mass=str(physical.mass),
            fullinertia=values([inertia[0,0],inertia[1,1],inertia[2,2],inertia[0,1],inertia[0,2],inertia[1,2]]))
        for j,proxy in enumerate(proxies[name]):
            pid=f'proxy_{i}_{j}'
            ET.SubElement(assets,'mesh',name=pid,vertex=values(proxy.vertices*.001),
                face=' '.join(map(str,np.asarray(proxy.faces).ravel())))
            ET.SubElement(body,'geom',type='mesh',mesh=pid,rgba=values([.25+.1*(i%4),.45,.7,1]))
    return ET.tostring(root,encoding='unicode')


def simulate(xml,report,config,output):
    import mujoco
    import manifold3d as mf
    from adsl.core.assembly_topology import mm_matrix,query_solids,solid_mesh,length_bound,checked
    from adsl.core.assembly import TabSlot
    model=mujoco.MjModel.from_xml_string(xml);data=mujoco.MjData(model);mujoco.mj_forward(model,data)
    ids={p['id']:mujoco.mj_name2id(model,mujoco.mjtObj.mjOBJ_BODY,p['id']) for p in report['parts']}
    root_id=ids[report['root_id']];initial=data.xmat[root_id].reshape(3,3).copy()
    frames={c['id']:(mm_matrix(c['tab_frame'],report['mm_per_unit']),
        mm_matrix(c['slot_frame'],report['mm_per_unit'])) for c in report['connections']}
    tabs={c['id']:query_solids(TabSlot(**c['parameters']))[0] for c in report['connections']}
    cavities={}
    for c in report['connections']:
        p=c['parameters']
        cavities[c['id']]=mf.Manifold.cube((p['width_mm']+2*p['fit_offset_mm'],
            p['thickness_mm']+2*p['fit_offset_mm'],p['slot_depth_mm']),True).translate((0,0,p['slot_depth_mm']/2))
    def pose(n):
        m=np.eye(4);m[:3,:3]=data.xmat[ids[n]].reshape(3,3);m[:3,3]=data.xpos[ids[n]]*1000;return m
    trajectory=[]; exits={};peak=0.;last=[]
    steps=math.ceil(config['duration_seconds']/model.opt.timestep)
    stride=max(1,round(.02/model.opt.timestep))
    for k in range(steps+1):
        if k: mujoco.mj_step(model,data)
        if k%stride and k!=steps: continue
        current=data.xmat[root_id].reshape(3,3)
        tilt=math.degrees(math.acos(np.clip((current@initial.T)[2,2],-1,1)))
        peak=max(peak,tilt);joints=[]
        for c in report['connections']:
            tf,sf=frames[c['id']]
            rel=np.linalg.inv(pose(c['slot_part'])@sf)@pose(c['tab_part'])@tf
            tab=checked(tabs[c['id']].transform(rel[:3,:]));cavity=cavities[c['id']]
            inserted=checked(tab^cavity)
            bound=length_bound(solid_mesh(tab))*solid_mesh(cavity).area
            occupied=float(inserted.volume())
            # Shared TabSlot query geometry is measured in the simulated
            # relative pose. An AABB overlap alone cannot establish insertion.
            exited=bool(occupied<=bound)
            interval=None if inserted.is_empty() else np.asarray(inserted.bounding_box()).reshape(2,3)[:,2].tolist()
            if exited: exits.setdefault(c['id'],float(data.time))
            joints.append(dict(connection_id=c['id'],exited=exited,exit_axial_mm=float(-rel[2,3]),
                lateral_offset_mm=float(np.linalg.norm(rel[:2,3])),
                relative_angle_deg=math.degrees(math.acos(np.clip((np.trace(rel[:3,:3])-1)/2,-1,1))),
                insertion_volume_mm3=occupied,volume_uncertainty_mm3=bound,
                effective_insertion_interval_mm=interval,slot_frame_world_mm=(pose(c['slot_part'])@sf).tolist()))
        linear=max((float(np.linalg.norm(data.qvel[6*i:6*i+3])) for i in range(len(ids))),default=0)
        angular=max((float(np.linalg.norm(data.qvel[6*i+3:6*i+6])) for i in range(len(ids))),default=0)
        if data.time>=config['duration_seconds']-config['settle_window_seconds']:last.append((linear,angular))
        trajectory.append(dict(time_seconds=float(data.time),tilt_deg=tilt,linear_speed_m_s=linear,
            angular_speed_rad_s=angular,interfaces=joints,qpos=data.qpos.tolist()))
    np.savez(output/'trajectory.npz',time=np.array([r['time_seconds'] for r in trajectory]),
        qpos=np.array([r['qpos'] for r in trajectory],dtype=np.float64))
    write_json(output/'trajectory.json',trajectory)
    settled=bool(last) and max(v[0] for v in last)<=config['settle_linear_m_s'] and max(v[1] for v in last)<=config['settle_angular_rad_s']
    final=trajectory[-1]
    return dict(observation_duration_seconds=config['duration_seconds'],
        simulated_duration_seconds=round(float(data.time),10),
        assessment_time_seconds=round(final['time_seconds'],10),
        assessment_scope='Full gravity-only observation; final state at the end, first events are not early stops',
        final_tipped=final['tilt_deg']>config['tilt_threshold_deg'],
        final_exited_interfaces=[c['connection_id'] for c in final['interfaces'] if c['exited']],
        peak_tilt_deg=peak,final_tilt_deg=final['tilt_deg'],tipped=peak>config['tilt_threshold_deg'],
        exits=exits,settled=settled,final_interfaces=trajectory[-1]['interfaces'],
        final_linear_speed_m_s=last[-1][0],final_angular_speed_rad_s=last[-1][1],
        interface_retention_verified=True,
        interface_method='TabSlot solid intersection with declared insertion region at sampled simulated relative poses; no pullout-force claim'),trajectory


def save_frames(report,meshes,trajectory,output):
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
    indices=sorted({0,len(trajectory)-1,max(range(len(trajectory)),key=lambda i:trajectory[i]['tilt_deg']),
        next((i for i,r in enumerate(trajectory) if any(c['exited'] for c in r['interfaces'])),len(trajectory)-1)})
    paths=[]
    for i in indices:
        row=trajectory[i];q=np.asarray(row['qpos']).reshape(-1,7);placed=[]
        for j,mesh in enumerate(meshes.values()):
            rot=Rotation.from_quat(q[j,[4,5,6,3]]).as_matrix()
            placed.append((mesh.vertices@rot.T+q[j,:3]*1000)[mesh.faces])
        points=np.concatenate([t.reshape(-1,3) for t in placed]);lo=points.min(axis=0);hi=points.max(axis=0)
        centre=(lo+hi)/2;span=max(hi-lo)*.65
        fig=plt.figure(figsize=(7,6));ax=fig.add_subplot(projection='3d')
        for j,tri in enumerate(placed):
            ax.add_collection3d(Poly3DCollection(tri,alpha=.8,facecolor=plt.cm.tab10(j%10),linewidth=.05))
        xx,yy=np.meshgrid([centre[0]-span,centre[0]+span],[centre[1]-span,centre[1]+span])
        ax.plot_surface(xx,yy,np.zeros((2,2)),alpha=.15,color='gray')
        ax.set(xlim=(centre[0]-span,centre[0]+span),ylim=(centre[1]-span,centre[1]+span),
            zlim=(min(0,lo[2]),max(hi[2],1)*1.15),xlabel='X mm',ylabel='Y mm',zlabel='Z mm',
            title=f'MuJoCo self weight, t={row["time_seconds"]:.2f}s, tilt={row["tilt_deg"]:.1f} deg')
        path=output/f'simulation_{i:04d}.png';fig.savefig(path,dpi=120);plt.close(fig);paths.append(str(path))
    return paths


def analyze(args,physics):
    config=physics.get('standing',{});density=physics.get('material',{}).get('density_kg_m3')
    required=('duration_seconds','timestep_seconds','friction','tilt_threshold_deg','settle_window_seconds',
              'settle_linear_m_s','settle_angular_rad_s','coacd')
    if density is None or any(k not in config for k in required):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NEEDS_SPEC: density/contact/settling settings')
    if density<=0 or config['timestep_seconds']<=0 or not 0<config['settle_window_seconds']<=config['duration_seconds']:
        raise ValueError('invalid standing configuration')
    report,rows,meshes=load_parts(args)
    if not rows or any(r['status']!='PASS' for r in rows):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='DEPENDENCY_MESH_UNAVAILABLE: one connected volume per rigid print body required',metrics={'items':rows})
    from adsl.core.assembly_topology import mesh_solid,interface_measurement
    parts={p['id']:p for p in report['parts']};solids={n:mesh_solid(m) for n,m in meshes.items()}
    interfaces=[interface_measurement(c,parts,solids,report['mm_per_unit']) for c in report['connections']]
    if any(r['status']!='PASS' for r in interfaces):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='INTERFACE_GEOMETRY_UNVERIFIED',metrics={'items':interfaces})
    if any(c['parameters']['fit_offset_mm']<0 for c in report['connections']):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='RIGID_CONTACT_INTERFERENCE_UNSUPPORTED: elastic press fit not simulated')
    proxies={};checks={};proxy_solids={}
    for n,m in meshes.items():
        write_json(args.output/'stage.json',{'stage':'collision_decomposition','part_id':n,'status':'RUNNING'})
        proxies[n]=collision_proxies(m,config['coacd'])
        gaps=[c['parameters']['fit_offset_mm'] for c in report['connections'] if n in (c['tab_part'],c['slot_part'])]
        checks[n],proxy_solids[n]=verify_proxies(m,proxies[n],min(gaps) if gaps else None)
        for i,p in enumerate(proxies[n]): p.export(args.output/f'{n}.proxy_{i}.obj')
    proxy_interfaces=[interface_measurement(c,parts,proxy_solids,report['mm_per_unit']) for c in report['connections']]
    write_json(args.output/'collision_proxy_validation.json',{'parts':checks,'interfaces':proxy_interfaces})
    if not all(c['accepted'] for c in checks.values()) or any(r['status']!='PASS' for r in proxy_interfaces):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='COLLISION_PROXY_UNVERIFIED',metrics={'proxy_checks':checks,'items':proxy_interfaces})
    write_json(args.output/'stage.json',{'stage':'self_weight_simulation','status':'RUNNING'})
    xml=xml_model(report,meshes,proxies,config,density);(args.output/'model.xml').write_text(xml)
    result,trajectory=simulate(xml,report,config,args.output)
    findings=[]
    if result['tipped']:
        findings.append(finding(NAME,'SELF_WEIGHT_TIPPING',
            f"At {result['assessment_time_seconds']:g} s: root tilt {result['final_tilt_deg']:.4g} deg; "
            f"observation peak {result['peak_tilt_deg']:.4g} deg; limit {config['tilt_threshold_deg']}",
            part_ids=[report['root_id']],domain=result))
    for cid,t in result['exits'].items():
        c=next(c for c in report['connections'] if c['id']==cid)
        findings.append(finding(NAME,'SELF_WEIGHT_INTERFACE_EXIT',
            f'{cid}: at {result["assessment_time_seconds"]:g} s, '+
            ('still outside' if cid in result['final_exited_interfaces'] else 'back inside')+
            f' insertion region; first exit at {t:.4g} s (simulation continued)',
            part_ids=[c['tab_part'],c['slot_part']],domain={'connection_id':cid,'first_exit_seconds':t,
                'assessment_time_seconds':result['assessment_time_seconds'],
                'final_interface':next(i for i in result['final_interfaces'] if i['connection_id']==cid)}))
    capture_verified=result['interface_retention_verified']
    status='FAIL' if findings else 'PASS' if result['settled'] and capture_verified else 'INDETERMINATE'
    write_json(args.output/'stage.json',{'stage':'completed','status':status})
    result['simulation_frames']=save_frames(report,meshes,trajectory,args.output)
    return CheckerResult(checker=NAME,status=status,
        summary=f'After {result["assessment_time_seconds"]:g} s self-weight: final tilt {result["final_tilt_deg"]:.4g} deg; '
            f'{len(result["final_exited_interfaces"])} interfaces outside; '+
            ('settled' if result['settled'] else 'motion not settled')+f'; observation verdict {status}',
        metrics=result,findings=findings,assumptions={'uniform_density_kg_m3':density,'external_load_stability':'NOT_EVALUATED',
            'contact':'independent bodies, no weld; CoACD proxies verified locally by sampling/queries',
            'mujoco_version':importlib.metadata.version('mujoco'),'coacd_version':importlib.metadata.version('coacd'),
            'configuration':config},artifacts={'trajectory':str(args.output/'trajectory.json'),'simulation':str(args.output/'model.xml')})

if __name__=='__main__':cli(NAME,analyze)
