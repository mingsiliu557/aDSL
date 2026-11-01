"""Ours-only image generation. No final evaluation or baseline mesh gate."""
import argparse
import asyncio
from concurrent.futures import ThreadPoolExecutor, wait, FIRST_COMPLETED
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import traceback

ROOT=Path(__file__).resolve().parents[2]

def read(p):return json.loads(p.read_text())
def save(p,obj):
    p.parent.mkdir(parents=True,exist_ok=True);tmp=p.with_suffix(p.suffix+'.tmp')
    tmp.write_text(json.dumps(obj,indent=2,default=str)+'\n');tmp.replace(p)

def worker(root,cid):
    from adsl.agents.models import ObjectRequest,RepairPolicy
    from adsl.agents.assembly_topology import checker_spec as topology_spec
    from adsl.agents.assembly_physics import checker_spec
    from experiments.densicrafter_image_only.workflow import ImageOnlyWorkflow,TASK
    cfg=read(root/'run_config.json');row=next(r for r in read(root/'input_manifest.json')['cases'] if r['case_id']==cid)
    out=root/'cases'/cid;out.mkdir(parents=True,exist_ok=True)
    if (out/'status.json').exists():raise ValueError('Existing case attempt: explicit resume/retry required')
    image=root/row['image_path'];assert hashlib.sha256(image.read_bytes()).hexdigest()==row['image_sha256']
    started=time.time();state=dict(case_id=cid,status='RUNNING',started=started,gpu=os.environ.get('CUDA_VISIBLE_DEVICES'),input_sha256=row['image_sha256'],model=cfg['model'],input_mode='single_image_without_case_caption')
    save(out/'status.json',state)
    request=ObjectRequest(requirement=TASK,workspace=out/'native',task_id=cid,image_paths=(image,),max_rounds=cfg['max_rounds'],
        fixed_assembly=cfg['fixed_assembly'],repair_policy=RepairPolicy(print_partition_editable=True,print_orientation_editable=True,max_total_candidates=cfg['max_rounds']-1),
        checker_specs=(topology_spec(),checker_spec('assembly_overhang'),checker_spec('assembly_standing')))
    class RecordedWorkflow(ImageOnlyWorkflow):
        def _runtime(self,request,workspace,**kw):
            runtime=super()._runtime(request,workspace,**kw);self.recorded_runtime=runtime
            original=runtime.run;counter=0
            async def recorded(**kwargs):
                nonlocal counter
                counter+=1
                save(out/'role_inputs'/f'{counter:03d}_{kwargs.get("role","unknown")}.json',dict(role=kwargs.get('role'),stage=kwargs.get('stage'),input=kwargs.get('input'),instructions=getattr(kwargs.get('agent'),'instructions',None)))
                return await original(**kwargs)
            runtime.run=recorded
            return runtime
    workflow=RecordedWorkflow(root/'model.yaml')
    try:
        result=asyncio.run(workflow.generate(request))
        state.update(status='COMPLETE',approved=result.approved,source_path=str(result.source_path),glb_path=str(result.glb_path),selected_round=result.selected_round,usage=result.usage)
        code=0
    except Exception as error:
        from adsl.agents.utils.request_errors import classify_model_request_error
        info=classify_model_request_error(error)
        pause=bool(info and info.get('kind')=='shared_fault') or getattr(error,'status_code',None) in (401,403,429)
        state.update(status='PAUSED_API' if pause else 'FAILED',error_type=type(error).__name__,error=str(error),request_error=info)
        (out/'error.log').write_text(traceback.format_exc());code=75 if pause else 1
    state['elapsed_seconds']=time.time()-started
    if hasattr(workflow,'recorded_runtime'):
        from dataclasses import asdict,is_dataclass
        usage=workflow.recorded_runtime.usage.totals();state['usage']=asdict(usage) if is_dataclass(usage) else str(usage)
    save(out/'status.json',state);return code


def batch(root):
    cfg=read(root/'run_config.json');rows=read(root/'input_manifest.json')['cases'];gpus=cfg['gpus'];pending=[];results={}
    for row in rows:
        cid=row['case_id'];p=root/'cases'/cid/'status.json'
        if p.exists():
            r=read(p);results[cid]=r
            if r['status'] not in ('COMPLETE','FAILED'):raise ValueError(f'Unfinished prior attempt {cid}; inspect before resume')
        else:pending.append(cid)
    active={};stop=False
    def record(status):save(root/'status.json',dict(status=status,total=len(rows),complete=sum(r['status']=='COMPLETE' for r in results.values()),failed=sum(r['status']=='FAILED' for r in results.values()),remaining=len(pending),active=active,updated=time.time(),final_evaluation=False))
    def execute(cid,gpu):
        out=root/'cases'/cid;out.mkdir(parents=True,exist_ok=True);env=dict(os.environ,CUDA_VISIBLE_DEVICES=gpu)
        with (out/'generation.log').open('a') as log:
            proc=subprocess.Popen([sys.executable,str(Path(__file__).resolve()),'--root',str(root),'--worker',cid],env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,cwd=ROOT)
            try:return proc.wait(timeout=7200)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid,signal.SIGTERM)
                try:proc.wait(timeout=15)
                except subprocess.TimeoutExpired:os.killpg(proc.pid,signal.SIGKILL);proc.wait()
                state=read(out/'status.json') if (out/'status.json').exists() else dict(case_id=cid)
                state.update(status='FAILED',error_type='CASE_TIMEOUT',error='generation exceeded 7200 seconds');save(out/'status.json',state);return 124
    # First real case validates the adapter and native pipeline before filling both workers.
    with ThreadPoolExecutor(max_workers=len(gpus)) as pool:
        futures={};available=list(gpus);first=not results
        while pending or futures:
            while pending and available and not stop and (not first or not futures):
                cid=pending.pop(0);gpu=available.pop(0);active[cid]=gpu;futures[pool.submit(execute,cid,gpu)]=(cid,gpu);record('RUNNING')
            if not futures:break
            done,_=wait(futures,return_when=FIRST_COMPLETED)
            for f in done:
                cid,gpu=futures.pop(f);code=f.result();active.pop(cid);available.append(gpu)
                p=root/'cases'/cid/'status.json';results[cid]=read(p) if p.exists() else dict(case_id=cid,status='FAILED',exit_code=code)
                if code==75 or first and code!=0:stop=True
                first=False
            record('PAUSING' if stop else 'RUNNING')
    record('PAUSED' if stop else 'FINISHED')
    return 75 if stop else 0

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--root',type=Path,required=True);p.add_argument('--worker');args=p.parse_args()
    raise SystemExit(worker(args.root.resolve(),args.worker) if args.worker else batch(args.root.resolve()))
