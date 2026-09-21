"""Six frozen prompts, connector in both arms; reuse three historical w/topology runs.

Thin submission/offline reporting only. No new agent loop, geometry, or retries.
Never select a different version after offline scoring. Individual failures continue.
"""
from __future__ import annotations
import argparse
import asyncio
import csv
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback
import yaml

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from experiments.fixed_assembly_prompt import run as prompt, run_six, retry_api_cases
from adsl.agents.assembly_topology import checker_spec, run_assembly_topology
from adsl.agents.models import ImageCriticDecision, ObjectRequest
from adsl.agents.overhang_edit import assert_version, file_hash, version_assets
from adsl.agents.prompts import object_prompt
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.io import read_json, write_json

CASES=('SF07','SF03','SF13','SF02','SF10','SF16')
SIZES={**prompt.SIZES,'SF02':[90.,90.,150.],'SF10':[180.,90.,90.],'SF16':[100.,40.,160.]}
OLD=REPO/'local_experiment/assembly_topology_six_20260921T060500Z'
PRIOR=(REPO/'local_experiment/assembly_topology_api_retry_20260921T071034Z',
       REPO/'temp/assembly_topology_api_retry_20260921T083011Z',
       REPO/'temp/assembly_topology_retry3_20260921T090735Z')
REUSED={
    'SF07':REPO/'temp/assembly_topology_remaining_20260921T100200Z/continued/new_SF07',
    'SF03':REPO/'temp/assembly_topology_SF03_resume_20260921T091930Z/continued/new_SF03',
    'SF13':REPO/'temp/assembly_topology_remaining_20260921T100200Z/continued/new_SF13'}
CORE=tuple(p for p in run_six.IMPLEMENTATION if p.startswith('adsl-'))


def optional(path):
    return read_json(path) if path.is_file() else {}


def selected_version(work):
    book=read_json(work/'assembly_versions.json')
    version=book['versions'][book['retained']]
    assert_version(version)
    if file_hash(work/'source.py')!=file_hash(Path(version['source'])):
        raise ValueError('Published source does not match retained')
    return book,version


def reuse_evidence(cid, expected):
    old=read_json(OLD/'six_cases.json')
    assert all(file_hash(REPO/p)==old['implementation_sha256'][p] for p in CORE), 'Core implementation changed'
    initial=read_json(OLD/'new'/cid/'input.json')
    for key in ('original_task','manufacturing_requirements','fixed_assembly','source_repair_limit','checker_specs'):
        assert initial[key]==expected[key], f'Historical {cid} {key} changed'
    first=(PRIOR[0]/'fresh/SF13' if cid=='SF13' else OLD/'new'/cid)
    audit=read_json(first/'input_audit.json')
    assert audit['input_verified'] and audit['assembly_api_verified'], 'Missing fresh-generation audit'
    before=yaml.safe_load((REPO/'temp/cliproxy_profile_before_retries_20260921.yaml').read_text())
    after=yaml.safe_load(prompt.PROFILE.read_text())
    assert before['params'].pop('max_retries')==0 and after['params'].pop('max_retries')==3
    assert before==after, 'Historical model differs beyond authorized retry count'
    work=REUSED[cid];book,v=selected_version(work)
    assert book['completed'] and v['execution']
    roots=[OLD/'new'/cid/'generate',PRIOR[0]/'continued'/('new_'+cid)]
    if cid=='SF13': roots[1]=PRIOR[0]/'fresh/SF13/generate'
    roots += [PRIOR[1]/'continued'/('new_'+cid)]
    if cid!='SF03': roots += [PRIOR[2]/'continued'/('new_'+cid)]
    roots += [work]
    return dict(workspace=str(work),version=v['id'],record_hash=v['record_hash'],
        source_sha256=file_hash(Path(v['source'])),lineage=[str(p) for p in roots if p.exists()],
        initial_audit=str(first/'input_audit.json'),input_verified=True,
        caveats=['Historical execution with API interruptions and isolated continuations; not a fresh simultaneous run.',
                 'SDK max_retries changed 0 to 3; same model, core, threshold, dimensions and total edit allowance.',
                 'Continuation reviewed saved working assets and reset per-run critic history; original records retained.'],
        charged_repairs={'SF07':3,'SF03':4,'SF13':4}[cid])


def prepare(root):
    root.mkdir(parents=True,exist_ok=False)
    for arm in ('wo','w'):
        prompt.prepare(root/arm,cases=CASES,max_rounds=5,assembly_topology=arm=='w',sizes=SIZES)
    jobs=[]
    for cid in CASES:
        control=read_json(root/'wo'/cid/'input.json');feedback=read_json(root/'w'/cid/'input.json')
        assert control['original_task']==feedback['original_task']
        assert control['fixed_assembly']==feedback['fixed_assembly'] and control['source_repair_limit']==feedback['source_repair_limit']==4
        for arm in ('wo','w'):
            item=dict(case=cid,arm=arm,id=cid+'_'+arm,origin='fresh',workspace=str(root/arm/cid/'generate'))
            if arm=='w' and cid in REUSED:
                item.update(origin='historical_reuse',reuse=reuse_evidence(cid,feedback),workspace=str(REUSED[cid]))
            jobs.append(item)
    paths=(*CORE,'experiments/fixed_assembly_prompt/run.py','experiments/fixed_assembly_prompt/run_paired.py',
           'experiments/fixed_assembly_prompt/retry_api_cases.py')
    write_json(root/'paired_plan.json',dict(cases=CASES,jobs=jobs,created_at=time.time(),
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        implementation_sha256={p:file_hash(REPO/p) for p in paths},profile_sha256=file_hash(prompt.PROFILE),
        max_evaluation_rounds=5,max_source_repairs=4,api_interruptions_charged=False,
        topology_spec=checker_spec().model_dump(),comparison='independent prompt-to-3D; connector in both arms',
        note='9 fresh generations + 3 historical results, including failures. Offline scores never select assets.'))
    shutil.copy2(prompt.PROFILE,root/'model_profile.yaml')
    for p in paths:
        target=root/'implementation_snapshot'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(REPO/p,target)
    summarize(root)


def verify_frozen(root):
    plan=read_json(root/'paired_plan.json')
    assert file_hash(prompt.PROFILE)==plan['profile_sha256'], 'Frozen API profile changed'
    assert all(file_hash(REPO/p)==h for p,h in plan['implementation_sha256'].items()), 'Frozen implementation changed'
    return plan


def prepare_continuation(root, previous):
    """Explicit one-time continuation of the first-review SF07 interruption."""
    old=read_json(previous/'paired_plan.json')
    assert read_json(previous/'paused.json')['case']=='SF07_wo'
    assert {p.parent.name for p in (previous/'jobs').glob('*/generate.json')}=={'SF07_wo'}
    before=yaml.safe_load((previous/'model_profile.yaml').read_text())
    after=yaml.safe_load(prompt.PROFILE.read_text())
    assert before['params'].pop('max_retries')==3 and after['params'].pop('max_retries')==6
    assert before==after, 'Only the authorized retry change is allowed'
    changed='adsl-core/core/export/export_assembly.py'
    assert all(file_hash(REPO/p)==old['implementation_sha256'][p] for p in CORE if p!=changed)
    prior_work=previous/'wo/SF07/generate'
    assert read_json(prior_work.parent/'result.json')['repair_attempts']==0
    selected_version(prior_work)
    root.mkdir(parents=True,exist_ok=False)
    for arm in ('wo','w'):
        prompt.prepare(root/arm,cases=CASES,max_rounds=5,assembly_topology=arm=='w',sizes=SIZES)
        for cid in CASES:
            assert read_json(root/arm/cid/'input.json')==read_json(previous/arm/cid/'input.json')
    jobs=old['jobs']
    for item in jobs:
        if item['origin']=='historical_reuse':
            _,v=selected_version(Path(item['workspace']))
            assert v['record_hash']==item['reuse']['record_hash']
        else:
            item['workspace']=str(root/item['arm']/item['case']/'generate')
        if item['id']=='SF07_wo':
            item.update(origin='api_continuation',resume_from=str(prior_work))
    paths=tuple(old['implementation_sha256'])
    plan={**old,'jobs':jobs,'created_at':time.time(),
        'implementation_sha256':{p:file_hash(REPO/p) for p in paths},
        'profile_sha256':file_hash(prompt.PROFILE),'previous_batch':str(previous),
        'amendments':{'max_retries':[3,6],'export_comparison':changed,
            'scope':'Exact zero-area/duplicate encoding only; no mesh, topology or tolerance change.'},
        'note':'8 unstarted generations + interrupted SF07 initial-review continuation + 3 historical w results. No new initial generation or edit budget for SF07.'}
    write_json(root/'paired_plan.json',plan)
    shutil.copy2(prompt.PROFILE,root/'model_profile.yaml')
    for p in paths:
        target=root/'implementation_snapshot'/p;target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(REPO/p,target)
    summarize(root)


def image_payload(inputs, count):
    # No arm, topology numbers, source, model plan, or earlier Code/Image opinions.
    return dict(requirement=inputs['original_task']['prompt'],planner_checklist=[],
        round=1,max_rounds=1,reference_image_count=0,render_image_count=count,
        previous_image_decisions=[],code_critic_corrections=[],
        evaluation_instruction='Judge visible task adherence in these final views only. Do not assume missing geometry exists in code. This is a standalone final image assessment, not a repair request.')


async def evaluate(root,item):
    output=root/'offline'/item['id'];output.mkdir(parents=True,exist_ok=False)
    work=Path(item['workspace']);book,v=selected_version(work)
    if item['origin']=='historical_reuse': assert v['record_hash']==item['reuse']['record_hash']
    source,execution,_=version_assets(v)
    # Freeze selection BEFORE either evaluator; never publish back into generation.
    write_json(output/'selection.json',dict(workspace=str(work),version=v['id'],record_hash=v['record_hash'],
        source_sha256=file_hash(source),selected_by='existing retained, before offline scores',execution=v['execution']))
    result=dict(case=item['case'],arm=item['arm'],source_sha256=file_hash(source),image_status='NOT_EXECUTED',topology_status='NOT_EXECUTED')
    write_json(output/'evaluation.json',result)
    try:
        run=run_assembly_topology(checker_spec(),execution=execution,source=source,root=output)
        result.update(topology_status=run.result.status,topology_summary=run.result.summary,
            topology_items=run.result.metrics.get('items',[]))
    except Exception as error:
        result.update(topology_status='ERROR',topology_error=f'{type(error).__name__}: {error}'[:500])
    write_json(output/'evaluation.json',result)
    # A checker error never prevents an independent final image assessment.
    if execution.render_paths:
        review=output/'image';review.mkdir()
        shutil.copy2(source,review/'source.py')
        inputs=read_json(root/'wo'/item['case']/'input.json')
        request=ObjectRequest(inputs['original_task']['prompt'],review,'offline_image',max_rounds=1)
        workflow=prompt.PromptWorkflow(prompt.PROFILE)
        runtime=workflow._runtime(request,review,mode='offline_image')
        try:
            payload=image_payload(inputs,len(execution.render_paths))
            agent=runtime.agent(name='object-image-critic',instructions=object_prompt('image_critic',articulation=False),output_type=ImageCriticDecision)
            response=await runtime.run(agent=agent,input=user_input(json.dumps(payload,ensure_ascii=False),execution.render_paths),
                role='offline-image',stage='offline_image')
            decision=workflow._typed_output(response.final_output,ImageCriticDecision)
            result.update(image_status='PASS' if decision.approved else 'FAIL',image_review=decision.model_dump())
        except Exception as error:
            result.update(image_status='ERROR',image_error=f'{type(error).__name__}: {error}'[:500])
        finally:
            write_json(output/'evaluation.json',result)
    else:
        result.update(image_status='INDETERMINATE',image_error='No retained renders')
    assert_version(v)
    assert read_json(work/'assembly_versions.json')['retained']==book['retained'], 'Offline evaluation changed selection'
    write_json(output/'evaluation.json',result)


def usage(workspaces):
    calls=[read_json(p) for work in workspaces for p in sorted((Path(work)/'api_calls').glob('*.json'))]
    return dict(api_calls=len(calls),known_tokens=sum(c.get('total_tokens') or 0 for c in calls),
                unknown_usage_calls=sum(c.get('total_tokens') is None for c in calls))


def summarize(root):
    plan=read_json(root/'paired_plan.json');rows=[]
    for item in plan['jobs']:
        work=Path(item['workspace']);book=optional(work/'assembly_versions.json')
        evaluated=optional(root/'offline'/item['id']/'evaluation.json')
        history=work/'repair_history.jsonl'
        reservations=len(history.read_text().splitlines()) if history.is_file() else 0
        credits=retry_api_cases.api_interrupted_repairs(work) if item['origin']=='fresh' else []
        row=dict(case=item['case'],arm=item['arm'],origin=item['origin'],
            generation=optional(root/'jobs'/item['id']/'generate.json').get('status','REUSED' if item['origin']=='historical_reuse' else 'PENDING'),
            evaluation=optional(root/'jobs'/item['id']/'evaluate.json').get('status','PENDING'),
            retained=book.get('retained'),qualified=book.get('qualified'),stop_reason=book.get('stop_reason'),
            image_status=evaluated.get('image_status','NOT_EXECUTED'),topology_status=evaluated.get('topology_status','NOT_EXECUTED'),
            repair_attempts=item['reuse']['charged_repairs'] if item['origin']=='historical_reuse' else reservations-len(credits),
            api_interrupted_attempts=credits,workspace=str(work),
            generation_usage=usage(item['reuse']['lineage'] if item['origin']=='historical_reuse' else
                ([item['resume_from'],work] if item.get('resume_from') else [work])),
            offline_usage=usage([root/'offline'/item['id']/'image']))
        rows.append(row)
    write_json(root/'case_results.json',rows)
    with (root/'case_results.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
        writer.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in r.items()} for r in rows)
    metrics={arm:{key:dict(pass_count=sum(r[key]=='PASS' for r in rows if r['arm']==arm),
        denominator=len(CASES),statuses={s:sum(r[key]==s for r in rows if r['arm']==arm)
        for s in ('PASS','FAIL','INDETERMINATE','ERROR','NOT_EXECUTED')})
        for key in ('image_status','topology_status')} for arm in ('wo','w')}
    write_json(root/'metrics.json',metrics)


def launch_phase(root,item,phase):
    folder=root/'jobs'/item['id'];folder.mkdir(parents=True,exist_ok=True)
    status=folder/(phase+'.json')
    if status.exists():
        print('SKIP previously started',item['id'],phase,flush=True);return
    start=time.time();write_json(status,dict(status='RUNNING',started_at=start))
    print('START',item['id'],phase,flush=True)
    with (folder/(phase+'.log')).open('w') as log:
        child=subprocess.run([sys.executable,'-u',str(Path(__file__).resolve()),'--root',str(root),
            '--job',item['id'],'--phase',phase],cwd=REPO,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
    write_json(status,dict(status='FINISHED' if child.returncode==0 else 'ERROR',exit_code=child.returncode,
        started_at=start,elapsed_seconds=time.time()-start))
    summarize(root)
    print('END',item['id'],phase,'rc',child.returncode,flush=True)


def run(root):
    if not (root/'paired_plan.json').exists():prepare(root)
    plan=verify_frozen(root)
    # Complete generation/selection of ALL cases before any offline scoring.
    for item in plan['jobs']:
        if item['origin']=='historical_reuse':continue
        launch_phase(root,item,'generate')
        result=optional(root/item['arm']/item['case']/'result.json')
        audit=optional(root/item['arm']/item['case']/'input_audit.json')
        current_failures = prompt.current_geometry_failures(Path(item['workspace']), result.get('geometry_failures',[])) if result else []
        shared = (prompt.shared_export_failures(current_failures + result.get('shared_failures', []))
                  + prompt.shared_input_failures(audit))
        if shared:
            write_json(root/'paused.json',dict(case=item['id'],reason='confirmed shared fault',failures=shared))
            return
    for item in plan['jobs']:
        launch_phase(root,item,'evaluate')
    summarize(root)
    write_json(root/'completed.json',dict(completed_at=time.time(),note='Finished execution, not a claim of all passing'))


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--prepare-only',action='store_true')
    parser.add_argument('--continue-from',type=Path,help='Explicitly prepare the interrupted first-case continuation in a new root')
    parser.add_argument('--summarize',action='store_true')
    parser.add_argument('--job');parser.add_argument('--phase',choices=('generate','evaluate'))
    args=parser.parse_args();root=args.root.resolve()
    if args.continue_from:
        prepare_continuation(root,args.continue_from.resolve())
        if not args.prepare_only:run(root)
    elif args.prepare_only:prepare(root)
    elif args.summarize:summarize(root)
    elif args.job:
        plan=verify_frozen(root);item=next(j for j in plan['jobs'] if j['id']==args.job)
        try:
            if args.phase=='generate':
                assert item['origin'] in ('fresh','api_continuation'), 'Never regenerate a reused result'
                asyncio.run(prompt.run_case(root/item['arm'],item['case'],
                    resume_from=Path(item['resume_from']) if item.get('resume_from') else None))
            else:asyncio.run(evaluate(root,item))
        except Exception:
            traceback.print_exc();raise SystemExit(1)
    else:run(root)
