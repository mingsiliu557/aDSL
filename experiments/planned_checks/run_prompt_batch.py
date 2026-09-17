"""Independent prompt generation; joint checks enter the first executable ours round."""
import argparse
import asyncio
import csv
import json
import math
from dataclasses import replace
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from adsl.agents import service
from adsl.agents.models import ObjectRequest, CheckerSpec, RepairPolicy
from adsl.agents.overhang_edit import original_execution
from experiments.planned_checks import run
from experiments.planned_checks.run_edit_smoke import BudgetWorkflow, API, protection_for, reuse_calibration, stop_tree
from adsl.agents.checkers import run_checker, CheckerRun
from adsl.agents.models import CheckerResult
from adsl.agents.utils.runner import _json_value


def request_for(case, workspace):
    # Neither arm receives old source, models or reference renders.
    return ObjectRequest(requirement=case['prompt'],task_id=case['case_id'],workspace=workspace,
        image_paths=(),max_rounds=4,repair_policy=RepairPolicy(max_total_candidates=None))


class PromptWorkflow(BudgetWorkflow):
    def __init__(self, profile, *, case, deadline):
        super().__init__(profile)
        self.case, self.deadline = case, deadline

    async def _initialize_generated_checks(self, *, runtime, request, workspace, source_path,
                                           execution, round_number, plan):
        original=workspace/'original'
        shutil.copytree(execution.glb_path.parent, original)
        (original/'render').mkdir(exist_ok=True)
        for image in execution.render_paths:shutil.copy2(image,original/'render'/image.name)
        shutil.copy2(source_path,original/'source.py')
        for path in (execution.source_index_path,execution.analysis_geometry_path):
            if path and path.is_file():shutil.copy2(path,original/path.name)
        state={'case_id':self.case['case_id'],'case':self.case,'status':'INPUT_READY',
               'input_origin':'own_prompt_generation','input_hashes':run.hashes(original),
               'planning_seconds':0,'protection':protection_for(self.case['case_id'])}
        run.write_json(workspace/'state.json',state)
        registry=run.registry(self.case,workspace/'profiles')
        run.write_json(workspace/'registry.json',registry)
        # The planner reads only this run's own initial source/renders.
        options={'mode':'planned_checks','arm':'feedback','max_candidates':None,
                 'initial_round':round_number,'original_urdf':str(original/'scene.urdf'),
                 'protection_policy':'explicit_task_constraints','protection':state['protection']}
        spec=CheckerSpec.model_validate(registry['overhang_fixed_print']['spec'])
        calibration=run_checker(spec,execution=original_execution(options),source_path=original/'source.py',
                                round_root=workspace/'calibration').result
        run.write_json(workspace/'calibration_result.json',calibration.model_dump())
        measurement=calibration.metrics.get('measurement')
        profile=registry['overhang_fixed_print']
        scale=measurement.get('scale_mm_per_source_unit') if isinstance(measurement,dict) else None
        calibrated=(calibration.status=='PASS' and type(scale) in (int,float)
                    and math.isfinite(scale) and scale>0)
        if not calibrated and calibration.status=='PASS':
            calibration=calibration.model_copy(update={'status':'INDETERMINATE',
                'summary':'MEASUREMENT_CONDITIONS_UNAVAILABLE: initial scale missing or invalid'})
            run.write_json(workspace/'calibration_result.json',calibration.model_dump())
        if calibrated:
            profile['configuration']['case']['frozen_measurement']=measurement
            run.write_json(Path(profile['config_path']),profile['configuration'])
            profile['config_sha256']=run.sha(Path(profile['config_path']))
            run.write_json(workspace/'registry.json',registry)
        else:
            run.write_json(workspace/'print_measurement_unavailable.json',{
                'status':'INDETERMINATE','reason':calibration.summary,
                'effect':'No comparable overhang optimization in this run; independent checks and visual review continue.'})
        await run.plan_case_bounded(workspace.parent,request.task_id,API)
        plan_path=workspace/'tool_plan.resolved.json'
        if not plan_path.exists():raise RuntimeError('PLAN_UNAVAILABLE: own generated assets preserved')
        resolved=run.read(plan_path);run.verify_plan(resolved)
        specs=tuple(CheckerSpec.model_validate(t['spec']) for t in resolved['tools'] if t['selected'])
        options['unverified_plan_tools']=[
            {'name':t['name'],'status':t['status'],'reason':t.get('reason',''),
             'required':bool(t.get('required') or t['name'] in ('topology','overhang'))}
            for t in resolved['tools'] if t.get('status') in ('PLAN_INVALID','NEEDS_SPEC')]
        if calibrated:options['scale_mm_per_source_unit']=measurement['scale_mm_per_source_unit']
        joint=replace(request,checker_specs=specs,overhang_experiment=options,check_first=True,
            repair_policy=RepairPolicy(max_total_candidates=None,time_budget_seconds=max(1,self.deadline-time.time())))
        config=run.read(workspace/'runtime_config.json')
        config['request']=_json_value(joint)
        run.write_json(workspace/'runtime_config.json',config)
        run.write_json(workspace/'generation_check_handoff.json',{'mode':'generate','initial_round':round_number,
            'max_rounds':request.max_rounds,'source_sha256':run.sha(source_path),'source_origin':'own_prompt',
            'appearance_approval_required_before_checks':False})
        original_runner=service.run_checkers
        def measured(selected,*,require_topology=False,**kw):
            results=[]
            for item in sorted(selected,key=lambda x:x.name!='topology'):
                topology=next((r for r in results if r.spec.name=='topology'),None)
                reason='TOPOLOGY_DEPENDENCY_UNAVAILABLE' if item.name=='fea' and (topology is None or topology.result.status!='PASS') else None
                if reason:
                    dest=kw['round_root']/'checkers'/item.name
                    result=CheckerResult(checker=item.name,status='INDETERMINATE',summary=reason,violations=[{'code':reason}])
                    run.write_json(dest/'result.json',result.model_dump());results.append(CheckerRun(item,result,dest,()))
                elif (item.name=='overhang' and run.sha(kw['source_path'])==run.sha(original/'source.py')
                      and run.sha(kw['execution'].glb_path)==run.sha(original/'scene.glb')
                      and kw['execution'].urdf_path and run.sha(kw['execution'].urdf_path)==run.sha(original/'scene.urdf')):
                    results.append(reuse_calibration(item,calibration,workspace/'calibration',kw['round_root']))
                elif item.name=='overhang' and not calibrated:
                    dest=kw['round_root']/'checkers'/item.name
                    result=CheckerResult(checker=item.name,status='INDETERMINATE',
                        summary='MEASUREMENT_CONDITIONS_UNAVAILABLE: no frozen initial print measurement; candidate overhang not evaluated',
                        metrics={'overhang_area_mm2':None,'nominal_contact_area_mm2':None,'candidate_conclusion':'unevaluated'},
                        violations=[{'code':'MEASUREMENT_CONDITIONS_UNAVAILABLE','stage':'measurement_configuration'}],
                        artifacts={'initial_failure':str(workspace/'calibration_result.json')})
                    run.write_json(dest/'result.json',result.model_dump());results.append(CheckerRun(item,result,dest,()))
                else:results.append(run_checker(item,**kw))
            return results
        service.run_checkers=measured
        try:
            # Same generated source, runtime/session and absolute round ceiling.
            # Existing dispatcher uses joint isolated candidates from here on.
            return await super()._iterate(runtime=runtime,request=joint,workspace=workspace,
                source_path=source_path,mode='generate',plan=plan,resume_state={})
        finally:service.run_checkers=original_runner


def worker(root,cid,arm):
    if arm!='ours':raise ValueError('aDSL is historical only; do not regenerate')
    case=next(c for c in run.read(run.REPO/'experiments/standing_fea_30/case_manifest.json')['cases'] if c['case_id']==cid)
    workspace=root/arm/cid
    status_path=root/'statuses'/f'{cid}_{arm}.json'
    if workspace.exists():raise ValueError('existing generation workspace; no automatic replay')
    start=time.time()
    request=request_for(case,workspace)
    try:
        workflow=PromptWorkflow(API,case=case,deadline=run.read(root/'batch.json')['deadline'])
        result=asyncio.run(workflow.generate(request))
        book=run.read(workspace/'overhang_versions.json') if (workspace/'overhang_versions.json').exists() else {}
        error=book.get('error')
        state={'status':failure_status(error['type']) if error else 'COMPLETED','approved':result.approved,
               'retained':book.get('retained','published'),'error':book.get('error'), 'source':str(result.source_path)}
    except Exception as error:
        state={'status':failure_status(type(error).__name__),'error_type':type(error).__name__,'reason':str(error)[:400],
               'assets_preserved':True}
    state.update(case_id=cid,arm=arm,workspace=str(workspace),seconds=time.time()-start,input_origin='independent_prompt')
    run.write_json(status_path,state)
    print(state,flush=True)


def failure_status(error_type):
    return ('FLOW_ERROR' if error_type in {'TypeError','AttributeError','KeyError',
            'AssertionError','ValueError','WorkflowGateError'} else 'ERROR')


def batch(root,ids):
    root.mkdir(parents=True,exist_ok=True)
    if not (root/'batch.json').exists():
        run.write_json(root/'batch.json',{'started_at':time.time(),'deadline':time.time()+8*3600,
            'case_seconds':None,'max_rounds':4,'max_candidates':None,'cases':ids,
            'design':'ours independently generated from prompt; aDSL historical results only, no regeneration',
            'commit':subprocess.check_output(['git','rev-parse','HEAD'],text=True).strip(),
            'code_hashes':{str(p.relative_to(run.REPO)):run.sha(p) for p in
                list((run.REPO/'adsl-agents').rglob('*.py'))+list((run.REPO/'experiments/planned_checks').glob('*.py'))}})
    for arm in ('ours',):
        (root/arm).mkdir(exist_ok=True)
        run.write_json(root/arm/'batch.json',run.read(root/'batch.json'))
    from experiments.overhang_feedback.run_pilot import runtime_environment
    from experiments.standing_fea_30.run_batch import checker_environment
    env=checker_environment(runtime_environment(Path('/usr/bin/false')))
    for cid in ids:
        for arm in ('ours',):
            dest=root/'statuses'/f'{cid}_{arm}.json'
            if dest.exists():continue
            remaining=run.read(root/'batch.json')['deadline']-time.time()
            if remaining<=0:
                run.write_json(dest,{'case_id':cid,'arm':arm,'status':'BUDGET_NOT_EXECUTED'});continue
            (root/'logs').mkdir(exist_ok=True)
            print('starting independent generation',cid,arm,flush=True)
            with (root/'logs'/f'{cid}_{arm}.log').open('a') as log:
                proc=subprocess.Popen([sys.executable,'-u','-m',__spec__.name,'worker','--output',str(root),'--cases',cid,'--arm',arm],
                    cwd=run.REPO,env=env,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                try:code=proc.wait(timeout=remaining)
                except subprocess.TimeoutExpired:stop_tree(proc);code=124
            if not dest.exists():run.write_json(dest,{'case_id':cid,'arm':arm,'status':'WORKER_ERROR','exit_code':code})
            print(run.read(dest),flush=True)
            rows=[run.read(p) for p in sorted((root/'statuses').glob('*.json'))]
            run.csv_write(root/'generation_results.csv',rows,list(dict.fromkeys(k for r in rows for k in r)))
            summarize(root)
            if run.read(dest)['status']=='FLOW_ERROR':return


def summarize(root):
    historical=list(csv.DictReader((run.HISTORY/'results.csv').open()))
    rows=[]
    for cid in run.read(root/'batch.json')['cases']:
        old=next((r for r in historical if r['case_id']==cid and r['arm']=='adsl'),{})
        row={'case_id':cid,**{'adsl_'+k:v for k,v in old.items() if k not in ('prompt','case_id','arm')}}
        workspace=root/'ours'/cid;status=root/'statuses'/f'{cid}_ours.json'
        row['ours_run_status']=run.read(status)['status'] if status.exists() else 'NOT_RUN'
        if status.exists():
            value=run.read(status);row['ours_seconds']=value.get('seconds');row['ours_error']=value.get('error',value.get('reason'))
        checks=[]
        plan=run.read(workspace/'tool_plan.resolved.json') if (workspace/'tool_plan.resolved.json').exists() else {}
        if (workspace/'overhang_versions.json').exists():
            book=run.read(workspace/'overhang_versions.json');v=book['versions'][book['retained']]
            checks=[c['result'] for c in v.get('checkers',[])]
            row['ours_retained']=book['retained'];row['ours_attempts']=len(book['attempts'])
            row['ours_appearance']=v.get('reviews',{}).get('appearance_approved')
        for name in ('topology','standing','fea','overhang'):
            tool=next((t for t in plan.get('tools',[]) if t.get('name')==name),{})
            result=next((c for c in checks if c['checker']==name),{})
            row[f'ours_{name}_plan_status']=tool.get('status','NOT_PLANNED')
            row[f'ours_{name}_status']=result.get('status',tool.get('status') if not tool.get('selected',True) else 'NOT_EVALUATED')
            row[f'ours_{name}_reason']=result.get('summary',tool.get('reason'))
            metrics=result.get('metrics',{})
            row[f'ours_{name}_metrics_json']=json.dumps(metrics,ensure_ascii=False)
            for key in ('component_count','overhang_area_mm2','nominal_contact_area_mm2','support_required',
                        'max_displacement_over_characteristic_length','nominal_safety_factor','first_positive_buckling_factor'):
                if key in metrics:row[f'ours_{key}']=metrics[key]
        rows.append(row)
    run.csv_write(root/'comparison_12.csv',rows,list(dict.fromkeys(k for r in rows for k in r)))
    lines=['# Historical aDSL versus independently generated ours','',
        'aDSL values are historical; aDSL was not regenerated. Ours receives only the same prompt.',
        'Independent generation and changed model/configuration can affect differences; not a matched-asset causal estimate.',
        'Missing historical outputs remain missing. Overhang PASS means measurement completed only.',
        'Historical overhang values are not in the original table, so no invented overhang gain is reported.', '',
        '| Case | Historical aDSL | Ours run | Topology aDSL / ours | Standing | FEA |',
        '|---|---|---|---|---|---|']
    for r in rows:
        cells=[r['case_id'],r.get('adsl_run_status','NOT_AVAILABLE'),r['ours_run_status']]
        cells += [f"{r.get('adsl_'+n+'_status') or 'NOT_EVALUATED'} / {r['ours_'+n+'_status']}" for n in ('topology','standing','fea')]
        lines.append('| '+' | '.join(cells)+' |')
    (root/'COMPARISON_12.md').write_text('\n'.join(lines)+'\n')


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('phase',choices=['batch','worker'])
    parser.add_argument('--output',type=Path,required=True);parser.add_argument('--cases',nargs='+',default=list(run.IDS))
    parser.add_argument('--arm',choices=['ours'],default='ours');args=parser.parse_args()
    if not set(args.cases)<=set(run.IDS):raise ValueError('unknown cases')
    if args.phase=='batch':batch(args.output,args.cases)
    else:worker(args.output,args.cases[0],args.arm)
