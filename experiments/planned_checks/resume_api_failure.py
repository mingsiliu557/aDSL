"""Reviewed one-off resume: keep failed cases, frozen plans and all budgets."""
import argparse
import time

from experiments.planned_checks.run import REPO, read, sha, hashes, write_json, verify_plan


def resume(root):
    record = root/'api_failure_resume.json'
    if record.exists():raise ValueError('recovery already recorded')
    batch = read(root/'batch.json')
    failed = read(root/'SF05/state.json')
    if failed.get('status')!='PLAN_ERROR' or '502' not in failed.get('reason',''):
        raise ValueError('not the reviewed SF05 API failure')
    if time.time() >= batch['deadline']:raise ValueError('original batch deadline exhausted')
    previous = batch['execution_code_hashes']
    current = {p:sha(REPO/p) for p in previous}
    changed = {p for p in previous if previous[p]!=current[p]}
    allowed = {'experiments/planned_checks/run.py','experiments/planned_checks/token_budget.py'}
    if not changed or not changed <= allowed:raise ValueError(f'unreviewed changes: {changed}')
    for path in root.glob('SF*/state.json'):
        state = read(path)
        if 'input_hashes' in state and hashes(path.parent/'original')!=state['input_hashes']:
            raise ValueError(f'input changed: {path.parent.name}')
    for path in root.glob('SF*/tool_plan.resolved.json'):verify_plan(read(path))
    write_json(record, {'at':time.time(), 'reason':'case-local planner request failure recovery',
        'previous_execution_code_hashes':previous,'new_execution_code_hashes':current,
        'deadline_unchanged':batch['deadline'], 'failed_case_preserved':failed,
        'stepcode_ledger_sha256':sha(root/'stepcode_usage.json'),
        'failed_request_retried':False, 'budget_reset':False})
    batch['execution_code_hashes']=current
    write_json(root/'batch.json',batch)


if __name__=='__main__':
    from pathlib import Path
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    resume(parser.parse_args().output.resolve())
