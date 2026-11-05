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

def require_gate(path, config):
    gate=json.loads(Path(path).read_text())
    if gate['status']!='PASS' or not gate.get('next_stage_allowed'):raise ValueError('LOCK_GATE_NOT_PASSED')
    if gate['config_sha256']!=digest(config):raise ValueError('LOCK_GATE_CONFIG_MISMATCH')

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
            attempt=0
            def simulate(p,connectors,fixed=None):
                nonlocal attempt
                path=target/f'proxy_check_{attempt:04d}.json';attempt+=1
                try:
                    proxies,report=build_zone_proxies(base,connectors,D=D,config=config['coacd'],cache_dir=run/'proxy_cache')
                    verification=verify_zone_proxies(p,proxies,connectors,D=D,roles=report['blocks'],
                        rollout_config=config['rollout'],clearance_mm=config['connector']['clearance_ratio']*D,fixed_ids=fixed)
                    write_json(path,dict(build=report,verification=verification))
                    if verification['status']!='PASS':
                        return dict(status='UNAVAILABLE',reason='PROXY_UNVERIFIED',proxy_verification=verification)
                    result=rollout(p,D=D,config=config['rollout'],coacd_config=config['coacd'],
                        clearance_mm=config['connector']['clearance_ratio']*D,fixed_ids=fixed,proxy_cache=proxies)
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
    mesh=trimesh.load_mesh(whole);_,T,D=normalize(mesh,config['scale_mm'])
    parts=load_parts(raw_parts)
    for m in parts.values():m.apply_transform(T)
    run.mkdir(parents=True);export_parts(parts,run/'normalized_parts')
    write_json(run/'case.json',dict(D_mm=D,transform_mm=T.tolist(),whole_sha256=sha(whole),
               raw_parts_manifest_sha256=sha(Path(raw_parts)/'manifest.json'),config_sha256=digest(config),code_identity=code_identity()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',required=True);p.add_argument('--config',default='configs/default.yaml');p.add_argument('--gate',required=True)
    p.add_argument('--stage',required=True,choices=['initialize','repair','pge','cgr','rollout','cem','export']);p.add_argument('--whole');p.add_argument('--raw-parts')
    a=p.parse_args();cfg=yaml.safe_load(Path(a.config).read_text());require_gate(a.gate,cfg)
    if a.stage=='initialize':initialize(a.run,a.whole,a.raw_parts,cfg)
    else:evaluate_geometry(a.run,cfg,stage=a.stage)
if __name__=='__main__':main()
