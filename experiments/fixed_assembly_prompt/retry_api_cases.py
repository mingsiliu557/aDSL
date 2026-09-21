"""One explicitly authorized continuation of the four API-interrupted cases.

Old assets, ledgers and costs are immutable. Resume from saved working geometry;
only a case still lacking generated source starts from its prompt.
No production workflow changes and no automatic retry of this continuation.
"""
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
import yaml
import openai

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from experiments.fixed_assembly_prompt import run as prompt, verify_topology as saved
from adsl.agents.overhang_edit import assert_version,file_hash
from adsl.agents.utils.io import read_json,write_json

NAMES=('existing_SF13_open','new_SF07','new_SF03','new_SF13')


def api_interrupted_repairs(work):
    """API-aborted Coder attempts are uncharged, including saved partial patches."""
    history=work/'repair_history.jsonl';ledger=work/'assembly_versions.json'
    if not history.is_file() or not ledger.is_file():return []
    records=[json.loads(line) for line in history.read_text().splitlines()]
    versions=read_json(ledger)['versions'];credits=[]
    calls=[(p,read_json(p)) for p in (work/'api_calls').glob('*.json')]
    for i,record in enumerate(records):
        version=versions.get(record['attempt_id'],{})
        outcome=version.get('reviews',{}).get('edit_outcome',{})
        if outcome.get('status')!='TOOL_ERROR':continue
        end=records[i+1]['recorded_at'] if i+1<len(records) else float('inf')
        for path,call in calls:
            error_type=getattr(openai,str(call.get('error_type','')),None)
            if (call.get('status')=='ERROR' and str(call.get('stage','')).startswith('assembly_repair:')
                    and isinstance(error_type,type) and issubclass(error_type,openai.APIError)
                    and call.get('reason') and str(outcome.get('reason','')).startswith(call['reason'][:120])
                    and record['recorded_at']<=call.get('started_at',-1)<end):
                credits.append(dict(workspace=str(work.resolve()),attempt_id=record['attempt_id'],api_call=str(path.resolve())))
                break
    return credits


def prepare(previous,root,*,cases=None,previous_profile=None,budget_amendment=None):
    continuation=(previous/'retry_plan.json').is_file()
    if continuation:
        prior=read_json(previous/'retry_plan.json')
        old=dict(profile_sha256=prior['profile_sha256'],
                 max_source_repairs=prior['max_total_repairs_per_case'],
                 max_evaluation_rounds=prior['max_total_repairs_per_case']+1)
        prior_jobs={item['name']:item for item in prior['jobs']}
        rows={}
        for result in read_json(previous/'RESULTS.json'):
            rows[result['case']]=dict(result,group=prior_jobs[result['case']]['original_group'],
                repair_attempts=result['cumulative_repairs'])
        previous_tokens=prior['previous_batch_tokens']+sum(r['known_tokens'] for r in rows.values())
        previous_unknown=prior['previous_batch_unknown_usage']+sum(r['unknown_usage_calls'] for r in rows.values())
    else:
        old=read_json(previous/'six_cases.json')
        rows={r['case']:r for r in read_json(previous/'case_results.json')}
        previous_tokens=sum(r['tokens'] for r in rows.values())
        previous_unknown=sum(r['unknown_usage_calls'] for r in rows.values())
    current_hash=file_hash(prompt.PROFILE)
    amendment=None
    if current_hash!=old['profile_sha256']:
        assert previous_profile is not None, 'model profile changed; explicit previous profile required'
        assert file_hash(previous_profile)==old['profile_sha256'], 'previous profile hash mismatch'
        before=yaml.safe_load(previous_profile.read_text());after=yaml.safe_load(prompt.PROFILE.read_text())
        before_retry=before['params'].pop('max_retries');after_retry=after['params'].pop('max_retries')
        assert before==after and before_retry==0 and after_retry==3, 'only max_retries 0 -> 3 is authorized'
        amendment=dict(field='params.max_retries',before=0,after=3,
            previous_profile_sha256=old['profile_sha256'],current_profile_sha256=current_hash)
    selected=tuple(cases if cases is not None else NAMES)
    assert selected and len(selected)==len(set(selected)) and set(selected)<=set(NAMES)
    budget_change=read_json(budget_amendment) if budget_amendment else None
    if budget_change:
        assert Path(budget_change['previous_batch']).resolve()==previous.resolve()
        name=budget_change['case'];assert name in selected
        row=rows[name];assert row['repair_attempts']==budget_change['used_before']
        credits=budget_change['excluded_attempts'];seen=set()
        assert credits, 'no API interruption evidence'
        for credit in credits:
            identity=(credit['workspace'],credit['attempt_id'])
            assert identity not in seen, 'duplicate interruption credit'
            seen.add(identity)
            assert credit in api_interrupted_repairs(Path(credit['workspace'])), 'not an API-interrupted repair'
        row['repair_attempts']-=len(credits)
        assert row['repair_attempts']==budget_change['used_after']
    for name in selected:
        row=rows[name]
        assert not row.get('approved'), 'do not replay approved cases'
        assert 0 <= row['repair_attempts'] < old['max_source_repairs'], 'no remaining repair budget'
        if continuation: assert row['stop_reason'] in ('FLOW_ERROR','TOOL_ERROR'), 'only interrupted cases may continue'
    root.mkdir(parents=True,exist_ok=False)
    shutil.copy2(prompt.PROFILE,root/'model_profile.yaml')
    if amendment: shutil.copy2(previous_profile,root/'previous_model_profile.yaml')
    jobs=[]
    for name in selected:
        row=rows[name]
        if not continuation: assert 'API_error' in row['failure_categories']
        work=Path(row['workspace']);used=row['repair_attempts']
        assert 0 <= used <= old['max_source_repairs'], 'invalid cumulative repair budget'
        item=dict(name=name,case_id=name.split('_')[1],previous_workspace=str(work),
            original_group=row['group'],previous_result=row,used_repairs=used,
            remaining_repairs=old['max_source_repairs']-used,previous_version=None)
        if (work/'assembly_versions.json').is_file():
            book=read_json(work/'assembly_versions.json');version=book['versions'][book['working']]
            assert_version(version);assert version['execution']
            stage=root/'inputs'/name;stage.mkdir(parents=True)
            for target,origin in [('source.py',Path(version['source'])),
                                  ('plan.json',work/'plan.json'),('runtime_config.json',work/'runtime_config.json')]:
                shutil.copy2(origin,stage/target)
            asset=Path(version['execution']['output_root'])
            for folder in ('assembly','render'):shutil.copytree(asset/folder,stage/folder)
            if (asset/'source_index.json').is_file():shutil.copy2(asset/'source_index.json',stage/'source_index.json')
            item.update(previous_version=book['working'],source_sha256=file_hash(stage/'source.py'),
                        max_rounds=item['remaining_repairs']+1)
        else:
            assert name=='new_SF13' and used==0
            assert not (work/'source.py').is_file() or not (work/'source.py').read_text().strip()
            # Same frozen original input, not a regenerated prompt/configuration.
            folder=root/'fresh'/'SF13';folder.mkdir(parents=True)
            shutil.copy2(work.parent/'input.json',folder/'input.json')
            write_json(root/'fresh/batch.json',dict(model_profile=str(prompt.PROFILE),
                input_sha256={'SF13':file_hash(folder/'input.json')}))
            item['max_rounds']=old['max_evaluation_rounds']
        jobs.append(item)
    write_json(root/'retry_plan.json',dict(previous_batch=str(previous),jobs=jobs,
        max_total_repairs_per_case=old['max_source_repairs'],profile_sha256=current_hash,
        profile_amendment=amendment,
        budget_amendment=budget_change,
        omitted_cases={name:('already_approved' if row.get('approved') else
            'repair_budget_exhausted' if row['repair_attempts']>=old['max_source_repairs'] else 'not_selected')
            for name,row in rows.items() if name not in selected},
        previous_batch_tokens=previous_tokens,
        previous_batch_unknown_usage=previous_unknown,
        note='Supplemental continuation, not new independent samples. Old results never overwritten.'))


async def run_job(root,name):
    plan=read_json(root/'retry_plan.json');item=next(i for i in plan['jobs'] if i['name']==name)
    assert file_hash(prompt.PROFILE)==plan['profile_sha256']
    if item['previous_version'] is None:
        await prompt.run_case(root/'fresh',item['case_id'])
        return
    stage=root/'inputs'/name;work=root/'continued'/name
    assert file_hash(stage/'source.py')==item['source_sha256']
    saved.measure(stage.parent,work,prompt.PROFILE,asset_root=stage,
                  case_id=item['case_id'],max_rounds=item['max_rounds'])
    record=read_json(work/'input.json')
    record['requirement']+=(f"\nAPI CONTINUATION: {item['used_repairs']} of the original four source "
        f"repair attempts already consumed. At most {item['remaining_repairs']} additional edits remain. "
        'Review this saved working candidate first; do not regenerate the object or change frozen conditions.')
    write_json(work/'input.json',record)
    await saved.repair(work,review_all=True)


def summarize(root):
    rows=[]
    plan=read_json(root/'retry_plan.json')
    for item in plan['jobs']:
        work=root/'continued'/item['name'] if item['previous_version'] else root/'fresh'/item['case_id']/'generate'
        book=read_json(work/'assembly_versions.json') if (work/'assembly_versions.json').is_file() else {}
        retained=book.get('versions',{}).get(book.get('retained'),{})
        history=work/'repair_history.jsonl'
        edits=len(history.read_text().splitlines()) if history.is_file() else 0
        credits=api_interrupted_repairs(work)
        calls=[read_json(p) for p in sorted((work/'api_calls').glob('*.json'))]
        status=root/'jobs'/item['name']/'status.json'
        state=read_json(status) if status.is_file() else {'status':'PENDING'}
        rows.append(dict(case=item['name'],state=state,workspace=str(work),
            previous_version=item['previous_version'],approved=retained.get('reviews',{}).get('accepted',False),
            topology=retained.get('reviews',{}).get('assembly_topology',{}).get('status','NOT_EXECUTED'),
            stop_reason=book.get('stop_reason'),new_reserved_attempts=edits,api_interrupted_attempts=credits,
            new_repairs=edits-len(credits),cumulative_repairs=item['used_repairs']+edits-len(credits),
            api_calls=len(calls),known_tokens=sum(c.get('total_tokens') or 0 for c in calls),
            unknown_usage_calls=sum(c.get('total_tokens') is None for c in calls)))
    write_json(root/'RESULTS.json',rows)


def run(previous,root,*,cases=None,previous_profile=None,budget_amendment=None):
    prepare(previous,root,cases=cases,previous_profile=previous_profile,budget_amendment=budget_amendment)
    for item in read_json(root/'retry_plan.json')['jobs']:
        folder=root/'jobs'/item['name'];folder.mkdir(parents=True)
        start=time.time();state=dict(status='RUNNING',started_at=start)
        write_json(folder/'status.json',state)
        print('START',item['name'],'remaining edits',item['remaining_repairs'],flush=True)
        with (folder/'run.log').open('w') as log:
            result=subprocess.run([sys.executable,'-u',str(Path(__file__).resolve()),'--root',str(root),
                '--job',item['name']],cwd=REPO,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
        state.update(status='FINISHED' if result.returncode==0 else 'ERROR',exit_code=result.returncode,
                     elapsed_seconds=time.time()-start)
        write_json(folder/'status.json',state)
        try:summarize(root)
        except Exception:(folder/'summary_error.log').write_text(traceback.format_exc())
        print('END',item['name'],state,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__)
    parser.add_argument('--previous',type=Path)
    parser.add_argument('--root',type=Path,default=REPO/'temp'/('assembly_topology_api_retry_'+time.strftime('%Y%m%dT%H%M%SZ',time.gmtime())),
        help='New continuation defaults to the project code disk; archive to the data disk only after completion')
    parser.add_argument('--job')
    parser.add_argument('--cases',nargs='+',choices=NAMES,help='Only selected interrupted cases with remaining budget')
    parser.add_argument('--previous-profile',type=Path,help='Old frozen profile; permits only the authorized max_retries 0 -> 3 change')
    parser.add_argument('--budget-amendment',type=Path,help='Explicit user-authorized credits for evidenced API interruptions; old ledgers remain unchanged')
    args=parser.parse_args()
    if args.job:asyncio.run(run_job(args.root.resolve(),args.job))
    else:
        if not args.previous:parser.error('--previous required for a new continuation')
        run(args.previous.resolve(),args.root.resolve(),cases=args.cases,
            previous_profile=args.previous_profile.resolve() if args.previous_profile else None,
            budget_amendment=args.budget_amendment.resolve() if args.budget_amendment else None)
