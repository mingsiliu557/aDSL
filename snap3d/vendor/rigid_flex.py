# Vendored from mingsiliu557/aDSL 9e1f01dc32812bf649dc2f58fdc027c7c3dd8d7d
# Source: adsl-agents/assembly_standing.py. Only the mm_matrix dependency import is relocated.
import math
import xml.etree.ElementTree as ET
import numpy as np


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
    from .assembly_topology import mm_matrix
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
