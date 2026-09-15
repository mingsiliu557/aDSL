"""Bounded plan-only/measurement delivery. Mixed editing deliberately not enabled."""
from __future__ import annotations
import argparse
import asyncio
import csv
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from openai import APIError
from agents.exceptions import ModelBehaviorError

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from adsl.agents.planned_checks import ToolPlan, resolve_plan, verify_plan, PLANNER_INSTRUCTIONS, PlanValidationError, FEA_SCREENING_ASSUMPTIONS
from adsl.agents.planned_checks import resolve_partial_plan, keep_valid_plan_entries, ToolPlanOutputSchema
from adsl.agents.models import CheckerSpec, CheckerResult
from adsl.agents.checkers import run_checker
from adsl.agents.overhang_edit import original_execution
from adsl.agents.utils.runner import AgentRuntime
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.io import write_json
from experiments.overhang_feedback import run_pilot as overhang
from experiments.planned_checks.token_budget import BudgetModel

IDS = ('SF01','SF03','SF05','SF06','SF07','SF11','SF13','SF16','SF20','SF21','SF25','SF27')
HISTORY = REPO / 'local_experiment/topology_standing_fea_12_cpu_20260912T100549Z'
DEFAULT_PROFILE = REPO / 'adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml'

def measurement_case_ids(phase):
    smoke = ('SF03', 'SF07', 'SF27')
    return smoke if phase == 'smoke' else smoke + tuple(cid for cid in IDS if cid not in smoke)

def read(p):
    return json.loads(p.read_text())

def sha(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()

def hashes(folder):
    return {str(p.relative_to(folder)): sha(p) for p in sorted(folder.rglob('*')) if p.is_file()}

def csv_write(p, rows, fields):
    with p.with_suffix('.tmp').open('w') as f:
        w = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        w.writeheader(); w.writerows(rows)
    p.with_suffix('.tmp').replace(p)

def registry(case, folder):
    entries = {}
    category = case['category']
    choices = [('topology_one_piece','topology',REPO/'experiments/workflow_checkers/specs/topology_one_piece.json'),
               ('standing_natural_settle','standing',REPO/'experiments/workflow_checkers/specs/standing.json'),
               ('fea_'+category,'fea',REPO/f'experiments/standing_fea_30/specs/fea_{category}.json')]
    for pid, tool, path in choices:
        spec = read(path)
        config = read(Path(spec['command'][-1].replace('{project_root}',str(REPO))))
        dest = folder / (pid+'.json')
        write_json(dest, config)
        spec['command'][-1] = str(dest)
        if tool == 'fea': spec['timeout_seconds'] = 900
        entries[pid] = {'tool':tool,'applicable':True,'configuration':config,'spec':spec,
            'config_path':str(dest), 'config_sha256':sha(dest),
            'description': 'Existing screening assumption, not an inferred real-world load. Select only if appropriate to prompt; otherwise NEEDS_SPEC. Topology one_piece applies only to a required continuous object.'}
    cfg = overhang.case_config(case)
    cfg['case']['exterior_method'] = 'manifold_union'
    dest = folder/'overhang_print.json';write_json(dest,cfg)
    entries['overhang_fixed_print'] = {'tool':'overhang','applicable':True,'configuration':cfg,
        'spec':overhang.checker_spec(dest), 'config_path':str(dest),'config_sha256':sha(dest),
        'description':'Fixed printing-only scale, 180 mm maximum original extent, Z up; not FEA use scale. Manifold, known cross-backend discrepancy.'}
    return entries

def prepare(root, input_arm='ours'):
    root.mkdir(parents=True, exist_ok=True)
    marker = root/'batch.json'
    # Refuse a silently misdirected output root.
    if not any(root.resolve().is_relative_to(base.resolve()) for base in
               (Path('/jiigan-hp/lms/aDSL/experiment'), REPO/'local_experiment')):
        raise ValueError('outputs must be in the experiment data root or project local_experiment')
    manifest = read(REPO/'experiments/standing_fea_30/case_manifest.json')
    cases = {c['case_id']:c for c in manifest['cases']}
    protected = {c['case_id']:c['protection'] for c in read(REPO/'experiments/overhang_feedback/paired_assets.json')['cases']}
    start = time.time()
    state = {'started_at':start,'deadline':start+8*3600,'mode':'PLAN_AND_MEASURE_ONLY',
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'token_limits':{'cliproxy':100_000_000,'stepcode':None},'case_seconds':1800,'max_edit_attempts':2,'cases':list(IDS)}
    # Source snapshot establishes the actual dirty runtime, without modifying it.
    files = list((REPO/'adsl-agents').rglob('*.py')) + list((REPO/'experiments/planned_checks').glob('*.py'))
    state['code_hashes']={str(p.relative_to(REPO)):sha(p) for p in files}
    if marker.exists():
        state=read(marker)
    else:
        write_json(marker,state)
    for cid in IDS:
        folder=root/cid;folder.mkdir(exist_ok=True)
        if (folder/'state.json').exists():continue
        src=HISTORY/'workspaces'/input_arm/cid
        info={'case_id':cid,'historical_asset':str(src),'case':cases[cid],
              'protection':protected.get(cid),'planning_seconds':0,'edit_count':0,
              'edit_status':'MIXED_EDIT_NOT_IMPLEMENTED' if cid in protected else 'EDIT_NOT_READY'}
        required=[src/'source.py',src/'scene.glb',src/'scene.urdf']
        renders=sorted((src/'render').glob('*.png'))
        if not all(p.is_file() for p in required) or not renders:
            info['status']='INPUT_UNAVAILABLE';write_json(folder/'state.json',info);continue
        try:
            for node in ET.parse(src/'scene.urdf').iter('mesh'):
                path=(src/node.attrib['filename']).resolve()
                if not path.is_relative_to(src.resolve()) or not path.is_file():
                    raise ValueError('URDF reference missing/outside asset')
            index=src/'source_index.json'
            if index.is_file():
                expected=read(index).get('source_sha256')
                if expected and expected!=sha(src/'source.py'):
                    raise ValueError('source/index version mismatch')
            original=folder/'original';original.mkdir(exist_ok=True)
            for p in required+list(src.glob('source_index.json')):
                shutil.copy2(p,original/p.name)
            for name in ('render','meshes'):
                if (src/name).is_dir():shutil.copytree(src/name,original/name,dirs_exist_ok=True)
            info.update(status='INPUT_READY',input_hashes=hashes(original),retained='original')
            write_json(folder/'registry.json',registry(cases[cid],folder/'profiles'))
        except (ValueError,OSError,ET.ParseError) as e:
            info.update(status='INPUT_UNAVAILABLE',reason=str(e)[:300])
        write_json(folder/'state.json',info)
    return state

async def plan_case(root,cid,profile):
    folder=root/cid;state=read(folder/'state.json')
    if state['status']!='INPUT_READY' or (folder/'tool_plan.resolved.json').exists():return
    if (folder/'planning_started.json').exists():
        raise RuntimeError('interrupted planning is not automatically replayed')
    original=folder/'original'
    if hashes(original)!=state['input_hashes']:raise ValueError('input changed')
    reg=read(folder/'registry.json')
    runtime=AgentRuntime(model_profile=profile,workspace=folder/'planner',task_id=cid,
        session_database_root=Path('/tmp/adsl-planned-checks-sessions'))
    session_path=runtime.sessions.database_path
    snapshot=folder/'planner/session_snapshot.json'
    if snapshot.exists() and not session_path.exists():
        raise RuntimeError('local session lost; saved transcript requires reviewed recovery, not silent reset')
    write_json(folder/'planner/session_storage.json',{'database_path':str(session_path),
        'durability':'temporary local runtime; transcript snapshot on data disk',
        'previous_shared_database_preserved':True})
    if runtime.profile.model!='gpt-5.6-sol':raise ValueError('unexpected model')
    scope = {'http://127.0.0.1:44949/v1':'stepcode', 'http://127.0.0.1:8317/v1':'cliproxy'}.get(runtime.profile.base_url)
    if scope is None:raise ValueError('unknown API endpoint')
    if scope == 'cliproxy' and read(REPO/'local_experiment/planned_checks_new_api/compatibility_probe_persistent/result.json')['status']!='PASS':
        raise ValueError('new API compatibility probe has not passed')
    # Disable SDK retries before creating the budgeted model.
    runtime.profile=replace(runtime.profile,max_retries=0)
    model=runtime.profile.agent_model(workspace=folder/'planner')
    cache=Path('/vepfs_default/chanxueyan/lhp/lms/.codex/models_cache.json')
    meta=next(m for m in read(cache)['models'] if m['slug']=='gpt-5.6-sol')
    context=int(meta['context_window'])
    if context!=272000:raise ValueError('model context bound changed; review before calling')
    runtime.model=BudgetModel(model,root if scope=='stepcode' else REPO/'local_experiment/planned_checks_new_api',request_bound=context+int(runtime.profile.max_tokens or 32768),budget_scope=scope,
        evidence={'model':'gpt-5.6-sol','context_window':context,'metadata_sha256':sha(cache),
                  'scope':'local model requests, not independent upstream proxy/account billing'})
    agent=runtime.agent(name='initial-tool-planner',tools=(),output_type=ToolPlan,
        instructions=PLANNER_INSTRUCTIONS)
    agent.output_type=ToolPlanOutputSchema()
    payload={'prompt':state['case']['prompt'],'current_source':(original/'source.py').read_text(),
             'protection':state['protection'],'profiles':reg,'required':['topology','overhang'],
             'fea_screening_assumptions':FEA_SCREENING_ASSUMPTIONS}
    started=time.time();write_json(folder/'planning_started.json',{'at':started})
    try:
        errors=[];resolved=None
        for attempt in range(2):
            result=await runtime.run(agent=agent,input=user_input(json.dumps({**payload,'validation_errors':errors}),
                tuple(sorted((original/'render').glob('*.png')))),role='planner',stage=f'plan_{attempt}',max_turns=1)
            proposal=result.final_output
            candidate=proposal.model_dump() if isinstance(proposal,ToolPlan) else proposal
            write_json(folder/f'tool_plan.proposed.{attempt}.json',candidate)
            write_json(folder/'tool_plan.proposed.json',candidate)
            if resolved is not None:candidate=keep_valid_plan_entries(resolved,candidate)
            resolved=resolve_partial_plan(candidate,reg)
            errors=resolved['errors']
            write_json(folder/f'tool_plan.validation.{attempt}.json',{'errors':errors,
                'status':resolved['status'],'warnings':resolved['warnings'],'corrections_remaining':1-attempt})
            write_json(folder/f'tool_plan.partial.{attempt}.json',resolved)
            if errors and attempt==0:continue
            write_json(folder/'tool_plan.resolved.json',resolved)
            state.update(status=resolved['status'],reason='; '.join(errors));break
    except Exception as e:
        state.update(status='PLAN_ERROR',reason=f'{type(e).__name__}: {str(e)[:250]}')
        raise
    finally:
        state['planning_seconds']=state.get('planning_seconds',0)+time.time()-started;write_json(folder/'state.json',state)
        # Archive plain data, never open an active WAL database on the shared mount.
        session=None
        try:
            session=runtime.sessions.for_role('planner')
            write_json(snapshot,await session.get_items())
        except Exception as e:
            write_json(folder/'planner/session_snapshot_error.json',{'type':type(e).__name__})
        finally:
            if session is not None:session.close()

def measure_case(root,cid):
    folder=root/cid;state=read(folder/'state.json')
    if state['status'] not in ('PLAN_VALID','PLAN_PARTIAL','PLAN_INVALID','MEASURING'):return
    if not (folder/'tool_plan.resolved.json').exists():
        # Legacy failed plans have no frozen per-tool decisions; do not guess them.
        state['reason']='NO_RESOLVED_PLAN';write_json(folder/'state.json',state);return
    plan=read(folder/'tool_plan.resolved.json');verify_plan(plan)
    if hashes(folder/'original')!=state['input_hashes']:raise ValueError('original asset changed')
    reg=read(folder/'registry.json')
    for p in reg.values():
        if sha(Path(p['config_path']))!=p['config_sha256']:raise ValueError('frozen profile changed')
    state.setdefault('measurement_deadline',min(read(root/'batch.json')['deadline'],time.time()+1800-state['planning_seconds']))
    state.update(status='MEASURING');write_json(folder/'state.json',state)
    execution=original_execution({'original_urdf':str(folder/'original/scene.urdf')})
    observed={}
    for tool in sorted(plan['tools'],key=lambda t:('topology','standing','overhang','fea').index(t['name'])):
        name=tool['name'];record=folder/f'measurement_{name}.json'
        if record.exists():observed[name]=read(record);continue
        if not tool['selected']:
            row={'checker':name,'status':tool['status'],'reason':tool['reason']}
        elif name=='fea' and observed.get('topology',{}).get('status')!='PASS':
            row={'checker':name,'status':'INDETERMINATE','reason':'TOPOLOGY_DEPENDENCY_UNAVAILABLE'}
        else:
            spec=CheckerSpec.model_validate(tool['spec'])
            if time.time()+spec.timeout_seconds+5>state['measurement_deadline']:
                row={'checker':name,'status':'INDETERMINATE','reason':'BUDGET_NOT_EXECUTED'}
            else:
                start_marker=folder/f'measurement_{name}.started.json'
                if start_marker.exists():
                    row={'checker':name,'status':'INDETERMINATE','reason':'INTERRUPTED_NOT_REPLAYED'}
                else:
                    write_json(start_marker,{'at':time.time()});start=time.time()
                    run=run_checker(spec,execution=execution,source_path=folder/'original/source.py',round_root=folder/'baseline')
                    row={**run.result.model_dump(),'elapsed_seconds':time.time()-start,
                         'report_path':str(run.output_dir/'result.json')}
        write_json(record,row);observed[name]=row
    state.update(status='MEASURED',retained='original',joint_pass=False,stop_reason='MEASUREMENT_ONLY_NO_EDITS_OR_NEW_VISUAL_REVIEW')
    if hashes(folder/'original') != state['input_hashes']:
        raise ValueError('measurement mutated original asset')
    write_json(folder/'state.json',state)

async def plan_case_bounded(root, cid, profile):
    folder = root / cid
    elapsed = read(folder/'state.json').get('planning_seconds', 0)
    remaining = min(1800-elapsed, read(root/'batch.json')['deadline']-time.time())
    try:
        await asyncio.wait_for(plan_case(root, cid, profile), timeout=max(.01, remaining))
    except (APIError, ModelBehaviorError, asyncio.TimeoutError) as error:
        # Known request/output failures affect this case, not independent plans.
        # Do not retry, reset its start marker, or swallow programming/file errors.
        state = read(folder/'state.json')
        reason = f'{type(error).__name__}: {str(error)[:250]}'
        state.update(status='PLAN_ERROR', reason=reason)
        partials = sorted(folder.glob('tool_plan.partial.*.json'))
        if partials:
            resolved = read(partials[-1]); verify_plan(resolved)
            write_json(folder/'tool_plan.resolved.json', resolved)
            state.update(status=resolved['status'], planning_error=reason)
        write_json(folder/'state.json', state)
        write_json(folder/'planning_failure.json', {'stage':'planner_request_or_output',
            'reason':reason, 'retry_performed':False, 'partial_plan_preserved':bool(partials)})
        print(f'planning unavailable {cid}: {type(error).__name__}; continuing independent cases', flush=True)

def summarize(root):
    plans=[];results=[];costs=[]
    for cid in IDS:
        folder=root/cid
        if not (folder/'state.json').exists():continue
        state=read(folder/'state.json')
        plan=read(folder/'tool_plan.resolved.json') if (folder/'tool_plan.resolved.json').exists() else {}
        plans.append({'case_id':cid,'status':state['status'],'object_use':plan.get('object_use'),
            'tools':json.dumps(plan.get('tools',[]),ensure_ascii=False),'reason':state.get('reason'),'edit_status':state['edit_status']})
        for name in ('topology','standing','overhang','fea'):
            p=folder/f'measurement_{name}.json';r=read(p) if p.exists() else {}
            results.append({'case_id':cid,'checker':name,'status':r.get('status','NOT_EXECUTED'),
                'reason':r.get('reason',r.get('summary')),'retained':state.get('retained'),
                'metrics':json.dumps(r.get('metrics',{})),'edits':0,'appearance':'NOT_REVIEWED','protection':state['edit_status']})
            if 'elapsed_seconds' in r:costs.append({'case_id':cid,'stage':name,'seconds':r['elapsed_seconds']})
        p=folder/'planner/usage.jsonl'
        if p.exists():
            for line in p.read_text().splitlines():costs.append({'case_id':cid,**json.loads(line)})
    csv_write(root/'tool_plan_table.csv',plans,['case_id','status','object_use','tools','reason','edit_status'])
    csv_write(root/'case_results.csv',results,['case_id','checker','status','reason','retained','metrics','edits','appearance','protection'])
    csv_write(root/'costs.csv',costs,['case_id','stage','requests','input_tokens','output_tokens','total_tokens','seconds'])
    (root/'REPORT.md').write_text('# Planned checks: bounded first delivery\n\n'
        'Plan and baseline measurement only. Mixed editing is not enabled; no candidate accepted or asset republished. '
        'Original assets retained. No new visual review, no joint physical approval. '
        'Missing/invalid plans and skipped checks remain in CSV denominators. '
        'Manifold backend discrepancy remains unresolved. Runtime/token ledgers accompany this report.\n\n'
        '| Case | Stage status | Editing readiness |\n|---|---|---|\n'+''.join(
            f"| {r['case_id']} | {r['status']} | {r['edit_status']} |\n" for r in plans))

def main():
    ap=argparse.ArgumentParser();ap.add_argument('phase',choices=['plan-only','measure-only','smoke','batch','summarize'])
    ap.add_argument('--output',type=Path,required=True);ap.add_argument('--model-profile',type=Path,default=DEFAULT_PROFILE)
    args=ap.parse_args();root=args.output.resolve();prepare(root)
    if args.phase != 'summarize':
        batch=read(root/'batch.json')
        current={p:sha(REPO/p) for p in batch['code_hashes']}
        if 'execution_code_hashes' in batch and current!=batch['execution_code_hashes']:
            raise ValueError('execution code changed since batch start; review before resuming')
        batch['execution_code_hashes']=current;write_json(root/'batch.json',batch)
    if args.phase=='batch':raise SystemExit('BLOCKED: mixed-edit smoke not implemented; do not expand automatically')
    try:
        if args.phase=='plan-only':
            for cid in IDS:
                if time.time()>read(root/'batch.json')['deadline']:break
                print('planning',cid,flush=True)
                asyncio.run(plan_case_bounded(root,cid,args.model_profile));summarize(root)
                # Tool-level invalidity is recorded, not a batch-stopping workflow failure.
        elif args.phase in ('measure-only','smoke'):
            for cid in measurement_case_ids(args.phase):
                print('baseline',cid,flush=True);measure_case(root,cid);summarize(root)
    finally:summarize(root)

if __name__=='__main__':main()
