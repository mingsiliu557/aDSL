"""SF13 saved six-piece asset: measure first; optionally ONE existing-loop repair.

No Planner, initial generation, other physics, manual source edits or new budget.
The output directory is new. --repair consumes the saved measurement, never
re-generates the initial asset to measure it a second time.
"""
from __future__ import annotations
import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import subprocess
import sys

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from adsl.agents.assembly_topology import checker_spec,run_assembly_topology
from adsl.agents.checkers import CheckerRun
from adsl.agents.fixed_assembly import iterate_fixed_assembly
from adsl.agents.models import FixedAssemblyPlan,ObjectRequest,CheckerResult
from adsl.agents.overhang_edit import file_hash
from adsl.agents.utils.execution import ExecutionResult
from adsl.agents.utils.io import read_json,write_json
from experiments.fixed_assembly_prompt.run import PromptWorkflow

DEFAULT_PROFILE=REPO/'adsl-agents/configs/llm/stepcode-gpt-5.6-sol.yaml'

def initial_execution(root):
    asset=root/'rounds/round_01/asset'
    index=asset/'source_index.json'
    return ExecutionResult(asset,asset/'assembly/scene.glb',None,
        tuple(sorted((asset/'render').glob('*.png'))),'','',
        source_index_path=index if index.is_file() else None)


def measure(saved,output,profile=DEFAULT_PROFILE):
    profile=profile.resolve()
    profile_hash=file_hash(profile)
    output.mkdir(parents=True,exist_ok=False)
    generate=saved/'generate'
    shutil.copy2(generate/'source.py',output/'source.py')
    shutil.copy2(generate/'plan.json',output/'plan.json')
    asset=output/'rounds/round_01/asset';asset.mkdir(parents=True)
    for name in ('assembly','render'): shutil.copytree(generate/name,asset/name)
    if (generate/'source_index.json').exists(): shutil.copy2(generate/'source_index.json',asset/'source_index.json')
    original_input=read_json(saved/'input.json')
    record={'source_origin':str(generate/'source.py'),'source_sha256':file_hash(output/'source.py'),
        'fixed_assembly':original_input['fixed_assembly'],
        'requirement':read_json(generate/'runtime_config.json')['request']['requirement'],
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'checker_spec':checker_spec().model_dump(),'initial_generations':0,'max_source_repairs':1,
        'max_rounds':2,'api_profile':profile.stem,'llm_config':str(profile),
        'llm_config_sha256':profile_hash,
        'disabled_checkers':['topology','fea','standing','overhang'],
        'file_sha256':{p:file_hash(REPO/p) for p in ('adsl-core/core/assembly.py',
            'adsl-core/core/assembly_topology.py','adsl-agents/assembly_topology.py','adsl-agents/fixed_assembly.py')}}
    write_json(output/'input.json',record)
    run=run_assembly_topology(checker_spec(),execution=initial_execution(output),
        source=output/'source.py',root=output/'rounds/round_01')
    write_json(output/'baseline_result.json',run.result.model_dump())
    print(json.dumps({'status':run.result.status,'output':str(output),'items':[
        {k:r.get(k) for k in ('part_id','connection_id','status','code','component_count','reason')}
        for r in run.result.metrics.get('items',[])]},ensure_ascii=False,indent=2),flush=True)


async def repair(output):
    record=read_json(output/'input.json')
    baseline=CheckerResult.model_validate(read_json(output/'baseline_result.json'))
    from adsl.agents.service import _actionable_findings
    if not _actionable_findings(CheckerRun(checker_spec(),baseline,output,())):
        print('No confirmed failure or localized repairable open mesh; do not call API.',flush=True);return
    if (output/'assembly_versions.json').exists():
        raise ValueError('Repair already attempted; do not reset or replay the budget')
    config={**record['fixed_assembly'],'validation_mode':'visual_only'}
    req=record['requirement']+ ('\nCURRENT RUN OVERRIDE: this is an existing-asset repair check, not generation.'
        ' Only assembly_topology is enabled, in addition to Image/Code and export consistency.'
        ' Existing geometry mode is off. At most ONE source edit, two evaluations, no Planner.'
        ' Apply minimal body/interface/assembly changes using measured evidence; preserve original task and frozen conditions.')
    if (output/'boundary_localization.json').exists():
        req += (' Initial-source-only boundary localization is saved in boundary_localization.json; '
                'read it for measured positions and verified source ranges. This does not establish '
                'a Boolean-kernel cause or prove the interfaces disconnected. Current mesh feedback governs recheck.')
    request=ObjectRequest(req,output,'SF13_assembly_topology',max_rounds=2,fixed_assembly=config,
        checker_specs=(checker_spec(),))
    write_json(output/'repair_request.json',asdict(request))
    profile=Path(record.get('llm_config',DEFAULT_PROFILE))
    if record.get('llm_config_sha256') and file_hash(profile)!=record['llm_config_sha256']:
        raise ValueError('Frozen model profile changed')
    workflow=PromptWorkflow(profile)
    runtime=workflow._runtime(request,output,mode='generate')
    plan=FixedAssemblyPlan.model_validate(read_json(output/'plan.json'))
    run=CheckerRun(checker_spec(),baseline,output/'rounds/round_01/checkers/assembly_topology',())
    result=await iterate_fixed_assembly(workflow,runtime=runtime,request=request,workspace=output,
        source_path=output/'source.py',plan=plan,initial_execution=initial_execution(output),initial_topology_run=run)
    write_json(output/'run_result.json',{'approved':result.approved,'usage':runtime.usage.totals(),
        'initial_generations':0,'original_source_unchanged':file_hash(Path(record['source_origin']))==record['source_sha256']})


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--saved-case',type=Path)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--repair',action='store_true')
    parser.add_argument('--llm-config',type=Path,
        help='Freeze model profile when preparing a new measurement; repair reuses the recorded profile')
    args=parser.parse_args()
    if args.repair and args.llm_config: parser.error('--llm-config is selected during initial preparation only')
    if args.repair: asyncio.run(repair(args.output.resolve()))
    elif args.saved_case: measure(args.saved_case.resolve(),args.output.resolve(),args.llm_config or DEFAULT_PROFILE)
    else: parser.error('--saved-case is required for initial measurement')
