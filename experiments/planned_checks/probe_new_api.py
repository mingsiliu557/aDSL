"""Authorized small image/tool/schema/usage compatibility probe."""
import asyncio
from dataclasses import replace, asdict
from pathlib import Path
import time
import argparse
from agents import function_tool
from pydantic import BaseModel
from adsl.agents.utils.runner import AgentRuntime
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.io import write_json
from experiments.planned_checks.token_budget import BudgetModel
from experiments.planned_checks.run import REPO, HISTORY
import json

ROOT=REPO/'local_experiment/planned_checks_new_api'
class ProbeResult(BaseModel):
    tool_reply: str
    image_description: str

async def main(name):
    output=ROOT/name
    if (output/'started.json').exists():raise RuntimeError('probe already attempted; inspect before retry')
    runtime=AgentRuntime(model_profile=REPO/'adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml',workspace=output,task_id='api-probe')
    runtime.profile=replace(runtime.profile,max_tokens=512,timeout=60,max_retries=0)
    runtime.model=BudgetModel(runtime.profile.agent_model(workspace=output),ROOT,
        request_bound=272512,evidence={'model':'gpt-5.6-sol','purpose':'compatibility_probe'})
    if name != 'compatibility_probe' and (ROOT/'compatibility_probe/result.json').exists():
        prior=json.loads((ROOT/'compatibility_probe/result.json').read_text())
        if prior.get('type') == 'APIConnectionError':
            def acknowledge(state):
                for record in state['requests'].values():
                    if record['status']=='RESERVED' and record.get('bound_evidence',{}).get('purpose')=='compatibility_probe':
                        record.update(status='BOUNDED_UNKNOWN',actual_tokens=None,
                            review_reason='saved APIConnectionError; entire upper-bound charge retained')
            runtime.model.update(acknowledge)
    runtime.model_settings=runtime.profile.model_settings()
    called=[]
    @function_tool
    def probe_ping() -> str:
        """Read-only compatibility ping."""
        called.append(True)
        return 'probe_ok'
    agent=runtime.agent(name='probe',tools=[probe_ping],output_type=ProbeResult,
        instructions='Call probe_ping once. Return its exact reply and a brief description of the attached image.')
    picture=sorted((HISTORY/'workspaces/adsl/SF03/render').glob('*.png'))[0]
    start=time.time();write_json(output/'started.json',{'at':start})
    try:
        result=await runtime.run(agent=agent,input=user_input('Perform the compatibility probe.',(picture,)),
            role='probe',stage='probe',max_turns=3)
        value=result.final_output
        row={'status':'PASS' if called and value.tool_reply=='probe_ok' else 'FAIL',
            'output':value.model_dump(),'usage':asdict(runtime.usage.totals())}
    except Exception as error:
        row={'status':'ERROR','type':type(error).__name__,'reason':str(error)[:300]}
    row.update(seconds=time.time()-start,tool_calls=len(called))
    write_json(output/'result.json',row);print(row,flush=True)
    if row['status']!='PASS':raise SystemExit(1)

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--name',default='compatibility_probe')
    asyncio.run(main(parser.parse_args().name))
