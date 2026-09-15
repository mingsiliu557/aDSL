"""Explicit one-request SF03 format validation; no checker, edit or retry."""
import argparse
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from adsl.agents.planned_checks import ToolPlan,PLANNER_INSTRUCTIONS,resolve_plan,PlanValidationError,FEA_SCREENING_ASSUMPTIONS
from adsl.agents.planned_checks import resolve_partial_plan, ToolPlanOutputSchema
from adsl.agents.utils.runner import AgentRuntime
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.io import write_json
from experiments.planned_checks.run import DEFAULT_PROFILE,read,sha,hashes
from experiments.planned_checks.token_budget import BudgetModel


async def run(root,output):
    case=root/'SF03';state=read(case/'state.json');original=case/'original'
    if hashes(original)!=state['input_hashes']:raise ValueError('saved input changed')
    remaining=min(read(root/'batch.json')['deadline']-time.time(),1800-state['planning_seconds'])
    if remaining<=0:raise ValueError('existing time budget exhausted')
    output.mkdir(parents=True,exist_ok=False)
    started=time.time()
    manifest={'case_id':'SF03','scope':'one new planning request; zero corrections/checkers/edits',
        'status':'STARTED','started_at':started,'timeout_seconds':min(900,remaining),
        'previous_planning_seconds':state['planning_seconds'],'original_input_hashes':state['input_hashes'],
        'instructions':PLANNER_INSTRUCTIONS,'plan_schema':ToolPlan.model_json_schema(),
        'code_hashes':{str(p.relative_to(REPO)):sha(p) for p in [Path(__file__),REPO/'adsl-agents/planned_checks.py',REPO/'experiments/planned_checks/token_budget.py']}}
    write_json(output/'verification.json',manifest)
    runtime=None
    try:
        runtime=AgentRuntime(model_profile=DEFAULT_PROFILE,workspace=output/'runtime',task_id='SF03-format-check',
            session_database_root=Path('/tmp/adsl-planned-checks-sessions'))
        runtime.profile=replace(runtime.profile,max_retries=0)
        cache=Path('/vepfs_default/chanxueyan/lhp/lms/.codex/models_cache.json')
        metadata=next(m for m in read(cache)['models'] if m['slug']=='gpt-5.6-sol')
        if metadata['context_window']!=272000:raise ValueError('review model bound before calling')
        runtime.model=BudgetModel(runtime.profile.agent_model(workspace=output/'runtime'),root,
            request_bound=272000+32768,budget_scope='stepcode',evidence={'model':'gpt-5.6-sol','metadata_sha256':sha(cache),
            'scope':'SF03 single format verification; local model usage, not upstream account quota'})
        agent=runtime.agent(name='initial-tool-planner',instructions=PLANNER_INSTRUCTIONS,tools=(),output_type=ToolPlan)
        agent.output_type=ToolPlanOutputSchema()
        reg=read(case/'registry.json')
        payload={'prompt':state['case']['prompt'],'current_source':(original/'source.py').read_text(),
            'protection':state['protection'],'profiles':reg,'required':['topology','overhang'],'validation_errors':[],
            'fea_screening_assumptions':FEA_SCREENING_ASSUMPTIONS}
        result=await asyncio.wait_for(runtime.run(agent=agent,
            input=user_input(json.dumps(payload),tuple(sorted((original/'render').glob('*.png')))),
            role='planner',stage='SF03_single_format_validation',max_turns=1),timeout=min(900,remaining))
        proposal=result.final_output
        write_json(output/'tool_plan.proposed.json',proposal.model_dump() if isinstance(proposal,ToolPlan) else proposal)
        resolved=resolve_partial_plan(proposal,reg)
        write_json(output/'tool_plan.resolved.json',resolved)
        manifest.update(status=resolved['status'],errors=resolved['errors'])
    except Exception as e:
        manifest.update(status='ERROR',error=f'{type(e).__name__}: {str(e)[:300]}')
    finally:
        manifest['elapsed_seconds']=time.time()-started
        write_json(output/'verification.json',manifest)
        print(json.dumps(manifest | {'instructions':'saved in verification.json','plan_schema':'saved','code_hashes':'saved','original_input_hashes':'saved'},ensure_ascii=False),flush=True)
    return 0 if manifest['status'] in ('PLAN_VALID','PLAN_PARTIAL','PLAN_INVALID') else 1


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--batch',type=Path,required=True);p.add_argument('--output',type=Path,required=True)
    a=p.parse_args();raise SystemExit(asyncio.run(run(a.batch.resolve(),a.output.resolve())))
