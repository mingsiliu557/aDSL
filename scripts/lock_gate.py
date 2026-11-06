"""D=150 artificial lock gates, with backend-specific collision construction."""
from pathlib import Path
import argparse
import hashlib
import json
import time

import manifold3d as mf
import numpy as np
import yaml

from snap3d.connector import shapes
from snap3d.vendor import solid_mesh, checked
from snap3d.rollout import rollout
from snap3d.common import write_json, digest, sha

LABELS = [('zero_gravity', True, 0.), ('no_head', False, 9.81), ('headed', True, 9.81)]


def fixture(headed):
    D = 150.
    scale = D / float(np.linalg.norm([18., 18., 24.]))
    r = .006 * D
    length = 5 * D / 256
    receiver = mf.Manifold.cube([18, 18, 12]).translate([-9, -9, 12])
    for x in [-8, 6]:
        receiver = receiver + mf.Manifold.cube([2, 18, 12]).translate([x, -9, 0])
    donor = mf.Manifold.cube([5, 5, 4]).translate([-2.5, -2.5, 8])
    receiver = checked(receiver.scale([scale]*3))
    donor = checked(donor.scale([scale]*3))
    transform = np.eye(4)
    transform[2, 3] = 12 * scale
    peg, slot = shapes(radius=r, length=length, D=D, headed=headed)
    peg = checked(peg.transform(transform[:3]))
    slot = checked(slot.transform(transform[:3]))
    base = {'receiver': solid_mesh(receiver), 'peg': solid_mesh(donor)}
    parts = {'receiver': solid_mesh(checked(receiver-slot)), 'peg': solid_mesh(checked(donor+peg))}
    record = dict(edge='lock', donor='peg', receiver='receiver', T=transform.tolist(),
                  radius=r, length=length, clearance=.25*D/256, overhang=.75*D/256,
                  head_thickness=D/256, headed=headed)
    return parts, base, record, D, length


def coplanar_boxes():
    """Two plain boxes, a fixed lower body and a free upper body; D=150."""
    scale=150/float(np.linalg.norm([18.,18.,16.]))
    bottom=mf.Manifold.cube([18,18,12]).translate([-9,-9,0]).scale([scale]*3)
    top=mf.Manifold.cube([5,5,4]).translate([-2.5,-2.5,12]).scale([scale]*3)
    return {'receiver':solid_mesh(checked(bottom)),'peg':solid_mesh(checked(top))}


def first_ten_steps(result, dt):
    frames=result.get('frames',[])
    if not frames:return dict(available_steps=0,requested_steps=10,complete=False,steps=[])
    initial=frames[0]['peg']['position_mm'];rows=[]
    for index,frame in enumerate(frames[1:11],1):
        pose=frame['peg'];velocity=pose.get('linear_velocity_world_m_s')
        rows.append(dict(step=index,time_s=index*dt,
            displacement_xyz_mm=[float(x-y) for x,y in zip(pose['position_mm'],initial)],
            linear_velocity_xyz_m_s=velocity,
            speed_m_s=None if velocity is None else float(np.linalg.norm(velocity))))
    speeds=[r['speed_m_s'] for r in rows if r['speed_m_s'] is not None]
    return dict(initial_position_mm=initial,available_steps=len(rows),requested_steps=10,complete=len(rows)==10,
        max_speed_first_ten_m_s=max(speeds) if speeds else None,
        max_speed_whole_rollout_m_s=result.get('max_linear_speed_m_s'),steps=rows)


def reuse_zero_gravity(manifest_path, config):
    """Validate frozen v5 files, simulator and fixture before reusing its trace."""
    root=Path(__file__).resolve().parents[1]
    manifest_path=Path(manifest_path).resolve()
    identity=json.loads(manifest_path.read_text())
    previous=yaml.safe_load(identity['config_yaml'])
    current={key:value for key,value in config.items() if key!='gate'}
    if digest(previous)!=identity['config_sha256'] or previous!=current:
        raise ValueError('REUSE_SIMULATION_CONFIG_MISMATCH')
    directory=Path(identity['run_directory'])
    required={str(directory/name) for name in [
        'gate.json','zero_gravity/result.json','zero_gravity/model.xml',
        'zero_gravity/connector.json','zero_gravity/peg.stl','zero_gravity/receiver.stl']}
    required.update(str(path.relative_to(root)) for path in (root/'snap3d').rglob('*.py'))
    if not required.issubset(identity['files']):raise ValueError('REUSE_IDENTITY_INCOMPLETE')
    for name,expected in identity['files'].items():
        path=(root/name).resolve()
        if not path.is_relative_to(root) or not path.is_file() or sha(path)!=expected:
            raise ValueError('REUSE_HASH_MISMATCH: '+name)
    folder=root/directory
    previous_gate=json.loads((folder/'gate.json').read_text())
    if previous_gate['config_sha256']!=identity['config_sha256']:
        raise ValueError('REUSE_GATE_CONFIG_MISMATCH')
    parts,_,record,D,_=fixture(True)
    if record!=json.loads((folder/'zero_gravity/connector.json').read_text()) or D!=previous_gate['fixture_diagonal_mm']:
        raise ValueError('REUSE_FIXTURE_MISMATCH')
    for name,mesh in parts.items():
        if hashlib.sha256(mesh.export(file_type='stl')).hexdigest()!=sha(folder/f'zero_gravity/{name}.stl'):
            raise ValueError('REUSE_FIXTURE_MISMATCH: '+name)
    result=json.loads((folder/'zero_gravity/result.json').read_text())
    reference=dict(manifest_path=str(manifest_path.relative_to(root)),manifest_sha256=sha(manifest_path),
        result_path=str(directory/'zero_gravity/result.json'),result_sha256=sha(folder/'zero_gravity/result.json'),
        previous_gate_pass=result.get('gate_pass'),simulation_rerun=False,
        previous_config_sha256=identity['config_sha256'])
    return result,previous_gate,reference


def gate_verdict(label, result, D, length, clearance, config):
    measured=result.get('metrics',{})
    fixed=measured.get('receiver',{}).get('max_displacement_mm')
    motion=measured.get('peg',{}).get('max_displacement_mm')
    frames=result.get('frames',[])
    drop=None if not frames else frames[-1]['peg']['position_mm'][2]-frames[0]['peg']['position_mm'][2]
    ok=result['status']=='PASS' and fixed is not None and fixed<=1e-4*D
    if label=='zero_gravity':
        ok=ok and motion is not None and motion<config['gate']['zero_gravity_displacement_ratio']*D
    elif label=='no_head':ok=ok and drop is not None and drop < -length
    else:ok=ok and drop is not None and motion is not None and drop > -2*clearance and motion<length
    return bool(ok),fixed,motion,drop


def rigid_flex_gate(out, config, *, reuse_manifest=None):
    tests=[]
    reused=None if reuse_manifest is None else reuse_zero_gravity(reuse_manifest,config)
    def integrate(parts,folder,cfg,clearance,normal_only=False):
        folder.mkdir()
        for part,mesh in parts.items():mesh.export(folder/f'{part}.stl')
        started=time.monotonic()
        try:
            result=rollout(parts,D=150.,config=cfg,
                clearance_mm=clearance,fixed_ids=['receiver'],normal_only_diagnostic=normal_only,model_path=folder/'model.xml')
        except (ValueError,RuntimeError) as error:
            result=dict(status='UNAVAILABLE',reason=type(error).__name__,detail=str(error))
        elapsed=time.monotonic()-started
        result['rollout_wall_seconds']=elapsed
        return result,elapsed

    for label,headed,gravity in LABELS:
        parts,_,record,D,length=fixture(headed)
        cfg=dict(config['rollout'],gravity=gravity)
        if label=='zero_gravity' and reused is not None:
            result=reused[0]
            elapsed=result['rollout_wall_seconds']
            (out/label).mkdir()
            write_json(out/label/'reused.json',reused[2])
        else:
            result,elapsed=integrate(parts,out/label,cfg,record['clearance'])
        write_json(out/label/'connector.json',record)
        ok,fixed,motion,drop=gate_verdict(label,result,D,length,record['clearance'],config)
        result['gate_pass']=bool(ok)
        write_json(out/label/'result.json',result)
        tests.append(dict(test=label,pass_=bool(ok),status=result['status'],reason=result.get('reason'),
            rollout_wall_seconds=elapsed,requested_duration_s=config['rollout']['duration'],simulated_seconds=result.get('simulated_seconds'),
            flex_element_count=result.get('flex_element_count'),flex_element_counts=result.get('flex_element_counts'),
            initial_contact=result.get('initial_contact'),initial_contact_count=result.get('initial_contact_count'),
            max_contact_count=result.get('max_contact_count'),final_contact_count=result.get('final_contact_count'),
            fixed_displacement_mm=fixed,peg_displacement_mm=motion,peg_final_delta_z_mm=drop,
            length_mm=length,clearance_mm=record['clearance'],friction=cfg['friction'],
            max_linear_speed_m_s=result.get('max_linear_speed_m_s'),
            zero_gravity_bound_mm=config['gate']['zero_gravity_displacement_ratio']*D if label=='zero_gravity' else None,
            fixed_displacement_bound_mm=1e-4*D,
            reused=label=='zero_gravity' and reused is not None))
        print(json.dumps(tests[-1]),flush=True)
        if not ok:break

    if reused is not None:
        control=reused[1]['coplanar_control']
        normal_control=reused[1]['normal_only_control']
    else:
        # Always repeat the v4 plain-box control with the aligned production settings.
        clearance=config['connector']['clearance_ratio']*150.
        cfg=dict(config['rollout'],gravity=0.)
        diagnostic,wall=integrate(coplanar_boxes(),out/'coplanar_boxes_zero_gravity',cfg,clearance)
        write_json(out/'coplanar_boxes_zero_gravity/result.json',diagnostic)
        def control_record(result,elapsed,normal_only):
            return dict(status=result['status'],reason=result.get('reason'),rollout_wall_seconds=elapsed,
                simulated_seconds=result.get('simulated_seconds'),flex_element_count=result.get('flex_element_count'),
                initial_contact=result.get('initial_contact'),initial_contact_count=result.get('initial_contact_count'),
                max_contact_count=result.get('max_contact_count'),final_contact_count=result.get('final_contact_count'),
                metrics=result.get('metrics'),connector_count=0,diagnostic_only=True,
                condim=1 if normal_only else 3,friction=[0.,0.,0.] if normal_only else config['rollout']['friction'],
                first_ten_steps=first_ten_steps(result,config['rollout']['dt']))
        control=control_record(diagnostic,wall,False)
        print(json.dumps(dict(control='coplanar_boxes_zero_gravity',**control)),flush=True)
        normal_control=None
        zero=tests[0]
        if (zero['test']=='zero_gravity' and not zero['pass_'] and
                (zero['status']=='PASS' or zero['reason']=='SIMULATION_ERROR')):
            cfg=dict(config['rollout'],gravity=0.,friction=[0.,0.,0.])
            result,elapsed=integrate(coplanar_boxes(),out/'coplanar_boxes_condim1_zero_friction',cfg,clearance,True)
            write_json(out/'coplanar_boxes_condim1_zero_friction/result.json',result)
            normal_control=control_record(result,elapsed,True)
            print(json.dumps(dict(control='coplanar_boxes_condim1_zero_friction',**normal_control)),flush=True)
    report=dict(status='PASS' if len(tests)==3 and all(t['pass_'] for t in tests) else 'FAIL',
        collision_backend='rigid_flex',config_sha256=digest(config),fixture_diagonal_mm=150.,
        tests=tests,coplanar_control=control,normal_only_control=normal_control,
        reused_zero_gravity=None if reused is None else reused[2],controls_reused=reused is not None,
        next_stage_allowed=len(tests)==3 and all(t['pass_'] for t in tests),
        not_run=[label for label,_,_ in LABELS if label not in {t['test'] for t in tests}],
        initial_penetration_is_diagnostic_only=True,settings_source='aDSL fabrication_ablation@9e1f01d selection_v1 standing',
        uses_ipc=False)
    write_json(out/'gate.json',report)
    return report


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--reuse-zero-gravity', metavar='MANIFEST', help='Hash-bound saved v5 trace; rigid_flex only')
    args = parser.parse_args()
    out = Path(args.output)
    out.mkdir(parents=True, exist_ok=False)
    config = yaml.safe_load(Path('configs/default.yaml').read_text())
    report=rigid_flex_gate(out,config,reuse_manifest=args.reuse_zero_gravity)
    raise SystemExit(0 if report['next_stage_allowed'] else 2)


if __name__ == '__main__':
    main()
