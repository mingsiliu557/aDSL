"""Independent reference screening with separate preview, measurement and image-label caches."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Literal
from pydantic import BaseModel
from common import (cache_descriptor, cache_key, config, dump, load, now, process,
                    rows, sha, valid_files, write_rows)


class Labels(BaseModel):
    tags: list[Literal['complex_surface','standing_sensitive','grouping_tradeoff','multipart_contact']]
    selection_note: str
    needs_manual_confirmation: bool
    pose_observation: str
    appearance_input_usable: bool | None = None
    standing_applicable: bool | None = None


LABEL_PROMPT = ('Screen this reference for a development benchmark. Return only a subset of '
    'complex_surface, standing_sensitive, grouping_tradeoff, multipart_contact: curvature/section variation, '
    'support risk, possible split/merge tradeoff, and visually contacting functional parts respectively. '
    'Face/node count is not semantics. Tags are hypotheses, not physical results. Do not propose a '
    'decomposition or connection graph. Ordinary examples may have no tags. Confirm appearance_input_usable '
    'only if the target is complete, recognizable and sufficiently visible with usable native materials. '
    'Set standing_applicable true only for a natural independent ground-standing use; false for hanging, '
    'wall-mounted, externally supported or airborne objects; null when uncertain. State whether use pose, '
    'missing/transparent parts or insufficient views need manual confirmation. Give one concise reason and '
    'a pose observation. No label is a measured physical PASS.')


def _resources(c, row):
    root=Path(c['root']); result={}
    for name, digest in (row.get('resource_sha256') or {}).items():
        path=root/name
        if not path.is_file() or sha(path)!=digest: raise ValueError('RESOURCE_CHECKSUM_CHANGED:'+name)
        result[name]=digest
    return result


def _preview_key(c,row):
    raw=Path(c['root'])/row['raw_mesh']
    actual=sha(raw)
    if row.get('raw_sha256') and actual!=row['raw_sha256']: raise ValueError('RAW_ASSET_CHECKSUM_CHANGED')
    inputs=dict(resources=_resources(c,row))
    return cache_key(c,actual,inputs=inputs),cache_descriptor(c,actual,inputs=inputs)


def _legacy(c,row,stage,path):
    """Only migrate a historical result whose source, outputs and stage semantics were proven equal."""
    evidence=Path(c['root'])/'logs/revision_20261006/legacy_cache_evidence.json'
    if not evidence.is_file() or not path.is_file(): return None
    try:
        saved=load(path)
        proofs=load(evidence).get('records',[])
        proof=next((r for r in proofs if r.get('case_id')==row['case_id'] and r.get('stage')==stage
                    and r.get('record_sha256')==sha(path)),None)
        if not proof or not proof.get('eligible_for_stage_migration'): return None
        descriptor=cache_descriptor(c,row.get('raw_sha256'),stage=stage)
        previous=proof.get('stage_descriptor') or {}
        if any(previous.get(k)!=descriptor[k] for k in ('settings','code','dependency_versions')): return None
        if (proof.get('raw_source') or {}).get('recorded_sha256')!=row.get('raw_sha256') or not valid_files(saved.get('files_sha256'),c['root']): return None
        if stage=='preflight' and (saved.get('status')!='PASS' or _resources(c,row)): return None
        if stage=='measurement' and saved.get('status')!='COMPLETED': return None
        return dict(saved,migration=dict(evidence=str(evidence.relative_to(c['root'])),
            original_record=str(path.relative_to(c['root'])),original_record_sha256=sha(path),
            matched_git_commits=proof.get('matched_git_commits'),verified_at=now()))
    except (OSError,ValueError,KeyError,TypeError): return None


def validated_preflight(c,row):
    try:
        key,_=_preview_key(c,row)
        folder=Path(c['root'])/'derived'/row['case_id']; path=folder/'preflight_v2.json'
        if path.is_file():
            saved=load(path)
            if saved.get('cache_key')==key and valid_files(saved.get('files_sha256'),c['root']): return saved
        return _legacy(c,row,'preflight',folder/'preflight.json')
    except (OSError,ValueError,KeyError): return None


def preflight(c,row,config_path):
    key,descriptor=_preview_key(c,row)
    root=Path(c['root']); case=row['case_id']; output=root/'derived'/case
    output.mkdir(parents=True,exist_ok=True); done=output/'preflight_v2.json'
    saved=validated_preflight(c,row)
    if saved:
        if saved.get('migration') and not done.exists():
            saved=dict(saved,cache_key=key,cache_descriptor=descriptor)
            # Candidate/category/cache headers are not geometry dependencies.
            saved['files_sha256']={p:h for p,h in saved['files_sha256'].items()
                if Path(p).name not in ('candidate.json','preflight.json','preflight_v2.json')}
            dump(done,saved)
        return saved
    dump(output/'candidate.json',row)
    logs=root/'logs'/case/'runs'/f'{key}_{time.time_ns()}'
    worker=Path(__file__).with_name('reference_worker.py')
    imported=process([sys.executable,str(worker),'--config',str(config_path),'--stage','import',
        '--row',str(output/'candidate.json'),'--output',str(output)],logs/'import',c['render']['timeout_seconds'],c)
    result=dict(case_id=case,cache_key=key,cache_descriptor=descriptor,started_at=now(),
                import_process=imported,status='INDETERMINATE',files_sha256={})
    if imported['status']!='PASS':
        result['reason']='Import failed; original retained. See logs.'; dump(done,result); return result
    result['basic']=load(output/'basic.json')
    for mode in ('native','neutral'):
        preview=root/'previews'/case/mode
        rendered=process([sys.executable,str(worker),'--config',str(config_path),'--stage','render',
            '--glb',str(output/'reference.glb'),'--mode',mode,'--output',str(preview)],
            logs/mode,c['render']['timeout_seconds'],c)
        result[mode+'_process']=rendered
        if rendered['status']!='PASS':
            result['reason']='Render failed; no geometric verdict inferred.'; dump(done,result); return result
    picture=root/'previews'/case/'input.png'
    shutil.copyfile(root/'previews'/case/'native/render_0002.png',picture)
    result.update(status='PASS',input_image=str(picture.relative_to(root)),
        reference_mesh=str((output/'reference.glb').relative_to(root)),use_pose=str((output/'use_pose.json').relative_to(root)))
    files=[p for p in output.iterdir() if p.suffix in ('.json','.npz','.glb','.stl')
           and p.name not in ('candidate.json','preflight.json','preflight_v2.json')]
    files+=list((root/'previews'/case).rglob('*.png'))+list((root/'previews'/case).rglob('meta.json'))
    result['files_sha256']={str(p.relative_to(root)):sha(p) for p in files}
    dump(done,result); return result


def _label_inputs(c,row):
    import yaml
    root=Path(c['root'])
    pictures=[root/row['input_image']]+sorted((root/'previews'/row['case_id']/'neutral').glob('render_*.png'))
    if len(pictures)!=9: raise ValueError('LABEL_REQUIRES_INPUT_AND_EIGHT_VIEWS')
    # Credentials are never read for a cache lookup and are never written into the descriptor.
    profile=yaml.safe_load(Path(c['vlm']['profile']).read_text())
    params={k:v for k,v in profile['params'].items() if k not in ('api_key','token')}
    params.update(timeout=c['vlm']['timeout_seconds'],max_retries=0,max_tokens=2048)
    model=dict(provider=profile['provider'],api=profile['api'],params=params,
               credential_source=next(iter(profile.get('credential',{})),None))
    text=dict(case_id=row['case_id'],category=row.get('category'),task='Four coarse tags and input/use-pose applicability only.')
    inputs=dict(pictures_sha256=[sha(p) for p in pictures],text=text,model=model,
                system_prompt=LABEL_PROMPT,output_schema=Labels.model_json_schema(),schema_version=2)
    return pictures,inputs


def current_label(c,row):
    try:
        _,inputs=_label_inputs(c,row)
        key=cache_key(c,stage='label',inputs=inputs)
        folder=Path(c['root'])/'measurements'/row['case_id']/'vlm/attempts'/key
        if not (folder/'attempt.json').is_file(): return None
        saved=load(folder/'attempt.json')
        if saved.get('cache_key')==key and saved.get('input_descriptor')==inputs and valid_files(saved.get('input_files_sha256'),c['root']):
            return dict(saved,cache_current=True)
    except (OSError,ValueError,KeyError,TypeError): pass
    return None


def vlm_attempt_count(c):
    root=Path(c['root'])/'measurements'; identities=set()
    for pattern in ('*/vlm/attempt.json','*/vlm/attempts/*/attempt.json'):
        for path in root.glob(pattern):
            saved=load(path)
            if not saved.get('called_at'): continue
            case=path.relative_to(root).parts[0]
            identities.add((case,saved.get('attempt_id') or saved['called_at']))
    return len(identities)


async def _label_unlocked(c,row):
    cached=current_label(c,row)
    if cached is not None: return cached
    # Lookups remain possible after the global new-call budget is exhausted.
    if vlm_attempt_count(c)>=c['vlm']['max_calls']:
        return dict(status='NOT_CALLED',reason='Cumulative VLM attempt budget exhausted.')
    from agents import Agent, Runner
    from adsl.agents.utils.config import ModelProfile
    from adsl.agents.utils.inputs import user_input
    pictures,inputs=_label_inputs(c,row); key=cache_key(c,stage='label',inputs=inputs)
    root=Path(c['root']); folder=root/'measurements'/row['case_id']/'vlm/attempts'/key
    if (folder/'attempt.json').exists():
        return dict(status='UNAVAILABLE',reason='Existing same-key attempt has invalid evidence; preserved without another call.')
    folder.mkdir(parents=True,exist_ok=True); captured=[]
    for i,picture in enumerate(pictures):
        path=folder/'images'/f'{i:02d}.png'; path.parent.mkdir(exist_ok=True)
        shutil.copyfile(picture,path); captured.append(path)
    dump(folder/'input.json',dict(text=inputs['text'],pictures=[dict(path=str(p.relative_to(root)),sha256=sha(p)) for p in captured]))
    (folder/'system_prompt.txt').write_text(LABEL_PROMPT)
    profile=replace(ModelProfile.load(c['vlm']['profile']),max_retries=0,timeout=c['vlm']['timeout_seconds'],max_tokens=2048)
    payload=user_input(json.dumps(inputs['text']),tuple(captured))
    started=time.monotonic()
    result=dict(case_id=row['case_id'],attempt_id=f'{row["case_id"]}:{time.time_ns()}',cache_key=key,
        status='UNAVAILABLE',model=profile.runtime_metadata(),input_descriptor=inputs,called_at=now(),
        input_files_sha256={str(p.relative_to(root)):sha(p) for p in captured})
    dump(folder/'attempt.json',result)
    try:
        response=await Runner.run(Agent(name='benchmark-selection',instructions=LABEL_PROMPT,
            model=profile.agent_model(workspace=folder),model_settings=profile.model_settings(),output_type=Labels),
            input=payload,max_turns=1)
        result.update(status='PASS',output=response.final_output.model_dump(),usage=asdict(response.context_wrapper.usage))
        dump(folder/'messages.json',[item.to_input_item() for item in response.new_items])
    except Exception as error: result['reason']=f'{type(error).__name__}: {str(error)[:300]}'
    result['elapsed_seconds']=time.monotonic()-started; dump(folder/'attempt.json',result); return result


async def label(c,row):
    # Also serialize separate CLI invocations, keeping the shared attempt budget atomic.
    import fcntl
    lock=Path(c['root'])/'measurements/.vlm_call.lock'
    lock.parent.mkdir(parents=True,exist_ok=True)
    with lock.open('a') as handle:
        fcntl.flock(handle,fcntl.LOCK_EX)
        try: return await _label_unlocked(c,row)
        finally: fcntl.flock(handle,fcntl.LOCK_UN)


def _standing_request(c,row):
    automatic=(current_label(c,row) or {}).get('output') or {}
    manual=row.get('manual_review') or {}
    applicable=manual.get('standing_applicable',automatic.get('standing_applicable'))
    confirmed=manual.get('pose_confirmed',manual.get('use_pose_confirmed'))
    if confirmed is None: confirmed=bool(automatic.get('pose_observation','').strip() and automatic.get('needs_manual_confirmation') is False)
    return dict(applicable=applicable,pose_confirmed=bool(confirmed))


def _measurement_inputs(c,row):
    root=Path(c['root']); derived=root/'derived'/row['case_id']
    names=('use_pose.json','assembly_manifest.json','reference_source.json','reference_whole.stl','reference_whole.body.npz')
    return dict(files={name:sha(derived/name) for name in names},standing_request=_standing_request(c,row))


def validated_measurement(c,row):
    try:
        inputs=_measurement_inputs(c,row); key=cache_key(c,row['raw_sha256'],stage='measurement',inputs=inputs)
        folder=Path(c['root'])/'measurements'/row['case_id']; path=folder/'reference_measurement_v2.json'
        if path.is_file():
            saved=load(path)
            if saved.get('cache_key')==key and valid_files(saved.get('files_sha256'),c['root']): return saved
        return _legacy(c,row,'measurement',folder/'reference_measurement.json')
    except (OSError,ValueError,KeyError): return None


def measure(c,row,config_path):
    from adsl.agents.assembly_physics import checker_spec, run_assembly_checks
    from adsl.agents.utils.execution import ExecutionResult
    root=Path(c['root']); derived=root/'derived'/row['case_id']; output=root/'measurements'/row['case_id']
    output.mkdir(parents=True,exist_ok=True); done=output/'reference_measurement_v2.json'
    saved=validated_measurement(c,row)
    if saved:
        if saved.get('migration') and not done.exists():
            inputs=_measurement_inputs(c,row)
            saved=dict(saved,cache_key=cache_key(c,row['raw_sha256'],stage='measurement',inputs=inputs),
                       cache_descriptor=cache_descriptor(c,row['raw_sha256'],stage='measurement',inputs=inputs))
            dump(done,saved)
        return saved
    basic=load(derived/'basic.json')
    if basic['status']!='PASS' or (basic.get('raw_sha256') and basic['raw_sha256']!=row['raw_sha256']):
        result=dict(status='INDETERMINATE',reason='Reference volumes unavailable; no zero metrics substituted.',overhang=None,standing=None)
        dump(done,result);return result
    inputs=_measurement_inputs(c,row);key=cache_key(c,row['raw_sha256'],stage='measurement',inputs=inputs)
    specs=[checker_spec('assembly_overhang',c['measurement_timeout_seconds'],prepend_environment={'PYTHONPATH':[c['mujoco_python_path']]})]
    # New runs need an independently standing use and an explicitly reviewed pose.
    request=inputs['standing_request']
    if basic['standing_eligible'] and request['applicable'] is True and request['pose_confirmed']:
        specs.append(checker_spec('assembly_standing',c['measurement_timeout_seconds'],prepend_environment={'PYTHONPATH':[c['mujoco_python_path']]}))
    execution=ExecutionResult(derived,derived/'reference.glb',None,(),'','')
    run_root=output/'runs'/f'{key}_{time.time_ns()}'
    runs=run_assembly_checks(specs,execution=execution,source=derived/'reference_source.json',root=run_root,physics=c['physics'])
    result=dict(cache_key=key,cache_descriptor=cache_descriptor(c,row['raw_sha256'],stage='measurement',inputs=inputs),
        status='COMPLETED',scope='Whole reference, N=1; ground standing requires separate use-pose applicability review and does not verify part retention.',
        run_root=str(run_root.relative_to(root)),standing=None,overhang=None,files_sha256={})
    for run in runs:
        name=run.spec.name.replace('assembly_',''); report=run_root/(name+'.json'); dump(report,run.result.model_dump())
        result[name]=dict(status=run.result.status,summary=run.result.summary,report=str(report.relative_to(root)),metrics=run.result.metrics)
    if result['standing'] is None: result['standing']=dict(status='NOT_APPLICABLE' if request['applicable'] is False else 'INDETERMINATE',reason=basic.get('standing_limitation') or 'Free-standing applicability or use pose not confirmed; simulation not executed.')
    for name in inputs['files']: result['files_sha256'][str((derived/name).relative_to(root))]=inputs['files'][name]
    for path in run_root.rglob('*.json'):result['files_sha256'][str(path.relative_to(root))]=sha(path)
    dump(done,result);return result


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--config',type=Path,required=True);parser.add_argument('--limit',type=int)
    parser.add_argument('--case');parser.add_argument('--source',choices=['ABO','Toys4K'])
    parser.add_argument('--labels',action='store_true');parser.add_argument('--measure',action='store_true')
    parser.add_argument('--measure-only',action='store_true');parser.add_argument('--workers',type=int,choices=[1,2],default=1)
    args=parser.parse_args()
    if args.labels and args.workers!=1: parser.error('VLM labels require --workers 1 to enforce the cumulative attempt budget')
    c=config(args.config);path=Path(c['root'])/'manifests/candidates.jsonl'; candidates=rows(path)
    selected=[r for r in candidates if r.get('asset_status')=='downloaded' and (not args.case or r['case_id']==args.case)
              and (not args.source or r['source']==args.source)][:args.limit]
    def handle(original):
        row=dict(original);start=time.monotonic()
        try:
            result=validated_preflight(c,row) if args.measure_only else preflight(c,row,args.config.resolve())
            if not result: raise ValueError('CURRENT_PREFLIGHT_UNAVAILABLE')
            row.update(preflight_status=result['status'])
            for key in ('input_image','reference_mesh','use_pose'): row[key]=result.get(key)
            row['geometry_status']=result.get('basic',{}).get('status','INDETERMINATE')
            if result['status']!='PASS' and row.get('status') not in ('excluded','user_confirmed_dev','user_confirmed'):
                row.update(status='needs_review',selection_note=result.get('reason','Preview unavailable.'))
            if args.labels and c['vlm']['enabled'] and result['status']=='PASS':
                labeled=asyncio.run(label(c,row));row['vlm_status']=labeled['status']
                if labeled['status']=='PASS':
                    row.setdefault('historical_tags',row.get('tags',[]))
                    row.update(tags=labeled['output']['tags'],selection_note=labeled['output']['selection_note'],automatic_review=labeled['output'],vlm_cache_key=labeled['cache_key'])
            if args.measure: row['reference_measurement']=measure(c,row,args.config.resolve())
        except Exception as error:
            if row.get('status') not in ('excluded','user_confirmed_dev','user_confirmed'):row['status']='needs_review'
            row.update(preflight_status='INDETERMINATE',selection_note=f'{type(error).__name__}: {str(error)[:300]}')
        print(row['case_id'],row.get('preflight_status'),row.get('geometry_status'),row.get('vlm_status'),f'{time.monotonic()-start:.1f}s',flush=True)
        return row
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for updated in pool.map(handle,selected):
            next(r for r in candidates if r['case_id']==updated['case_id']).update(updated)
            write_rows(path,candidates)


if __name__=='__main__': main()
