"""File-based formula reimplementation; edge failures do not gate the case."""
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
from .connector import rebuild,ConnectorInfeasible
from .rollout import rollout,select_edges,relative_score
from .cem import optimize
from .vendor import mesh_solid,checked


def zero_gravity_review(result, *, D, config):
    """Appendix A: final-output diagnostic, never a CEM eligibility gate."""
    bound=config['gate']['zero_gravity_displacement_ratio']*D
    fixed=set(result.get('fixed_ids',[]));metrics=result.get('metrics',{})
    free={key:value.get('max_displacement_mm') for key,value in metrics.items() if key not in fixed}
    complete=result.get('status')=='PASS' and bool(metrics) and all(
        value is not None and np.isfinite(value) for value in free.values())
    valid=all(value<bound for value in free.values()) if complete else None
    return dict(status='PASS' if complete else 'UNAVAILABLE',valid=valid,
        validity='valid' if valid else ('invalid' if complete else 'unavailable'),
        displacement_bound_mm=bound,free_displacements_mm=free,
        reason=result.get('reason') if not complete else None)


def final_material_review(parts, result):
    """Record actual final-pose intersections; volume is not a soft-contact gate."""
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
        if not np.isfinite(volume):raise ValueError('NONFINITE_INTERSECTION_VOLUME')
        pairs.append(dict(a=a,b=b,intersection_volume_mm3=volume))
    return dict(status='PASS',affects_verdict=False,
        total_intersection_volume_mm3=sum(p['intersection_volume_mm3'] for p in pairs),pairs=pairs)


def paper_rollout(parts, *, D, config, fixed_ids=None, record=None,model_path=None):
    """One gravity objective call. Only final cross-part depth affects stability."""
    result=rollout(parts,D=D,config=config['rollout'],
        clearance_mm=config['connector']['clearance_ratio']*D,fixed_ids=fixed_ids,model_path=model_path)
    if result['status']=='PASS':
        depth=result['final_interpart_contact']['maximum_penetration_mm']
        if not np.isfinite(depth):raise ValueError('NONFINITE_FINAL_CONTACT_DEPTH')
        bound=D*config['rollout']['final_penetration_ratio']
        result['intersection_free']=bool(depth<=bound)
        result['final_penetration_bound_mm']=bound
        result['stable_motion']=result['stable']
        result['stable']=bool(result['stable_motion'] and result['intersection_free'])
    if record is not None:record('gravity',result)
    return result


def rollout_cost(probe, *, config, edge_count, rebuild_seconds=0.):
    """Projection from a completed timing probe; not a stability measurement."""
    if probe['status']!='PASS' or probe.get('step_count',0)<=0:raise ValueError('SPEED_PROBE_INCOMPLETE')
    per_step=probe['step_wall_seconds']/probe['step_count']
    initialization=probe['initialization_wall_seconds']
    overhead=max(0.,probe['wall_seconds']-probe['step_wall_seconds'])
    steps=int(round(config['rollout']['duration']/config['rollout']['dt']))
    single=overhead+per_step*steps
    evaluations=config['cem']['population']*config['cem']['generations']*edge_count
    return dict(flex_element_count=probe['flex_element_count'],measured_steps=probe['step_count'],
        mean_step_wall_seconds=per_step,initialization_wall_seconds=initialization,
        projected_single_rollout_seconds=single,optimization_edge_count=edge_count,
        nominal_evaluations=evaluations,projected_cem_rollout_seconds=single*evaluations,
        projected_cem_with_rebuild_seconds=(single+rebuild_seconds)*evaluations)


def evaluate_geometry(run, config, *, stage):
    run=Path(run);start=time.monotonic()
    metadata=json.loads((run/'case.json').read_text());D=metadata['D_mm']
    warnings=[]
    if metadata['config_sha256']!=digest(config):warnings.append('CONFIG_CHANGED')
    if metadata['code_identity']!=code_identity():warnings.append('CODE_CHANGED')
    stages=['repair','pge','cgr','rollout','cem','export']
    if stage not in stages:raise ValueError(stage)
    target=run/stage;target.mkdir(parents=True,exist_ok=True)
    try:
        if stage=='repair':
            raw=load_parts(run/'normalized_parts');records={};parts={}
            for i,m in raw.items():parts[i],records[i]=repair(m,D=D,config=config['repair'])
            export_parts(parts,target,metadata=records)
        elif stage=='pge':
            parts,records=pge(load_parts(run/'repair'),config=config['pge'],D=D)
            export_parts(parts,target,metadata=records)
        elif stage=='cgr':
            base=load_parts(run/'pge')
            contact_started=time.monotonic()
            edges,parameters,pairs=contact_graph(base,D=D,config=config['contact'],connector_config=config['connector'])
            contact_seconds=time.monotonic()-contact_started
            write_json(target/'contact.json',dict(edges=edges,parameters=parameters,pairs=pairs))
            rebuild_started=time.monotonic()
            parts,checks=rebuild(base,edges,parameters,D=D,config=config['connector'])
            rebuild_seconds=time.monotonic()-rebuild_started
            placed={c['edge'] for c in checks if c['status']=='PLACED'}
            write_json(target/'contact.json',dict(edges=[e for e in edges if e['id'] in placed],
                all_edges=edges,parameters=parameters,pairs=pairs,connectors=checks,
                timing=dict(contact_seconds=contact_seconds,rebuild_seconds=rebuild_seconds)))
            export_parts(parts,target,metadata=dict(checks=checks))
        elif stage in ('rollout','cem'):
            base=load_parts(run/'pge');contact=json.loads((run/'cgr/contact.json').read_text())
            edges=contact['edges'];parameters=contact['parameters'];parts=load_parts(run/'cgr')
            attempt=0;rollout_records=[]
            def simulate(p,fixed=None):
                nonlocal attempt
                index=attempt;attempt+=1
                def record(phase,result):
                    rollout_records.append(dict(attempt=index,phase=phase,status=result['status'],
                        reason=result.get('reason'),wall_seconds=result.get('wall_seconds'),
                        flex_element_count=result.get('flex_element_count'),
                        simulated_seconds=result.get('simulated_seconds'),stable=result.get('stable'),
                        final_interpart_contact=result.get('final_interpart_contact'),
                        final_material_check=result.get('final_material_check')))
                    write_json(target/'rollout_calls.json',rollout_records)
                result=paper_rollout(p,D=D,config=config,fixed_ids=fixed,record=record,model_path=run/'model.xml')
                if result['status']!='PASS':
                    write_json(target/f'rollout_error_{index:04d}.json',result)
                    raise RuntimeError('ROLLOUT_FAILED: '+str(result.get('reason')))
                return result
            if stage=='rollout':
                result=simulate(parts);write_json(target/'result.json',result)
            else:
                current=json.loads((run/'rollout/result.json').read_text())
                if current['status']!='PASS':raise RuntimeError('BASELINE_ROLLOUT_UNAVAILABLE')
                fixed=current['fixed_ids'];attempted=[];records=[]
                while not current['stable']:
                    eligible=select_edges(current,edges,D=D,config=config['rollout'],attempted=attempted)
                    if not eligible:break
                    eid=eligible[0];attempted.append(eid);e=next(e for e in edges if e['id']==eid)
                    baseline_global=current['global_score']
                    baseline_local=relative_score(current,e['a'],e['b'],D=D,config=config['rollout'])
                    candidate_records=[];edge_started=time.monotonic()
                    def evaluate(theta):
                        candidate=dict(parameters);candidate[eid]=np.asarray(theta).tolist()
                        try:
                            meshes,checks=rebuild(base,edges,candidate,D=D,config=config['connector'],strict_edge=eid)
                        except ConnectorInfeasible as error:
                            candidate_records.append(dict(theta=candidate[eid],status='FAIL',reason=str(error)))
                            write_json(target/f'candidates_{eid}.json',candidate_records)
                            return dict(status='FAIL',reason=str(error))
                        result=simulate(meshes,fixed)
                        candidate_records.append(dict(theta=candidate[eid],status='PASS',connectors=checks,
                            global_score=result['global_score'],stable=result['stable'],
                            rollout_wall_seconds=result['wall_seconds']))
                        write_json(target/f'candidates_{eid}.json',candidate_records)
                        return dict(status='PASS',local_score=relative_score(result,e['a'],e['b'],D=D,config=config['rollout']),
                                    global_score=result['global_score'],rollout=result)
                    opt=optimize(parameters[eid],D=D,config=config['cem'],evaluate=evaluate,
                                 baseline_global=baseline_global,seed=config['seed'])
                    best=opt['best'];adopted=best is not None and best['result']['local_score']<baseline_local
                    if adopted:
                        parameters[eid]=best['theta'];current=best['result']['rollout']
                        parts,_=rebuild(base,edges,parameters,D=D,config=config['connector'])
                    records.append(dict(edge=eid,adopted=adopted,history=opt['history'],evaluations=opt['evaluations'],
                                        baseline_global=baseline_global,elapsed_s=time.monotonic()-edge_started))
                    write_json(target/'optimization.json',records)
                export_parts(parts,target,metadata=dict(parameters=parameters,optimization=records))
                write_json(target/'result.json',current)
        else:
            parts=load_parts(run/'cem')
            export_parts(parts,target,metadata=dict(source_stage='cem'))
            current=json.loads((run/'cem/result.json').read_text())
            current['final_material_check']=final_material_review(parts,current)
            # Once, on the exact exported geometry, in its assembled input pose.
            exported=load_parts(target)
            zero=rollout(exported,D=D,config=dict(config['rollout'],gravity=0.),
                         clearance_mm=config['connector']['clearance_ratio']*D,
                         fixed_ids=current['fixed_ids'],retain_frames=False,model_path=run/'model.xml')
            review=zero_gravity_review(zero,D=D,config=config)
            write_json(target/'zero_gravity.json',zero)
            current=dict(current,zero_gravity_validity=review,zero_gravity_wall_seconds=zero.get('wall_seconds'))
            write_json(target/'result.json',current)
            if zero['status']!='PASS':raise RuntimeError('FINAL_ZERO_GRAVITY_FAILED: '+str(zero.get('reason')))
        write_json(target/'stage.json',dict(status='PASS',elapsed_s=time.monotonic()-start,
                   config_sha256=digest(config),code_identity=code_identity(),warnings=warnings))
    except Exception as error:
        write_json(target/'stage.json',dict(status='FAIL',reason=type(error).__name__,detail=str(error),
                   elapsed_s=time.monotonic()-start,warnings=warnings));raise


def initialize(run,whole,raw_parts,config):
    run=Path(run);parts=load_parts(raw_parts)
    assembly=trimesh.util.concatenate(list(parts.values()))
    _,T,D=normalize(assembly,config['scale_mm'])
    for m in parts.values():m.apply_transform(T)
    run.mkdir(parents=True,exist_ok=True);export_parts(parts,run/'normalized_parts')
    write_json(run/'case.json',dict(D_mm=D,transform_mm=T.tolist(),normalization_basis='raw_parts_assembly_bounds',whole_sha256=sha(whole),
               raw_parts_manifest_sha256=sha(Path(raw_parts)/'manifest.json'),config_sha256=digest(config),code_identity=code_identity()))


def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',default='configs/default.yaml')
    p.add_argument('--stage',required=True,choices=['initialize','repair','pge','cgr','rollout','cem','export']);p.add_argument('--whole');p.add_argument('--raw-parts')
    a=p.parse_args();cfg=yaml.safe_load(Path(a.config).read_text())
    if a.stage=='initialize':initialize(a.run,a.whole,a.raw_parts,cfg)
    else:evaluate_geometry(a.run,cfg,stage=a.stage)
if __name__=='__main__':main()
