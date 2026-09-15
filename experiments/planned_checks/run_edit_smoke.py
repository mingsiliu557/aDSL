"""Existing aDSL assets -> isolated planned local edits. No regeneration."""
import argparse
import asyncio
from dataclasses import replace
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import time
import shutil

from adsl.agents import service
from adsl.agents.checkers import run_checker, CheckerRun
from adsl.agents.models import ObjectRequest, CheckerSpec, CheckerResult, RepairPolicy
from adsl.agents.overhang_edit import original_execution, protection_check, experiment_protection
from experiments.planned_checks import run
from experiments.planned_checks.token_budget import BudgetModel

API = run.REPO/'adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml'
LEDGER = run.REPO/'local_experiment/planned_checks_new_api'

def reuse_calibration(spec, result, calibration_root, round_root):
    """Publish cached evidence inside the agent workspace, without remeasurement."""
    source = calibration_root/'checkers'/spec.name
    dest = round_root/'checkers'/spec.name
    shutil.copytree(source, dest, dirs_exist_ok=True)
    def relocate(value):
        if isinstance(value, str) and value.startswith(str(source.resolve())+'/'):
            return str(dest.resolve())+value[len(str(source.resolve())):]
        if isinstance(value, dict):return {k:relocate(v) for k,v in value.items()}
        if isinstance(value, list):return [relocate(v) for v in value]
        return value
    local_result = CheckerResult.model_validate(relocate(result.model_dump()))
    run.write_json(dest/'result.json',local_result.model_dump())
    run.write_json(dest/'cache_reuse.json',{'origin':str(source),'remeasured':False})
    return CheckerRun(spec,local_result,dest,())

def protection_for(cid):
    return {'required_features':['preserve prompt-required components, interfaces, function and recognizable appearance',
            'retain wood grain, knots, wheels, grille and compartments where present; do not delete parts to pass'],
        'explicit_constraints':[],
        'policy_notes':'Joint mode: no inherited class whitelist, exact surface-triangle or global AABB default. Explicit task constraints still apply.',
        'dimensions':'Preserve dimensions/interfaces explicitly required by the task; no additional exact-dimension certification.'}


def restore_geometry_evidence(folder):
    state=run.read(folder/'state.json'); original=folder/'original'
    if (original/'analysis_geometry.json').exists():return
    history=Path(state['historical_asset'])
    matches=[]
    for path in history.glob('rounds/**/analysis_geometry.json'):
        if run.read(path).get('source_sha256')!=run.sha(original/'source.py'):continue
        render=path.parent/'render'
        if all((render/name).is_file() and run.sha(render/name)==run.sha(original/name)
               for name in ['scene.glb','scene.urdf']):matches.append(path)
    if not matches:raise ValueError('no source/GLB/URDF-matched historical analysis geometry')
    if len({run.sha(p) for p in matches})!=1:raise ValueError('ambiguous matching geometry manifests')
    old_hashes=state['input_hashes']
    if run.hashes(original)!=old_hashes:raise ValueError('original changed before evidence restore')
    shutil.copy2(matches[0],original/'analysis_geometry.json')
    run.write_json(folder/'geometry_evidence_restore.json',{'source':str(matches[0]),
        'source_sha256':run.sha(original/'source.py'),'old_input_hashes':old_hashes,
        'reason':'restore existing source-linked evidence; source/model bytes unchanged'})
    state['input_hashes']=run.hashes(original);run.write_json(folder/'state.json',state)


class BudgetWorkflow(service.ObjectWorkflow):
    def _runtime(self,*args,**kwargs):
        runtime=super()._runtime(*args,**kwargs)
        runtime.profile=replace(runtime.profile,max_retries=0)
        runtime.model=BudgetModel(runtime.profile.agent_model(workspace=runtime.workspace),LEDGER,
            request_bound=304768,evidence={'model':'gpt-5.6-sol','case_id':args[0].task_id})
        return runtime


def worker(root,cid,resume=False):
    folder=root/cid
    state=run.read(folder/'state.json')
    if state['status']=='INPUT_UNAVAILABLE':return
    if resume and state.get('protection') and ('allowed_classes' in state['protection'] or state['protection'].get('surfaces')):
        raise ValueError('legacy explicit protection requires reviewed migration; use a new experiment configuration')
    marker=folder/'edit_worker_started.json'
    if marker.exists() and not resume:raise ValueError('case already attempted; no automatic replay')
    started=run.read(marker)['started_at'] if resume else time.time()
    deadline=run.read(root/'batch.json')['deadline']
    if deadline<=time.time():raise ValueError('original case deadline exhausted')
    if not resume:run.write_json(marker,{'started_at':started,'deadline':deadline,'max_rounds':4,'max_candidates':None})
    restore_geometry_evidence(folder)
    # Restoration writes the new evidence hash. Do not overwrite it with the
    # pre-restoration snapshot when adding the manual protection scope.
    state=run.read(folder/'state.json')
    state['protection']=protection_for(cid)
    run.write_json(folder/'state.json',state)
    original=folder/'original'
    options={'mode':'planned_checks','arm':'feedback','original_urdf':str(original/'scene.urdf'),
        'max_candidates':None,'protection':state['protection'],'protection_policy':'explicit_task_constraints'}
    # Freeze print conditions before planning; failed calibration is not zero area.
    reg=run.read(folder/'registry.json')
    spec=CheckerSpec.model_validate(reg['overhang_fixed_print']['spec'])
    if (folder/'calibration_result.json').exists():
        calibration_result=CheckerResult.model_validate(run.read(folder/'calibration_result.json'))
    else:
        calibration_result=run_checker(spec,execution=original_execution(options),source_path=original/'source.py',round_root=folder/'calibration').result
        run.write_json(folder/'calibration_result.json',calibration_result.model_dump())
    measurement=calibration_result.metrics.get('measurement')
    if calibration_result.status!='PASS' or not measurement:
        run.write_json(folder/'edit_status.json',{'status':'MEASUREMENT_UNAVAILABLE','retained':'original',
            'reason':calibration_result.summary});return
    profile=reg['overhang_fixed_print'];config=profile['configuration']
    config['case']['frozen_measurement']=measurement
    run.write_json(Path(profile['config_path']),config)
    profile['config_sha256']=run.sha(Path(profile['config_path']))
    run.write_json(folder/'registry.json',reg)
    options['scale_mm_per_source_unit']=measurement['scale_mm_per_source_unit']
    asyncio.run(run.plan_case_bounded(root,cid,API))
    if not (folder/'tool_plan.resolved.json').exists():
        run.write_json(folder/'edit_status.json',{'status':'PLAN_UNAVAILABLE','retained':'original'});return
    plan=run.read(folder/'tool_plan.resolved.json');run.verify_plan(plan)
    if not state['protection']:
        run.write_json(folder/'edit_status.json',{'status':'EDIT_NOT_READY','retained':'original',
            'reason':'no reviewed protection scope for original aDSL asset'});return
    protection=experiment_protection(original/'scene.urdf',original/'scene.urdf',options,exact_check=protection_check)
    run.write_json(folder/'protection_preflight.json',protection)
    if protection['status']!='PASS':
        run.write_json(folder/'edit_status.json',{'status':'EDIT_NOT_READY','retained':'original','reason':protection});return
    specs=tuple(CheckerSpec.model_validate(t['spec']) for t in plan['tools'] if t['selected'])
    if 'fea' in [s.name for s in specs] and 'topology' not in [s.name for s in specs]:
        # Keep the plan's dependency failure in evidence, do not run unsupported FEA.
        run.write_json(folder/'edit_status.json',{'status':'DEPENDENCY_UNRESOLVED','retained':'original'});return
    workspace=run.REPO/'local_experiment'/root.name/cid/'ours'
    workspace.parent.mkdir(parents=True,exist_ok=True)
    # A fixed per-case admission check plus parent hard timeout, never reset per round.
    original_runner=service.run_checkers
    def bounded_checkers(selected,**kwargs):
        results=[]
        for item in sorted(selected,key=lambda s:s.name!='topology'):
            # Calibration already measured this exact original under the frozen
            # print conditions. Reuse it for the first repair assessment only.
            if (item.name=='overhang' and run.sha(kwargs['source_path'])==run.sha(original/'source.py')
                    and run.sha(kwargs['execution'].glb_path)==run.sha(original/'scene.glb')
                    and kwargs['execution'].urdf_path
                    and run.sha(kwargs['execution'].urdf_path)==run.sha(original/'scene.urdf')):
                results.append(reuse_calibration(item,calibration_result,folder/'calibration',kwargs['round_root']))
                continue
            topology=next((r for r in results if r.spec.name=='topology'),None)
            reason=('TOPOLOGY_DEPENDENCY_UNAVAILABLE' if item.name=='fea' and topology and topology.result.status!='PASS'
                else 'BUDGET_NOT_EXECUTED' if time.time()+item.timeout_seconds+5>deadline else None)
            if reason:
                dest=kwargs['round_root']/'checkers'/item.name
                result=CheckerResult(checker=item.name,status='INDETERMINATE',summary=reason,
                    violations=[{'code':reason}])
                run.write_json(dest/'result.json',result.model_dump())
                results.append(CheckerRun(item,result,dest,()))
            else:results.append(run_checker(item,**kwargs))
        return results
    service.run_checkers=bounded_checkers
    request=ObjectRequest(requirement=state['case']['prompt']+'\nPreserve key shape and appearance. Repair actionable hard checker issues first; then reduce exposed overhang where safe. May stop without improvement.',
        workspace=workspace,task_id=cid,image_paths=tuple(sorted((original/'render').glob('*.png'))),
        max_rounds=4,check_first=True,checker_specs=specs,
        repair_policy=RepairPolicy(max_total_candidates=None,time_budget_seconds=max(1,deadline-time.time())),
        overhang_experiment=options)
    try:
        if resume:
            from adsl.agents.overhang_edit import version_record
            book=run.read(workspace/'overhang_versions.json')
            ledger_recovery = book.get('error',{}).get('reason') == 'TOKEN_USAGE_UNRESOLVED: do not replay unknown requests'
            api_recovery = (workspace/'api_resume_authorized.json').exists() and book.get('error',{}).get('type') in {'APIStatusError','APIConnectionError','APITimeoutError'}
            source_read_recovery = ((workspace/'source_read_resume_authorized.json').exists()
                and book.get('error',{}).get('reason')=='engineering critic did not use required tool read_file')
            if book['attempts'] and not (api_recovery or source_read_recovery):raise ValueError('reviewed recovery requires no edit attempts')
            if api_recovery or source_read_recovery:
                recovery=workspace/('source_read_resume_applied.json' if source_read_recovery else 'api_resume_applied.json')
                if recovery.exists():raise ValueError('reviewed API recovery already applied')
                if any(a.get('status') in {'EDITING','MODEL_STARTED'} for a in book['attempts'].values()):
                    raise ValueError('unfinished candidate requires separate review; do not replay')
                run.write_json(recovery,{'previous_book':book,'deadline':deadline,
                    'attempts_preserved':len(book['attempts']),'max_rounds_unchanged':request.max_rounds})
                next_round=workspace/'rounds'/f"round_{1+len(book['attempts']):02d}"
                if next_round.exists():next_round.rename(workspace/('source_read_interrupted_round' if source_read_recovery else 'api_interrupted_round'))
                for key in ('error','completed','stop_reason'):book.pop(key,None)
            elif ledger_recovery:
                recovery=workspace/'interrupted_usage_recovery.json'
                if recovery.exists():raise ValueError('usage recovery already performed')
                budget=BudgetModel(None,LEDGER,request_bound=304768,evidence={'review':'interrupted SF03 request'})
                def acknowledge(state):
                    prior=[]
                    for key,value in state['requests'].items():
                        if value['status']=='RESERVED' and value.get('bound_evidence',{}).get('case_id')==cid:
                            prior.append({'request_id':key,**value})
                            value.update(status='BOUNDED_UNKNOWN',actual_tokens=None,
                                review_reason='operator stopped case to restore input evidence; full bound retained')
                    run.write_json(recovery,{'previous_book':book,'requests':prior,'deadline_unchanged':deadline})
                budget.update(acknowledge)
                book.pop('error',None);book.pop('completed',None);book.pop('stop_reason',None)
            else:
                if (workspace/'input_recovery.json').exists():raise ValueError('input recovery already performed')
                run.write_json(workspace/'input_recovery.json',{'previous_book':book,'deadline_unchanged':deadline})
                (workspace/'rounds').rename(workspace/'rounds_before_input_recovery')
                book['versions']['original']=version_record('original',workspace/'original_source.py',original_execution(options))
            run.write_json(workspace/'overhang_versions.json',book)
            result=asyncio.run(BudgetWorkflow(API).resume(request))
        else:result=asyncio.run(BudgetWorkflow(API).edit(request,source=original/'source.py'))
        book=run.read(workspace/'overhang_versions.json')
        run.write_json(folder/'edit_status.json',{'status':'FLOW_ERROR' if book.get('error') else 'COMPLETED',
            'workspace':str(workspace),'retained':book['retained'],'approved':result.approved,
            'stop_reason':book['stop_reason'],'error':book.get('error'),'seconds':time.time()-started})
    finally:service.run_checkers=original_runner


def stop_tree(process):
    # Existing checkers start their own sessions; collect descendants before leader exit.
    rows=[list(map(int,line.split())) for line in subprocess.check_output(['ps','-eo','pid=,ppid=,pgid='],text=True).splitlines()]
    children={process.pid}
    while True:
        more={pid for pid,parent,_ in rows if parent in children}
        if more<=children:break
        children|=more
    groups={group for pid,_,group in rows if pid in children and group!=os.getpgrp()}
    for group in groups:
        try:os.killpg(group,signal.SIGTERM)
        except ProcessLookupError:pass
    try:process.wait(timeout=2)
    except subprocess.TimeoutExpired:pass
    for group in groups:
        try:os.killpg(group,signal.SIGKILL)
        except ProcessLookupError:pass
    process.wait()


def batch(root,ids,resume=False):
    run.prepare(root,input_arm='adsl')
    from experiments.overhang_feedback.run_pilot import runtime_environment
    from experiments.standing_fea_30.run_batch import checker_environment
    environment=checker_environment(runtime_environment(Path('/usr/bin/false')))
    for cid in ids:
        folder=root/cid
        if (folder/'edit_status.json').exists():
            if not resume:continue
            (folder/'edit_status.json').rename(folder/f'edit_status.before_recovery_{time.time_ns()}.json')
        if run.read(folder/'state.json')['status']=='INPUT_UNAVAILABLE':
            run.write_json(folder/'edit_status.json',{'status':'INPUT_UNAVAILABLE'});continue
        if (folder/'edit_worker_started.json').exists() and not resume:
            run.write_json(folder/'edit_status.json',{'status':'INTERRUPTED_NOT_REPLAYED','retained':'original'});continue
        remaining=run.read(root/'batch.json')['deadline']-time.time()
        if remaining<=0:
            run.write_json(folder/'edit_status.json',{'status':'BUDGET_NOT_EXECUTED','retained':'original'});continue
        print('starting',cid,flush=True)
        with (folder/'edit_console.log').open('a') as log:
            process=subprocess.Popen([sys.executable,'-u','-m',__spec__.name,'resume-worker' if resume else 'worker','--output',str(root),'--cases',cid],
                cwd=run.REPO,stdout=log,stderr=subprocess.STDOUT,start_new_session=True,env=environment)
            try:code=process.wait(timeout=remaining)
            except subprocess.TimeoutExpired:
                stop_tree(process);code=124
        if not (folder/'edit_status.json').exists():
            run.write_json(folder/'edit_status.json',{'status':'TIMEOUT' if code==124 else 'WORKER_ERROR',
                'exit_code':code,'retained':'original','reason':'see edit_console.log; originals preserved'})
        status=run.read(folder/'edit_status.json');print(cid,status,flush=True)
        rows=[{'case_id':p.parent.name,**run.read(p)} for p in sorted(root.glob('SF*/edit_status.json'))]
        run.csv_write(root/'edit_results.csv',rows,['case_id','status','retained','approved','stop_reason','seconds','reason'])
        if status['status']=='FLOW_ERROR':break


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('phase',choices=['worker','batch','resume','resume-worker'])
    p.add_argument('--output',type=Path,required=True);p.add_argument('--cases',nargs='+',default=['SF03'])
    args=p.parse_args()
    if not set(args.cases)<=set(run.IDS):raise ValueError('unknown case')
    if args.phase in {'worker','resume-worker'}:worker(args.output,args.cases[0],resume=args.phase=='resume-worker')
    else:batch(args.output,args.cases,resume=args.phase=='resume')
