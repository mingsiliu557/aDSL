"""Independent selection runner: import/preview, optional one-call labels, shortlisted reference checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import asyncio
from collections import Counter
from dataclasses import replace, asdict
from pathlib import Path
import shutil
import sys
import time
from typing import Literal
from common import cache_key, config, dump, load, now, process, rows, sha, write_rows


def preflight(c, row, config_path):
    root=Path(c['root']); raw=root/row['raw_mesh']; case=row['case_id']
    output=root/'derived'/case; output.mkdir(parents=True,exist_ok=True)
    actual_sha=sha(raw)
    if row.get('raw_sha256') and row['raw_sha256']!=actual_sha: raise ValueError('RAW_ASSET_CHECKSUM_CHANGED')
    key=cache_key(c,actual_sha); done=output/'preflight.json'
    if done.exists():
        saved=load(done)
        if saved.get('cache_key')==key and all((root/p).is_file() and sha(root/p)==h for p,h in saved.get('files_sha256',{}).items()) and saved.get('status')=='PASS': return saved
    dump(output/'candidate.json',row); logs=root/'logs'/case
    worker=Path(__file__).with_name('reference_worker.py')
    imported=process([sys.executable,str(worker),'--config',str(config_path),'--stage','import','--row',str(output/'candidate.json'),'--output',str(output)],logs/'import',c['render']['timeout_seconds'],c)
    result=dict(case_id=case,cache_key=key,started_at=now(),import_process=imported,status='INDETERMINATE',files_sha256={})
    if imported['status']!='PASS':
        result['reason']='Import failed; original retained. See logs.';dump(done,result);return result
    result['basic']=load(output/'basic.json')
    for mode in ('native','neutral'):
        preview=root/'previews'/case/mode
        rendered=process([sys.executable,str(worker),'--config',str(config_path),'--stage','render','--glb',str(output/'reference.glb'),'--mode',mode,'--output',str(preview)],logs/mode,c['render']['timeout_seconds'],c)
        result[mode+'_process']=rendered
        if rendered['status']!='PASS': result['reason']='Render failed; no geometric verdict inferred.';dump(done,result);return result
    # Native fixed 60 degree three-quarter view, separate from neutral review views.
    input_image=root/'previews'/case/'input.png';shutil.copyfile(root/'previews'/case/'native/render_0002.png',input_image)
    result.update(status='PASS',input_image=str(input_image.relative_to(root)),reference_mesh=str((output/'reference.glb').relative_to(root)),use_pose=str((output/'use_pose.json').relative_to(root)))
    files=list(output.glob('*.json'))+list(output.glob('*.npz'))+list(output.glob('*.glb'))+list(output.glob('*.stl'))+list((root/'previews'/case).rglob('*.png'))+list((root/'previews'/case).rglob('meta.json'))
    result['files_sha256']={str(p.relative_to(root)):sha(p) for p in files if p!=done}
    dump(done,result);return result


async def label(c,row):
    from agents import Agent, Runner
    from pydantic import BaseModel
    from adsl.agents.utils.config import ModelProfile
    from adsl.agents.utils.inputs import user_input
    class Labels(BaseModel):
        tags:list[Literal['complex_surface','standing_sensitive','grouping_tradeoff','multipart_contact']]
        selection_note:str
        needs_manual_confirmation:bool
        pose_observation:str
    root=Path(c['root']); folder=root/'measurements'/row['case_id']/'vlm';folder.mkdir(parents=True,exist_ok=True)
    saved=folder/'attempt.json'
    # At most one call per candidate/input key, including unsuccessful attempts.
    key=cache_key(c,row['raw_sha256'])
    if saved.exists(): return load(saved)
    profile=replace(ModelProfile.load(c['vlm']['profile']),max_retries=0,timeout=c['vlm']['timeout_seconds'],max_tokens=2048)
    prompt='Screen this reference for a development benchmark. Return only a subset of complex_surface, standing_sensitive, grouping_tradeoff, multipart_contact. Curvature/section variation, support risk, possible split/merge tradeoff, and visually contacting functional parts respectively. Face/node count is not semantics. Tags are hypotheses, not physical results. Do not propose a decomposition or a connection graph. Ordinary examples may have no tags. State if use pose, missing/transparent parts or insufficient views need manual confirmation. Give one concise reason and a pose observation.'
    pictures=[root/row['input_image']]+sorted((root/'previews'/row['case_id']/'neutral').glob('*.png'))
    snapshots=folder/'images';snapshots.mkdir(exist_ok=True)
    captured=[]
    for i,picture in enumerate(pictures):
        path=snapshots/f'{i:02d}.png';shutil.copyfile(picture,path);captured.append(path)
    pictures=captured
    payload=user_input(__import__('json').dumps(dict(case_id=row['case_id'],category=row['category'],task='Four coarse tags only.')) ,tuple(pictures))
    dump(folder/'input.json',dict(case_id=row['case_id'],pictures=[dict(path=str(p.relative_to(root)),sha256=sha(p)) for p in pictures]))
    (folder/'system_prompt.txt').write_text(prompt)
    started=time.monotonic();result=dict(cache_key=key,status='UNAVAILABLE',model=profile.runtime_metadata(),called_at=now())
    dump(saved,result)
    try:
        response=await Runner.run(Agent(name='benchmark-selection',instructions=prompt,model=profile.agent_model(workspace=folder),model_settings=profile.model_settings(),output_type=Labels),input=payload,max_turns=1)
        result.update(status='PASS',output=response.final_output.model_dump(),usage=asdict(response.context_wrapper.usage))
        dump(folder/'messages.json',[item.to_input_item() for item in response.new_items])
    except Exception as e: result.update(reason=f'{type(e).__name__}: {str(e)[:300]}')
    result['elapsed_seconds']=time.monotonic()-started;dump(saved,result);return result


def measure(c,row,config_path):
    from adsl.agents.assembly_physics import checker_spec, run_assembly_checks
    from adsl.agents.utils.execution import ExecutionResult
    from common import cpu_env
    root=Path(c['root']); derived=root/'derived'/row['case_id']; output=root/'measurements'/row['case_id'];output.mkdir(parents=True,exist_ok=True)
    key=cache_key(c,row['raw_sha256']); done=output/'reference_measurement.json'
    if done.exists() and load(done).get('cache_key')==key:
        result=load(done)
        if all(Path(p).is_file() and sha(p)==h for p,h in result.get('files_sha256',{}).items()): return result
    basic=load(derived/'basic.json')
    if basic['status']!='PASS' or (basic.get('raw_sha256') and basic['raw_sha256']!=row['raw_sha256']):
        result=dict(cache_key=key,status='INDETERMINATE',reason='Reference volumes unavailable; no zero metrics substituted.',overhang=None,standing=None);dump(done,result);return result
    specs=[checker_spec('assembly_overhang',c['measurement_timeout_seconds'],prepend_environment={'PYTHONPATH':[c['mujoco_python_path']]})]
    if basic['standing_eligible']: specs.append(checker_spec('assembly_standing',c['measurement_timeout_seconds'],prepend_environment={'PYTHONPATH':[c['mujoco_python_path']]}))
    execution=ExecutionResult(derived,derived/'reference.glb',None,(),'','')
    # Existing checker outputs are immutable; retries get a fresh directory.
    attempt=0
    run_root=output/'runs'/f'{key}_{attempt:02d}'
    while run_root.exists():
        attempt+=1;run_root=output/'runs'/f'{key}_{attempt:02d}'
    runs=run_assembly_checks(specs,execution=execution,source=derived/'reference_source.json',root=run_root,physics=c['physics'])
    result=dict(cache_key=key,status='COMPLETED',scope='Whole reference, N=1; standing does not verify part retention.',run_root=str(run_root.relative_to(root)),standing=None,overhang=None,files_sha256={})
    for run in runs:
        name=run.spec.name.replace('assembly_','');dump(output/(name+'.json'),run.result.model_dump())
        result[name]=dict(status=run.result.status,summary=run.result.summary,report=str((output/(name+'.json')).relative_to(root)),metrics=run.result.metrics)
    if result['standing'] is None: result['standing']=dict(status='INDETERMINATE',reason=basic['standing_limitation'])
    for p in (derived/'use_pose.json',derived/'assembly_manifest.json',derived/'reference_source.json',derived/'reference_whole.stl',derived/'reference_whole.body.npz'):
        result['files_sha256'][str(p)]=sha(p)
    for run in runs:
        for p in run.output_dir.rglob('*.json'): result['files_sha256'][str(p)]=sha(p)
    dump(done,result);return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);p.add_argument('--limit',type=int);p.add_argument('--case');p.add_argument('--labels',action='store_true');p.add_argument('--measure',action='store_true');p.add_argument('--measure-only',action='store_true');p.add_argument('--workers',type=int,choices=[1,2],default=1)
    args=p.parse_args();c=config(args.config);path=Path(c['root'])/'manifests/candidates.jsonl'; candidates=rows(path)
    selected=[r for r in candidates if r.get('asset_status')=='downloaded' and (not args.case or r['case_id']==args.case)][:args.limit]
    def handle(original):
        row=dict(original)
        start=time.monotonic()
        try:
            result=load(Path(c['root'])/'derived'/row['case_id']/'preflight.json') if args.measure_only else preflight(c,row,args.config.resolve())
            row.update(preflight_status=result['status'])
            for k in ('input_image','reference_mesh','use_pose'): row[k]=result.get(k)
            row['geometry_status']=result.get('basic',{}).get('status','INDETERMINATE')
            if result['status']!='PASS' or row['geometry_status']!='PASS': row.update(status='needs_review',selection_note=result.get('reason','Reference volume is not measurable.'))
            if args.labels and c['vlm']['enabled'] and result['status']=='PASS':
                calls=len(list((Path(c['root'])/'measurements').glob('*/vlm/attempt.json')))
                if calls<c['vlm']['max_calls']:
                    labeled=asyncio.run(label(c,row));row['vlm_status']=labeled['status']
                    if labeled['status']=='PASS': row.update(tags=labeled['output']['tags'],selection_note=labeled['output']['selection_note'],automatic_review=labeled['output'])
            if args.measure: row['reference_measurement']=measure(c,row,args.config.resolve())
        except Exception as e: row.update(status='needs_review',preflight_status='INDETERMINATE',geometry_status='INDETERMINATE',input_image=None,reference_mesh=None,use_pose=None,selection_note=f'{type(e).__name__}: {str(e)[:300]}')
        print(row['case_id'],row.get('preflight_status'),row.get('geometry_status'),row.get('vlm_status'),f'{time.monotonic()-start:.1f}s',flush=True)

        return row
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for updated in pool.map(handle,selected):
            next(r for r in candidates if r['case_id']==updated['case_id']).update(updated)
            write_rows(path,candidates)
