"""One fixed CPU batch: resume stages, per-case four-hour deadlines, plain CSV/JSON."""
import argparse,concurrent.futures,copy,csv,datetime,hashlib,json,math,os,resource,signal,subprocess,sys,time,threading
from pathlib import Path

STOP_EVENT=threading.Event()
STAGES=['initialize','repair','simplify','pge','cgr','rollout','cem','export']

def read(path,default=None):
    try:return json.loads(Path(path).read_text())
    except FileNotFoundError:return default
    except json.JSONDecodeError:
        if default is not None:return default
        raise

def write(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.tmp')
    tmp.write_text(json.dumps(value,indent=2,sort_keys=True,allow_nan=False)+'\n');tmp.replace(path)

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def immutable_config(config):
    result=copy.deepcopy(config);result['cem'].pop('workers',None);return result

def concurrency_plan(peak_rss_bytes):
    if not isinstance(peak_rss_bytes,int) or peak_rss_bytes<=0:raise ValueError('INVALID_PEAK_RSS')
    maximum=min(30,(26*2**30)//peak_rss_bytes)
    if maximum<1:raise ValueError('SINGLE_ROLLOUT_EXCEEDS_26_GIB')
    # Eight candidates fill one wave. Prefer few case parents over idle tails.
    workers=min(8,maximum);cases=max(1,maximum//workers);total=cases*workers
    return dict(measured_peak_rss_bytes=peak_rss_bytes,simulation_budget_bytes=26*2**30,
        maximum_allowed_simulations=maximum,case_workers=cases,cem_workers=workers,total_simulation_workers=total,
        projected_simulation_rss_bytes=total*peak_rss_bytes,hard_process_cap=30,
        reason='full eight-candidate waves where possible; leave remainder for preparation instead of extra case parents')

def public_reason(value,root):
    text=str(value).replace(str(Path(root).resolve()),'.')
    # Reports retain a cause, but no server/home path or account identity.
    import re
    return re.sub(r'/(?:home|scratch|tmp)/[^\s\"\']+', '<local-path>',text)[:600]

def verify_manifest(directory):
    manifest=read(Path(directory)/'manifest.json')
    if manifest is None:raise ValueError('STAGE_MANIFEST_MISSING')
    for row in manifest['parts']:
        if sha(Path(directory)/row['stl'])!=row['stl_sha256']:raise ValueError('STAGE_ASSET_HASH_MISMATCH')
    if sha(Path(directory)/manifest['assembly_glb'])!=manifest['assembly_sha256']:raise ValueError('STAGE_ASSEMBLY_HASH_MISMATCH')

def stage_complete(run,stage):
    result=read(run/stage/'stage.json',{})
    if result.get('status')!='PASS':return False
    if stage in ['repair','simplify','pge','cgr','cem','export']:verify_manifest(run/stage)
    if stage in ['rollout','export'] and read(run/stage/'result.json',{}).get('status')!='PASS':raise ValueError('STAGE_OBSERVATION_MISSING')
    if stage=='initialize':verify_manifest(run/'normalized_parts')
    return True

def run_case(root,case_id,stop_after='export',profile_baseline=False,affinity=None):
    if affinity:os.sched_setaffinity(0,{int(x) for x in affinity.split(',')})
    import yaml
    from snap3d.pipeline import initialize,evaluate_geometry,paper_rollout
    from snap3d.export import load_parts
    from snap3d.common import code_identity,digest
    root=Path(root).resolve();config=yaml.safe_load((root/'run_config.yaml').read_text());entry=next(r for r in read(root/'inputs.json')['cases'] if r['case_id']==case_id)
    run=root/'cases'/case_id/'assembly';run.mkdir(parents=True,exist_ok=True)
    source=(root/entry['source_directory']).resolve();identity=dict(whole_sha256=sha(source/'generation/whole.ply'),raw_parts_manifest_sha256=sha(source/'decomposition/manifest.json'),config=immutable_config(config))
    if identity['whole_sha256']!=entry['whole_sha256'] or identity['raw_parts_manifest_sha256']!=entry['decomposition_manifest_sha256']:raise ValueError('SOURCE_ASSET_CHANGED')
    previous=read(run/'input_identity.json')
    if previous is not None and previous!=identity:raise ValueError('CASE_INPUT_OR_CONFIG_CHANGED')
    write(run/'input_identity.json',identity)
    if profile_baseline:
        start=time.monotonic();result=paper_rollout(load_parts(run/'cgr'),D=read(run/'case.json')['D_mm'],config=config,model_path=run/'model.xml')
        elapsed=time.monotonic()-start;peak=int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)*1024
        write(run/'rollout/result.json',result)
        write(run/'rollout/resource.json',dict(peak_rss_bytes=peak,wall_seconds=elapsed,memory=config['rollout']['memory'],
            initialization_wall_seconds=result.get('initialization_wall_seconds'),step_wall_seconds=result.get('step_wall_seconds'),
            flex_element_count=result.get('flex_element_count'),status=result['status']))
        write(run/'rollout/stage.json',dict(status='PASS' if result['status']=='PASS' else 'FAIL',elapsed_s=elapsed,
            reason=result.get('reason'),config_sha256=digest(config),code_identity=code_identity()))
        if result['status']!='PASS':raise RuntimeError('CALIBRATION_ROLLOUT_FAILED: '+str(result.get('reason')))
        return
    for stage in STAGES[:STAGES.index(stop_after)+1]:
        if stage_complete(run,stage):continue
        target=run/stage
        if stage=='rollout' and (run/'model.xml').exists():
            archive=run/'interrupted'/f'model_{time.time_ns()}.xml';archive.parent.mkdir(exist_ok=True);(run/'model.xml').rename(archive)
        if target.exists() and any(target.iterdir()):
            archive=run/'interrupted'/f'{stage}_{time.time_ns()}';archive.parent.mkdir(exist_ok=True);target.rename(archive)
        write(run/'current_stage.json',dict(stage=stage,started_at=time.time()))
        started=time.monotonic()
        print(case_id,'START',stage,flush=True)
        if stage=='initialize':
            initialize(run,source/'generation/whole.ply',source/'decomposition',config)
            write(run/'initialize/stage.json',dict(status='PASS',elapsed_s=time.monotonic()-started))
        else:evaluate_geometry(run,config,stage=stage)
        print(case_id,'PASS',stage,'seconds',time.monotonic()-started,flush=True)


def case_summary(root,case_id,state):
    run=Path(root)/'cases'/case_id/'assembly';row=dict(case_id=case_id,status=state.get('status','PENDING'),wall_seconds=state.get('wall_seconds',0.),reason=state.get('reason'))
    for stage in STAGES:
        record=read(run/stage/'stage.json',{})
        row[stage+'_status']=record.get('status','NOT_RUN');row[stage+'_seconds']=record.get('elapsed_s')
    for stage in ['simplify','pge']:
        metadata=read(run/stage/'manifest.json',{}).get('metadata',{})
        if stage=='simplify':
            for key in ['total_faces_before','total_faces_after','within_budget','fallback_parts']:row['simplify_'+key]=metadata.get(key)
        else:
            for key in ['retained_cut_count','rolled_back_cut_count','residual_overlap_volume_mm3','residual_overlap_volume_cells']:row['pge_'+key]=metadata.get(key)
    contact=read(run/'cgr/contact.json',{});connectors=contact.get('connectors',[])
    row['connectors_placed']=sum(c['status']=='PLACED' for c in connectors) if contact else None
    row['connectors_skipped']=(sum(c['status']=='SKIPPED' for c in connectors)+sum(p.get('connector_status')=='SKIPPED' for p in contact.get('pairs',[]))) if contact else None
    for label,path in [('baseline',run/'rollout/result.json'),('final',run/'export/result.json')]:
        result=read(path,{})
        if not result and label=='final':result=read(run/'cem/result.json',{})
        metrics=result.get('metrics',{})
        row[label+'_max_displacement_mm']=max((m['max_displacement_mm'] for m in metrics.values()),default=None)
        row[label+'_max_rotation_deg']=max((math.degrees(m['max_rotation_rad']) for m in metrics.values()),default=None)
        row[label+'_stable']=result.get('stable')
    final=read(run/'export/result.json',{});row['zero_gravity_valid']=final.get('zero_gravity_validity',{}).get('valid')
    row['zero_gravity_status']=final.get('zero_gravity_validity',{}).get('status')
    if state.get('status')=='TIMEOUT':
        stage=read(run/'current_stage.json',{}).get('stage')
        if stage and row.get(stage+'_status')!='PASS':row[stage+'_status']='TIMEOUT'
    elif state.get('status')=='FAILED':
        stage=read(run/'current_stage.json',{}).get('stage')
        if stage and row.get(stage+'_status')!='PASS':row[stage+'_status']='FAIL'
    return row


def terminate_group(process):
    for sig in [signal.SIGTERM,signal.SIGKILL]:
        try:os.killpg(process.pid,sig)
        except ProcessLookupError:pass
        if sig==signal.SIGTERM:time.sleep(1.)
    process.wait()


def execute_case(root,entry,affinity,stop_after='export',profile_baseline=False):
    root=Path(root);cid=entry['case_id'];directory=root/'cases'/cid;directory.mkdir(parents=True,exist_ok=True)
    state_path=directory/'case_status.json';previous=read(state_path,{})
    if previous.get('status') in ['COMPLETE','FAILED','TIMEOUT'] and not profile_baseline:
        if previous['status']=='COMPLETE':verify_manifest(directory/'assembly/export')
        return previous
    prior=float(previous.get('wall_seconds',0.));limit=4*3600.;remaining=limit-prior
    state=dict(status='RUNNING',case_id=cid,wall_seconds=prior,wall_time_limit_seconds=limit)
    if remaining<=0:state.update(status='TIMEOUT',reason='CASE_WALL_TIME_LIMIT');write(state_path,state);return state
    cmd=[sys.executable,str(Path(__file__).resolve()),'--root',str(root),'--case-id',cid,'--stop-after',stop_after,'--affinity',','.join(map(str,affinity))]
    if profile_baseline:cmd+=['--profile-baseline']
    started=time.monotonic()
    with (directory/'case.log').open('a') as log:
        process=subprocess.Popen(cmd,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        while True:
            code=process.poll();elapsed=time.monotonic()-started
            state['wall_seconds']=prior+elapsed;write(state_path,state)
            if code is not None:
                state['status']='COMPLETE' if code==0 and stop_after=='export' and not profile_baseline else ('PREPARED' if code==0 else 'FAILED')
                if code:
                    failure=read(directory/'case_error.json',{})
                    state['reason']=failure.get('reason',f'CASE_PROCESS_EXIT_{code}')
                break
            if STOP_EVENT.is_set():
                terminate_group(process);state.update(status='INTERRUPTED',reason='BATCH_INTERRUPTED');break
            if elapsed>=remaining:
                terminate_group(process);state.update(status='TIMEOUT',reason='CASE_WALL_TIME_LIMIT');break
            time.sleep(1.)
    write(state_path,state);write(directory/'summary.json',case_summary(root,cid,state));return state


def summarize(root):
    root=Path(root);entries=read(root/'inputs.json')['cases'];rows=[]
    for entry in entries:
        directory=root/'cases'/entry['case_id'];state=read(directory/'case_status.json',{})
        row=read(directory/'summary.json') if state.get('status') in ['COMPLETE','FAILED','TIMEOUT'] else None
        if row is None:row=case_summary(root,entry['case_id'],state)
        rows.append(row)
    fields=list(rows[0]);temporary=root/'summary_table.tmp'
    with temporary.open('w') as f:
        writer=csv.DictWriter(f,fieldnames=fields);writer.writeheader()
        for row in rows:writer.writerow({k:json.dumps(v) if isinstance(v,list) else v for k,v in row.items()})
    temporary.replace(root/'summary_table.csv')
    terminal=sum(r['status'] in ['COMPLETE','FAILED','TIMEOUT'] for r in rows)
    successes=sum(r['status']=='COMPLETE' and r['final_stable'] is True for r in rows)
    result=dict(case_count=len(rows),terminal_count=terminal,complete_count=sum(r['status']=='COMPLETE' for r in rows),
        failed_count=sum(r['status']=='FAILED' for r in rows),timeout_count=sum(r['status']=='TIMEOUT' for r in rows),
        stable_complete_count=successes,stable_fraction=successes/len(rows) if terminal==len(rows) else None,
        zero_gravity_valid_count=sum(r['status']=='COMPLETE' and r['zero_gravity_valid'] is True for r in rows),
        unfinished=terminal<len(rows),case_wall_seconds_sum=sum(r['wall_seconds'] for r in rows),
        batch_active_wall_seconds=read(root/'batch_clock.json',{}).get('wall_seconds'),
        campaign_active_wall_seconds=read(root/'batch_clock.json',{}).get('wall_seconds',0.)+read(root/'concurrency.json',{}).get('preflight_wall_seconds',0.),concurrency=read(root/'concurrency.json'))
    write(root/'summary.json',result);write(root/'summary_rows.json',rows)
    report='''# CPU batch with budget-only mesh simplification

## 与论文不同之处

| 项目 | 当前实现 |
| --- | --- |
| 仿真器 | MuJoCo rigid_flex 替代 IPC |
| 最终穿透判定 | 末帧跨件深度 ≤ D/256 |
| 碎薄片判定 | 按平均厚度 2V/A < D/256 |
| CEM 预算 | population/elite/generations=8/2/2，加稳定候选提前停止 |
| 网格简化 | 每例目标 ≤400,000 面；修不好退回原网格的例外逐例记录 |

Public X-Part light, Manifold editing and previously disclosed connector/repair
implementation details remain unchanged. No model, seed, physics threshold,
connector rule, PGE score, CEM variance or acceptance rule is tuned per case.

The zero-gravity diagnostic can fail when overlapping rigid_flex thin shells push
apart and retain drift. This mechanism is supported by the earlier box control and
first-case evidence. Per-case threshold exceedance alone does not establish that
it is the dominant cause across this batch; validity and displacement remain in
each original observation. Zero-gravity invalidity does not erase the export or
change CEM eligibility.

All 100 active cases stay in the denominator. Failed/timeout cases are unsuccessful,
not fabricated observed falls. Pending cases keep the full-batch rate incomplete.
Each case has a cumulative four-hour active wall limit across resumes; completed
stages are hash-verified and reused. Failures/timeouts do not stop the queue and
are not individually retuned. Original inputs and previous evaluations remain intact.

See summary_table.csv, summary_rows.json and cases/<case_id>/assembly/*/stage.json.
Simplification records include each part's before/after faces, signed volume-change
ratio, closure, repair attempt and fallback. Restoring original geometry takes
precedence over the target face cap; overages are explicit rather than hidden.

'''
    report+='## Current summary\n\n'+json.dumps(result,indent=2,sort_keys=True)+'\n'
    (root/'report.md').write_text(report)
    return result


def run_batch(root):
    import yaml
    root=Path(root).resolve();plan=read(root/'concurrency.json');cfg=yaml.safe_load((root/'run_config.yaml').read_text())
    assert cfg['cem']['workers']==plan['cem_workers']
    assert plan['total_simulation_workers']*plan['measured_peak_rss_bytes']<=26*2**30 and plan['total_simulation_workers']<=30
    entries=read(root/'inputs.json')['cases'];allowed=sorted(os.sched_getaffinity(0));slots=plan['case_workers']
    assert len(allowed)>=plan['total_simulation_workers']
    started=time.monotonic();prior=read(root/'batch_clock.json',{}).get('wall_seconds',0.)
    STOP_EVENT.clear()
    def stop(*args):STOP_EVENT.set()
    signal.signal(signal.SIGINT,stop);signal.signal(signal.SIGTERM,stop)
    # A lane is a case process plus its own CEM pool; affinity partitions prevent
    # native preparation threads from using the other lanes' CPUs.
    def lane(index):
        cpus=allowed[index::slots]
        for entry in entries[index::slots]:
            if STOP_EVENT.is_set():break
            try:execute_case(root,entry,cpus)
            except Exception as error:
                directory=root/'cases'/entry['case_id'];previous=read(directory/'case_status.json',{})
                state=dict(previous,status='FAILED',reason=public_reason(f'{type(error).__name__}: {error}',root))
                write(directory/'case_status.json',state);write(directory/'summary.json',case_summary(root,entry['case_id'],state))
    with concurrent.futures.ThreadPoolExecutor(max_workers=slots) as pool:
        futures=[pool.submit(lane,i) for i in range(slots)]
        while not all(f.done() for f in futures):
            write(root/'batch_clock.json',dict(wall_seconds=prior+time.monotonic()-started));summarize(root);time.sleep(10.)
        for f in futures:f.result()
    write(root/'batch_clock.json',dict(wall_seconds=prior+time.monotonic()-started));summarize(root)


def main():
    p=argparse.ArgumentParser();p.add_argument('--root',required=True);p.add_argument('--case-id');p.add_argument('--stop-after',choices=STAGES,default='export')
    p.add_argument('--profile-baseline',action='store_true');p.add_argument('--affinity');p.add_argument('--prepare-case');p.add_argument('--calibrate-case');p.add_argument('--summarize',action='store_true')
    a=p.parse_args();root=Path(a.root).resolve()
    if a.case_id:
        try:run_case(root,a.case_id,a.stop_after,a.profile_baseline,a.affinity)
        except BaseException as error:
            write(root/'cases'/a.case_id/'case_error.json',dict(reason=public_reason(f'{type(error).__name__}: {error}',root)));raise
    elif a.prepare_case or a.calibrate_case:
        cid=a.prepare_case or a.calibrate_case;entry=next(r for r in read(root/'inputs.json')['cases'] if r['case_id']==cid)
        state=execute_case(root,entry,sorted(os.sched_getaffinity(0)),stop_after='cgr' if a.prepare_case else 'rollout',profile_baseline=bool(a.calibrate_case))
        if state['status'] in ['FAILED','TIMEOUT']:raise RuntimeError('CALIBRATION_CASE_NOT_AVAILABLE: '+str(state.get('reason')))
        if a.calibrate_case:
            record=read(root/'cases'/cid/'assembly/rollout/resource.json')
            prior_peak=read(root/'prior_resource_evidence.json',{}).get('max_cem_worker_peak_rss_bytes',0)
            plan=concurrency_plan(max(record['peak_rss_bytes'],prior_peak));write(root/'concurrency.json',dict(plan,calibration_case_id=cid,
                largest_case_baseline_peak_rss_bytes=record['peak_rss_bytes'],first_case_reused_worker_peak_rss_bytes=prior_peak,preflight_wall_seconds=state['wall_seconds'],
                rollout_seconds=record['wall_seconds'],flex_element_count=record.get('flex_element_count')))
            import yaml
            config=yaml.safe_load((root/'run_config.yaml').read_text());config['cem']['workers']=plan['cem_workers'];(root/'run_config.yaml').write_text(yaml.safe_dump(config,sort_keys=False))
        summarize(root)
    elif a.summarize:summarize(root)
    else:run_batch(root)

if __name__=='__main__':main()
