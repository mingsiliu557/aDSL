"""First mandatory gate; no generation or CEM on failure."""
from pathlib import Path
import argparse,json,time
import numpy as np
import manifold3d as mf
import yaml
from snap3d.connector import shapes
from snap3d.vendor import solid_mesh,checked,collision_proxies
from snap3d.rollout import rollout
from snap3d.common import write_json,digest

def fixture(headed):
    D=float(np.linalg.norm([18.,18.,24.])); r=.006*D; length=5*D/256
    peg,slot=shapes(radius=r,length=length,D=D,headed=headed)
    # Receiver has its underside at z=12 and two feet reaching the ground.
    # Peg hangs below this underside: gravity is the axial withdrawal load.
    receiver=mf.Manifold.cube([18,18,12]).translate([-9,-9,12])
    for x in [-8,6]: receiver=receiver+mf.Manifold.cube([2,18,12]).translate([x,-9,0])
    donor=mf.Manifold.cube([5,5,4]).translate([-2.5,-2.5,8])
    peg=peg.translate([0,0,12]); slot=slot.translate([0,0,12])
    return {'receiver':solid_mesh(checked(receiver-slot)),'peg':solid_mesh(checked(donor+peg))},D,length

def _proxy_child(conn, mesh, options):
    try: conn.send(('PASS',collision_proxies(mesh,options)))
    except Exception as error: conn.send(('ERROR',(type(error).__name__,str(error))))
    finally: conn.close()

def bounded_proxies(mesh, options, seconds=300):
    import multiprocessing as mp
    context=mp.get_context('spawn');parent,child=context.Pipe(duplex=False)
    process=context.Process(target=_proxy_child,args=(child,mesh,options));process.start();child.close()
    try:
        if not parent.poll(seconds):
            process.terminate();process.join(10)
            if process.is_alive():process.kill();process.join()
            raise RuntimeError(f'COACD_TIMEOUT: {seconds}s per part; precision unchanged')
        status,value=parent.recv();process.join()
        if status!='PASS':raise RuntimeError(f'COACD_ERROR: {value}')
        return value
    finally:
        parent.close()
        if process.is_alive():process.terminate();process.join()

def main():
    parser=argparse.ArgumentParser(); parser.add_argument('--output',required=True); args=parser.parse_args()
    out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    config=yaml.safe_load(Path('configs/default.yaml').read_text()); rows=[]
    for label,headed,gravity in [('zero_gravity',True,0.),('no_head',False,9.81),('headed',True,9.81)]:
        parts,D,length=fixture(headed); folder=out/label;folder.mkdir()
        for i,m in parts.items(): m.export(folder/f'{i}.stl')
        start=time.monotonic(); proxies={}
        try:
            for i,m in parts.items():
                proxies[i]=bounded_proxies(m,dict(config['coacd'],threshold_mm=(.25*D/256)/4))
                for k,p in enumerate(proxies[i]): p.export(folder/f'{i}_proxy_{k}.stl')
            cfg=dict(config['rollout'],friction=[0.,0.,0.])
            result=rollout(parts,D=D,config=cfg,coacd_config=config['coacd'],clearance_mm=.25*D/256,
                           fixed_ids=['receiver'],gravity=gravity,proxy_cache=proxies)
            if 'xml' in result: (folder/'model.xml').write_text(result.pop('xml'))
            measured=result.get('metrics',{}); fixed=measured.get('receiver',{}).get('max_displacement_mm',float('inf'))
            motion=measured.get('peg',{}).get('max_displacement_mm',float('inf'))
            final_z=result.get('frames',[{}])[-1].get('peg',{}).get('position_mm',[None,None,None])[2]
            initial_z=result.get('frames',[{}])[0].get('peg',{}).get('position_mm',[None,None,None])[2]
            if final_z is not None: final_z-=initial_z
            ok=result['status']=='PASS' and fixed<=1e-4*D
            if label=='zero_gravity': ok=ok and motion<1e-4*D
            elif label=='no_head': ok=ok and final_z is not None and final_z < -length
            else: ok=ok and final_z is not None and final_z > -2*(.25*D/256) and motion<length
            result['gate_pass']=bool(ok)
        except (ValueError,RuntimeError) as e:
            result=dict(status='UNAVAILABLE',reason=type(e).__name__,detail=str(e),gate_pass=False)
        write_json(folder/'result.json',result)
        rows.append(dict(test=label,pass_=result['gate_pass'],elapsed_s=time.monotonic()-start,status=result['status'],reason=result.get('reason')))
        print(json.dumps(rows[-1]),flush=True)
        if not result['gate_pass']: break
    report=dict(status='PASS' if len(rows)==3 and all(r['pass_'] for r in rows) else 'FAIL',tests=rows,config_sha256=digest(config),
                next_stage_allowed=len(rows)==3 and all(r['pass_'] for r in rows),not_run=[x for x in ['zero_gravity','no_head','headed'] if x not in [r['test'] for r in rows]],coacd_timeout_s_per_part=300)
    write_json(out/'gate.json',report)
    raise SystemExit(0 if report['next_stage_allowed'] else 2)
if __name__=='__main__':main()
