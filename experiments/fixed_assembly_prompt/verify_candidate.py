"""One saved source -> visual/code review -> bounded isolated repairs."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from adsl.agents.fixed_assembly import iterate_fixed_assembly
from adsl.agents.models import FixedAssemblyPlan, ObjectRequest
from adsl.agents.overhang_edit import file_hash, assert_version
from adsl.agents.utils.io import read_json,write_json
from experiments.fixed_assembly_prompt.run import PromptWorkflow


async def main(saved_case, source, output, max_rounds=2):
    if not 1 <= max_rounds <= 5:
        raise ValueError('max_rounds must be between 1 and 5')
    output.mkdir(parents=True,exist_ok=False)  # Never replay/overwrite an old attempt.
    saved=read_json(saved_case/'input.json')
    config={**saved['fixed_assembly'], 'validation_mode':'visual_only'}
    plan=FixedAssemblyPlan.model_validate(read_json(saved_case/'generate/plan.json'))
    requirement=read_json(saved_case/'generate/runtime_config.json')['request']['requirement']
    requirement += ('\nCURRENT VALIDATION OVERRIDE: reuse this saved candidate, with validation_mode=visual_only.'
        ' Geometry, connectivity and all four physical checkers are disabled, not failed.'
        ' Keep connector generation and placement; judge visual assembly with Image/Code Critic.'
        ' Retain frozen dimensions and fit allowance, but do not claim geometric or physical validation.'
        f' CURRENT RUN BUDGET OVERRIDE: at most {max_rounds} evaluation rounds, including the initial'
        f' evaluation, and at most {max_rounds-1} source edits; this replaces any older one-repair wording.'
        ' Stop normally on approval or explicit no change; do not force edits to exhaust the budget.')
    original_hash=file_hash(source)
    current=output/'source.py';shutil.copy2(source,current)
    changed=('adsl-core/core/export/export_assembly.py','adsl-agents/fixed_assembly.py',
             'adsl-agents/service.py','adsl-agents/utils/asset_executor.py')
    write_json(output/'input.json',{'route':'saved_failed_source_validation','source':str(source),
        'source_sha256':original_hash,'fixed_assembly':config,
        'profile':'stepcode-gpt-5.6-sol', 'geometry_validation':'NOT_EVALUATED',
        'initial_generations':0,'max_rounds':max_rounds,'max_source_repairs':max_rounds-1,'physical_checkers':[],
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'file_sha256':{p:file_hash(REPO/p) for p in changed}})
    write_json(output/'plan.json',plan.model_dump())
    request=ObjectRequest(requirement,output,'assembly_diagnostic_single',max_rounds=max_rounds,
                          fixed_assembly=config,checker_specs=())
    workflow=PromptWorkflow(REPO/'adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml')
    runtime=workflow._runtime(request,output,mode='generate')
    started=time.time();result=None;error=None
    print(f'Saved candidate: at most {max_rounds} rounds / {max_rounds-1} repairs. No Planner/generation.',flush=True)
    try:
        result=await iterate_fixed_assembly(workflow,runtime=runtime,request=request,
            workspace=output,source_path=current,plan=plan)
    except Exception as exc:
        error={'type':type(exc).__name__,'reason':str(exc)[:400]}
        (output/'error.log').write_text(traceback.format_exc())
    book=read_json(output/'assembly_versions.json') if (output/'assembly_versions.json').exists() else {}
    for record in book.get('versions',{}).values(): assert_version(record)
    calls=[read_json(p) for p in sorted((output/'api_calls').glob('*.json'))]
    summary={'flow_completed':result is not None,'visual_code_approved':bool(result and result.approved),
        'geometry_validation':'NOT_EVALUATED',
        'stop_reason':book.get('stop_reason'),'working_version':book.get('working'),
        'qualified_version':book.get('qualified'),'retained_version':book.get('retained'),
        'initial_generations':0,'repair_attempts':max(0,len(book.get('versions',{}))-1),
        'max_rounds':max_rounds,'max_source_repairs':max_rounds-1,
        'elapsed_seconds':time.time()-started,'error':error,'source_unchanged':file_hash(source)==original_hash,
        'api_calls':[{k:c.get(k) for k in ('stage','status','elapsed_seconds','input_tokens','output_tokens','total_tokens')} for c in calls],
        'known_tokens':sum(c.get('total_tokens') or 0 for c in calls),
        'unknown_usage_calls':sum(c.get('total_tokens') is None for c in calls),'physical_checkers':'NOT_EXECUTED'}
    write_json(output/'result.json',summary)
    print(json.dumps(summary,ensure_ascii=False,indent=2),flush=True)
    return int(error is not None or book.get('stop_reason')=='FLOW_ERROR')


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--saved-case',type=Path,required=True)
    parser.add_argument('--source',type=Path,required=True)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--max-rounds',type=int,choices=range(1,6),default=2,
                        help='Total review rounds, including initial evaluation; default 2, maximum 5')
    args=parser.parse_args()
    raise SystemExit(asyncio.run(main(args.saved_case.resolve(),args.source.resolve(),args.output.resolve(),args.max_rounds)))
