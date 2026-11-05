"""File-based stage runner. Real generation is gated by artificial lock tests."""
from pathlib import Path
import argparse,json,time
import numpy as np
import trimesh
import yaml
from .common import normalize,write_json,sha,digest,code_identity
from .export import export_parts,load_parts
from .repair import repair
from .pge import pge
from .contact import contact_graph
from .connector import rebuild
from .rollout import rollout,select_edges,relative_score
from .cem import optimize
from .proxies import build_zone_proxies, verify_zone_proxies
from .vendor import mesh_solid,checked

def require_gate(path, config):
    gate=json.loads(Path(path).read_text())
    if gate['status']!='PASS' or not gate.get('next_stage_allowed'):raise ValueError('LOCK_GATE_NOT_PASSED')
    if gate['config_sha256']!=digest(config):raise ValueError('LOCK_GATE_CONFIG_MISMATCH')

def zero_gravity_review(result, *, D, config):
    """Paper Appendix A: every free part must move strictly less than half a cell."""
    bound=config['gate']['zero_gravity_displacement_ratio']*D
    fixed=set(result.get('fixed_ids',[]));metrics=result.get('metrics',{})
    free={key:value.get('max_displacement_mm') for key,value in metrics.items() if key not in fixed}
    complete=result.get('status')=='PASS' and bool(free) and all(
        value is not None and np.isfinite(value) for value in free.values())
    return dict(status='PASS' if complete else 'UNAVAILABLE',
        valid=all(value<bound for value in free.values()) if complete else None,
        displacement_bound_mm=bound,free_displacements_mm=free,
        reason=result.get('reason') if not complete else None)


def final_material_review(parts, result):
    """Appendix B: test actual final material, independently of shell contacts."""
    import itertools
    frames=result.get('frames',[])
    if not frames:raise ValueError('FINAL_MATERIAL_POSES_MISSING')
    solids={}
    for key,mesh in parts.items():
        start=frames[0][key];end=frames[-1][key]
        rotation=np.asarray(end['rotation'])@np.asarray(start['rotation']).T
        transform=np.eye(4);transform[:3,:3]=rotation
        transform[:3,3]=np.asarray(end['position_mm'])-rotation@np.asarray(start['position_mm'])
        moved=mesh.copy();moved.apply_transform(transform)
        solids[key]=mesh_solid(moved)
    pairs=[]
    for a,b in itertools.combinations(sorted(solids),2):
        volume=float(checked(solids[a]^solids[b]).volume())
        pairs.append(dict(a=a,b=b,intersection_volume_mm3=volume))
    return dict(status='PASS',intersection_free=all(p['intersection_volume_mm3']<=0 for p in pairs),pairs=pairs)


def paper_rollout(parts, *, D, config, coacd_config, fixed_ids=None, proxy_cache=None, record=None):
    """The zero-gravity validity check precedes each gravity objective call."""
    kwargs=dict(D=D,coacd_config=coacd_config,
        clearance_mm=config['connector']['clearance_ratio']*D,
        fixed_ids=fixed_ids,proxy_cache=proxy_cache)
    zero=rollout(parts,config=dict(config['rollout'],gravity=0.),retain_frames=False,**kwargs)
    check=zero_gravity_review(zero,D=D,config=config)
    zero['validity_check']=check
    if record is not None:record('zero_gravity',zero)
    if check['status']!='PASS' or not check['valid']:
        return dict(status='UNAVAILABLE',reason='ZERO_GRAVITY_INVALID',zero_gravity=check,
            fixed_ids=zero.get('fixed_ids',[]),flex_element_count=zero.get('flex_element_count'))
    kwargs['fixed_ids']=zero['fixed_ids']
    result=rollout(parts,config=config['rollout'],**kwargs)
    result['zero_gravity']=check
    if result['status']=='PASS':
        try:geometry=final_material_review(parts,result)
        except (ValueError,RuntimeError) as error:
            geometry=dict(status='UNAVAILABLE',intersection_free=None,reason=str(error))
        result['final_material_check']=geometry
        result['stable_motion']=result['stable']
        result['stable']=bool(result['stable'] and geometry['status']=='PASS' and geometry['intersection_free'])
    if record is not None:record('gravity',result)
    return result


def evaluate_geometry(run, config, *, stage):
    run=Path(run);start=time.monotonic()
    metadata=json.loads((run/'case.json').read_text());D=metadata['D_mm']
    if metadata['config_sha256']!=digest(config):raise ValueError('CONFIG_CHANGED')
    if metadata['code_identity']!=code_identity():raise ValueError('CODE_CHANGED')
    stages=['repair','pge','cgr','rollout','cem','export']
    if stage not in stages:raise ValueError(stage)
    predecessor={'pge':'repair','cgr':'pge','rollout':'cgr','cem':'rollout','export':'cem'}.get(stage)
    if predecessor:
        previous=run/predecessor/'stage.json'
        if not previous.exists() or json.loads(previous.read_text())['status']!='PASS':
            raise ValueError('PREVIOUS_STAGE_NOT_PASSED')
    target=run/stage
    if target.exists():raise ValueError('STAGE_EXISTS_USE_NEW_RUN_OR_NEXT_STAGE')
    target.mkdir()
    try:
        if stage=='repair':
            raw=load_parts(run/'normalized_parts');records={};parts={}
            for i,m in raw.items():parts[i],records[i]=repair(m,D=D,config=config['repair'])
            export_parts(parts,target,metadata=records)
        elif stage=='pge':
            parts,records=pge(load_parts(run/'repair'),config=config['pge']);export_parts(parts,target,metadata=records)
        elif stage=='cgr':
            base=load_parts(run/'pge');edges,parameters,pairs=contact_graph(base,D=D,config=config['contact'],connector_config=config['connector'])
            write_json(target/'contact.json',dict(edges=edges,parameters=parameters,pairs=pairs))
            parts,checks=rebuild(base,edges,parameters,D=D,config=config['connector'])
            write_json(target/'contact.json',dict(edges=edges,parameters=parameters,pairs=pairs,connectors=checks))
            export_parts(parts,target,metadata=dict(checks=checks))
        elif stage in ('rollout','cem'):
            base=load_parts(run/'pge');contact=json.loads((run/'cgr/contact.json').read_text())
            edges=contact['edges'];parameters=contact['parameters'];parts=load_parts(run/'cgr')
            attempt=0;rollout_records=[]
            def simulate(p,connectors,fixed=None):
                nonlocal attempt
                path=target/f'proxy_check_{attempt:04d}.json';attempt+=1
                def record(phase,result):
                    if phase=='zero_gravity':write_json(target/f'zero_gravity_{attempt-1:04d}.json',result)
                    rollout_records.append(dict(attempt=attempt-1,phase=phase,status=result['status'],
                        reason=result.get('reason'),wall_seconds=result.get('wall_seconds'),
                        flex_element_count=result.get('flex_element_count'),
                        simulated_seconds=result.get('simulated_seconds'),
                        validity_check=result.get('validity_check'),
                        final_material_check=result.get('final_material_check')))
                    write_json(target/'rollout_calls.json',rollout_records)
                try:
                    if config['rollout'].get('collision_backend','rigid_flex')=='rigid_flex':
                        result=paper_rollout(p,D=D,config=config,coacd_config=config['coacd'],
                            fixed_ids=fixed,record=record)
                        write_json(path,dict(collision_backend='rigid_flex',proxy_construction=False,
                            initial_contact=result.get('initial_contact'),flex_element_count=result.get('flex_element_count'),
                            status=result['status'],reason=result.get('reason')))
                        result['collision_evidence_ref']=path.name
                        return result
                    proxies,report=build_zone_proxies(base,connectors,D=D,config=config['coacd'],cache_dir=run/'proxy_cache')
                    verification=verify_zone_proxies(p,proxies,connectors,D=D,roles=report['blocks'],
                        rollout_config=config['rollout'],clearance_mm=config['connector']['clearance_ratio']*D,fixed_ids=fixed)
                    write_json(path,dict(build=report,verification=verification))
                    if verification['status']!='PASS':
                        return dict(status='UNAVAILABLE',reason='PROXY_UNVERIFIED',proxy_verification=verification)
                    result=paper_rollout(p,D=D,config=config,coacd_config=config['coacd'],
                        fixed_ids=fixed,proxy_cache=proxies,record=record)
                    result['proxy_evidence_ref']=path.name
                    return result
                except (ValueError,RuntimeError) as error:
                    reason=str(error).split(':',1)[0]
                    write_json(path,dict(status='UNAVAILABLE',reason=reason,detail=str(error)))
                    return dict(status='UNAVAILABLE',reason=reason,detail=str(error))
            if stage=='rollout':
                result=simulate(parts,contact['connectors']);write_json(target/'result.json',result)
                if result['status']!='PASS':raise ValueError(result['reason'])
            else:
                current=json.loads((run/'rollout/result.json').read_text())
                if current['status']!='PASS':raise ValueError('BASELINE_ROLLOUT_UNAVAILABLE')
                fixed=current['fixed_ids'];attempted=[];records=[]
                while not current['stable']:
                    eligible=select_edges(current,edges,D=D,config=config['rollout'],attempted=attempted)
                    if not eligible:break
                    eid=eligible[0];attempted.append(eid);e=next(e for e in edges if e['id']==eid)
                    baseline_global=current['global_score'];baseline_local=relative_score(current,e['a'],e['b'],D=D,config=config['rollout'])
                    def evaluate(theta):
                        candidate=dict(parameters);candidate[eid]=np.asarray(theta).tolist()
                        try:
                            meshes,checks=rebuild(base,edges,candidate,D=D,config=config['connector'])
                            result=simulate(meshes,checks,fixed)
                        except (ValueError,RuntimeError) as error:return dict(status='FAIL',reason=str(error))
                        if result['status']!='PASS':return dict(status='FAIL',reason=result['reason'])
                        return dict(status='PASS',local_score=relative_score(result,e['a'],e['b'],D=D,config=config['rollout']),global_score=result['global_score'],rollout=result)
                    opt=optimize(parameters[eid],D=D,config=config['cem'],evaluate=evaluate,baseline_global=baseline_global,seed=config['seed'])
                    best=opt['best'];adopted=best is not None and best['result']['local_score']<baseline_local
                    if adopted:
                        parameters[eid]=best['theta'];current=best['result']['rollout'];parts,_=rebuild(base,edges,parameters,D=D,config=config['connector'])
                    records.append(dict(edge=eid,adopted=adopted,history=opt['history'],evaluations=opt['evaluations'],baseline_global=baseline_global))
                export_parts(parts,target,metadata=dict(parameters=parameters,optimization=records))
                write_json(target/'result.json',current)
        else:
            previous=run/'cem'
            if not previous.exists():previous=run/'cgr'
            export_parts(load_parts(previous),target,metadata=dict(source_stage=previous.name))
        write_json(target/'stage.json',dict(status='PASS',elapsed_s=time.monotonic()-start,config_sha256=digest(config)))
    except Exception as error:
        write_json(target/'stage.json',dict(status='FAIL',reason=type(error).__name__,detail=str(error),elapsed_s=time.monotonic()-start));raise

def initialize(run,whole,raw_parts,config):
    run=Path(run)
    if run.exists():raise ValueError('RUN_EXISTS')
    parts=load_parts(raw_parts)
    # Completion can extend beyond the whole-mesh bounds. Freeze the transform
    # and D from the actual assembled parts, before any repair/PGE/candidate.
    assembly=trimesh.util.concatenate(list(parts.values()))
    _,T,D=normalize(assembly,config['scale_mm'])
    for m in parts.values():m.apply_transform(T)
    run.mkdir(parents=True);export_parts(parts,run/'normalized_parts')
    write_json(run/'case.json',dict(D_mm=D,transform_mm=T.tolist(),normalization_basis='raw_parts_assembly_bounds',whole_sha256=sha(whole),
               raw_parts_manifest_sha256=sha(Path(raw_parts)/'manifest.json'),config_sha256=digest(config),code_identity=code_identity()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',default='configs/default.yaml');p.add_argument('--gate',required=True)
    p.add_argument('--stage',required=True,choices=['initialize','repair','pge','cgr','rollout','cem','export']);p.add_argument('--whole');p.add_argument('--raw-parts')
    a=p.parse_args();cfg=yaml.safe_load(Path(a.config).read_text());require_gate(a.gate,cfg)
    if a.stage=='initialize':initialize(a.run,a.whole,a.raw_parts,cfg)
    else:evaluate_geometry(a.run,cfg,stage=a.stage)
if __name__=='__main__':main()
