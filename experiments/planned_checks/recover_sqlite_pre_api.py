"""Explicit one-off recovery of the diagnosed first-request SQLite failure.

Never replay a case with a token reservation, usage, proposal, or measurement.
Keeps the original deadline, elapsed planning time, assets, and failed SQLite files.
"""
import argparse
import json
from pathlib import Path
import sys
import time

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from adsl.agents.utils.io import write_json
from experiments.planned_checks.run import read,sha,hashes


def recover(root):
    folder=root/'SF01'
    state=read(folder/'state.json');batch=read(root/'batch.json')
    if (root/'sqlite_recovery.json').exists():
        raise ValueError('recovery already performed; no repeated resets')
    if state.get('status')!='PLAN_ERROR' or state.get('reason')!='OperationalError: disk I/O error':
        raise ValueError('not the diagnosed pre-API SQLite failure')
    if time.time()>=batch['deadline']:
        raise ValueError('batch deadline exhausted; do not reset it')
    if (any((root/name).exists() for name in ('token_budget.json','stepcode_usage.json','cliproxy_token_budget.json')) or list(root.glob('SF*/planner/usage.jsonl'))
            or list(root.glob('SF*/tool_plan.proposed*.json'))
            or list(root.glob('SF*/measurement_*.json'))):
        raise ValueError('API/evaluation evidence exists; automatic replay is unsafe')
    if hashes(folder/'original')!=state['input_hashes']:
        raise ValueError('original asset changed')
    marker=folder/'planning_started.json'
    if not marker.exists():raise ValueError('missing old start evidence')
    current={p:sha(REPO/p) for p in batch['code_hashes']}
    write_json(root/'sqlite_recovery.json',{'at':time.time(),'failed_state':state,
        'failed_planning_start':read(marker),'previous_execution_code_hashes':batch.get('execution_code_hashes'),
        'new_execution_code_hashes':current,'deadline_unchanged':batch['deadline'],
        'reason':'confirmed SDK input persistence failure before first model invocation; use local SQLite'})
    marker.rename(folder/'planning_started.failed_sqlite.json')
    state.update(status='INPUT_READY',previous_error=state.pop('reason'),sqlite_recovered=True)
    write_json(folder/'state.json',state)
    batch['execution_code_hashes']=current;write_json(root/'batch.json',batch)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True,type=Path)
    recover(p.parse_args().output.resolve())
