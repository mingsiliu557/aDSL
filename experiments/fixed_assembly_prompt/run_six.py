"""Frozen 3 saved assets + 3 fresh prompts; existing loops, five rounds each.

This is a small serial experiment launcher, not an agent/tool scheduler.
Each job has a separate process, workspace and ledger. Never replay a started job.
"""
from __future__ import annotations
import argparse
import asyncio
import ast
from collections import Counter
import csv
import difflib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import time
import traceback

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from experiments.fixed_assembly_prompt import run as prompt, verify_topology as saved
from adsl.agents.overhang_edit import file_hash,assert_version
from adsl.agents.utils.io import read_json,write_json

EXISTING=(
    ('existing_SF07_open','SF07','assembly_topology_prompt_SF07_20260921_v1/SF07/generate','known open base mesh'),
    ('existing_SF13_open','SF13','fixed_assembly_prompt_regroup_SF13_20260920T160523Z/SF13/generate','known open shelf meshes'),
    ('existing_SF13_pass','SF13','assembly_topology_SF13_feedback_recovery_20260921T051900Z','previously passed normal control; paired with SF13_open'),
)
MAX_ROUNDS=5
IMPLEMENTATION=('adsl-agents/assembly_topology.py','adsl-agents/fixed_assembly.py',
    'adsl-agents/service.py','adsl-agents/tools/files.py','adsl-core/core/assembly_topology.py',
    'adsl-core/core/assembly.py','adsl-core/core/export/export_assembly.py',
    'experiments/fixed_assembly_prompt/run.py','experiments/fixed_assembly_prompt/verify_topology.py',
    'experiments/fixed_assembly_prompt/run_six.py')


def prepare(root):
    root.mkdir(parents=True,exist_ok=False)
    jobs=[]
    for name,cid,origin,reason in EXISTING:
        asset=(REPO/'local_experiment'/origin).resolve()
        manifest=read_json(asset/'assembly/assembly_manifest.json')
        assert manifest['source_sha256']==file_hash(asset/'source.py')
        assert list((asset/'render').glob('*.png')) and (asset/'plan.json').is_file()
        for filename,digest in manifest['files_sha256'].items():
            assert file_hash(asset/'assembly'/filename)==digest
        jobs.append(dict(name=name,group='existing',case_id=cid,asset_root=str(asset),reason=reason,
            source_sha256=file_hash(asset/'source.py'),manifest_sha256=file_hash(asset/'assembly/assembly_manifest.json'),
            fixed_assembly=read_json(asset/'runtime_config.json')['request']['fixed_assembly']))
    prompt.prepare(root/'new',max_rounds=MAX_ROUNDS,cases=prompt.IDS,assembly_topology=True,profile=prompt.PROFILE)
    jobs += [dict(name='new_'+cid,group='new',case_id=cid,reason='fresh original prompt; no old source/images') for cid in prompt.IDS]
    record=dict(commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        created_at=time.time(),jobs=jobs,max_source_repairs=MAX_ROUNDS-1,max_evaluation_rounds=MAX_ROUNDS,
        profile=str(prompt.PROFILE),profile_sha256=file_hash(prompt.PROFILE),
        implementation_sha256={p:file_hash(REPO/p) for p in IMPLEMENTATION},
        checker_spec=saved.checker_spec().model_dump(),render=dict(engine='CYCLES',width=512,height=512,samples=32),
        geometry_timeout_seconds=120,render_timeout_seconds=300,
        note='Existing SF13 open/pass are paired versions, not independent objects. No claim of physical fixation.')
    write_json(root/'six_cases.json',record)
    subprocess.run(['git','diff','--output='+str(root/'implementation.patch'),'--',*IMPLEMENTATION],cwd=REPO,check=True)
    for name in IMPLEMENTATION:
        target=root/'implementation_snapshot'/name;target.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(REPO/name,target)
    summarize(root)


async def job(root,name):
    batch=read_json(root/'six_cases.json')
    assert file_hash(Path(batch['profile']))==batch['profile_sha256'],'Frozen API profile changed'
    assert all(file_hash(REPO/p)==sha for p,sha in batch['implementation_sha256'].items()),'Frozen implementation changed'
    item=next(j for j in batch['jobs'] if j['name']==name)
    if item['group']=='new':
        # Reuse single-case native generation; its old batch pause does not
        # prevent later independent jobs in this explicitly authorized batch.
        await prompt.run_case(root/'new',item['case_id'])
    else:
        asset=Path(item['asset_root']);work=root/'existing'/name
        assert file_hash(asset/'source.py')==item['source_sha256']
        assert file_hash(asset/'assembly/assembly_manifest.json')==item['manifest_sha256']
        saved.measure(asset.parent,work,Path(batch['profile']),asset_root=asset,case_id=item['case_id'],
                      max_rounds=batch['max_evaluation_rounds'])
        # Normal controls still receive ordinary Image/Code review. No hard
        # failure is fabricated, and the loop may approve without a patch.
        await saved.repair(work,review_all=True)


def read_if(path,default=None):
    if not path.is_file(): return {} if default is None else default
    try: return read_json(path)
    except (OSError,ValueError): return {'read_error':str(path)}


def counts(result,kind):
    rows=[r for r in result.get('metrics',{}).get('items',[]) if r.get('kind')==kind]
    if rows: return dict(Counter(r.get('status','INDETERMINATE') for r in rows))
    if kind=='interface' and result.get('status')=='PASS': return {'NOT_APPLICABLE':True}
    return {'NOT_EXECUTED':True}


def summarize(root):
    batch=read_json(root/'six_cases.json');rows=[]
    for item in batch['jobs']:
        work=(root/'existing'/item['name'] if item['group']=='existing' else root/'new'/item['case_id']/'generate')
        state=read_if(root/'jobs'/item['name']/'state.json')
        generation=read_if(work.parent/'result.json') if item['group']=='new' else {}
        book=read_if(work/'assembly_versions.json');versions=book.get('versions',{})
        original=versions.get('original',{});retained=versions.get(book.get('retained'),{})
        working=versions.get(book.get('working'),{})
        initial=original.get('reviews',{}).get('assembly_topology') or read_if(work/'baseline_result.json')
        final=retained.get('reviews',{}).get('assembly_topology') or {}
        calls=[read_if(p) for p in sorted((work/'api_calls').glob('*.json'))]
        histories=work/'repair_history.jsonl'
        repairs=len(histories.read_text().splitlines()) if histories.is_file() else 0
        categories=[]
        if state.get('exit_code',0): categories.append('tool_or_process_error')
        if generation.get('exception'): categories.append('generation_error')
        if any(c.get('status')=='ERROR' for c in calls): categories.append('API_error')
        if list(work.rglob('flow_error.json')): categories.append('flow_error')
        if any(read_if(p).get('status')=='TOOL_ERROR' for p in work.rglob('edit_outcome.json')): categories.append('tool_error')
        if final.get('status')=='FAIL': categories.append('geometry_failure')
        if final.get('status') in ('INDETERMINATE','ERROR') or not final: categories.append('unverified')
        if book.get('stop_reason')=='NO_CHANGE': categories.append('no_reasonable_change')
        if retained.get('reviews',{}).get('appearance_approved') is False: categories.append('appearance_rejected')
        publication_error=None
        if retained:
            try:
                assert_version(retained)
                assert file_hash(work/'source.py')==file_hash(Path(retained['source']))
            except (AssertionError,ValueError,OSError) as error:
                publication_error=f'{type(error).__name__}: {error}'
                categories.append('publication_mismatch')
        changed=[]
        if original and working and original['source']!=working['source']:
            before=Path(original['source']).read_text();after=Path(working['source']).read_text()
            (root/'jobs'/item['name']/'source.diff').write_text(''.join(difflib.unified_diff(before.splitlines(True),after.splitlines(True),fromfile='initial',tofile='working')))
            try:
                def symbols(text):
                    return {getattr(n,'name',f'{type(n).__name__}:{i}'):ast.dump(n) for i,n in enumerate(ast.parse(text).body)}
                a,z=symbols(before),symbols(after);changed=[s for s in a.keys()|z.keys() if a.get(s)!=z.get(s)]
            except SyntaxError: changed=['candidate_syntax_error']
        row=dict(case=item['name'],group=item['group'],reason=item['reason'],status=state.get('status','PENDING'),
            initial_parts=counts(initial,'part'),final_parts=counts(final,'part'),
            initial_interfaces=counts(initial,'interface'),final_interfaces=counts(final,'interface'),
            initial_topology=initial.get('status','NOT_EXECUTED'),final_topology=final.get('status','NOT_EXECUTED'),
            working_topology=working.get('reviews',{}).get('assembly_topology',{}).get('status','NOT_EXECUTED'),
            initial_appearance=original.get('reviews',{}).get('appearance_approved'),
            final_appearance=retained.get('reviews',{}).get('appearance_approved'),
            approved=bool(retained.get('reviews',{}).get('accepted',False) and not publication_error),
            retained=book.get('retained'),publication_error=publication_error,
            generation_error=generation.get('exception'),
            changed_symbols=changed,repair_location='manual review pending' if changed else 'none',
            visible_regression='manual review pending',failure_categories=categories,stop_reason=book.get('stop_reason'),
            repair_attempts=repairs,api_calls=len(calls),tokens=sum(c.get('total_tokens') or 0 for c in calls),
            unknown_usage_calls=sum(c.get('total_tokens') is None for c in calls),
            input_tokens=sum(c.get('input_tokens') or 0 for c in calls),
            output_tokens=sum(c.get('output_tokens') or 0 for c in calls),
            api_seconds=sum(c.get('elapsed_seconds') or 0 for c in calls),
            seconds=state.get('elapsed_seconds'),workspace=str(work))
        rows.append(row)
    write_json(root/'case_results.json',rows)
    with (root/'case_results.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(rows[0]));writer.writeheader()
        writer.writerows({k:json.dumps(v,ensure_ascii=False) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows)
    lines=['# 固定装配六例小批量验证','', '## Material Passport','',
        '- Origin Skill: academic-research-suite / experiment-agent','- Origin Mode: run',
        '- Verification Status: UNVERIFIED（待全部完成及人工外观核对）','',
        f"每例最多{batch['max_evaluation_rounds']}轮审核（含初稿），最多{batch['max_source_repairs']}次源码修补；只启用assembly_topology，Image/Code顺序不变。",
        '已有SF13的open/pass是配对版本，正常对照通过不计为修复收益。未验证不计PASS。','']
    for group in ('existing','new'):
        subset=[r for r in rows if r['group']==group]
        lines += [f'## {group}（单独统计）','',
            '| 案例 | 状态 | Topology 初始→保留 | 外观 | 修补次数 | API/tokens |',
            '| --- | --- | --- | --- | --- | --- |']
        lines += [f"| {r['case']} | {r['status']} | {r['initial_topology']} → {r['final_topology']} | {r['final_appearance']} | {r['repair_attempts']} | {r['api_calls']}/{r['tokens']} |" for r in subset]
        lines += ['',f"完成 {sum(r['status']=='COMPLETED' for r in subset)}/3；保留版本合格 {sum(r['approved'] for r in subset)}/3。",'']
    lines += ['详见 case_results.csv/json，各例日志在 jobs/，修补前后完整资产和审核在 workspace。',
              '个例失败继续，不自动追加预算；不据此宣称实物固定、承载或制造成功。']
    (root/'REPORT.md').write_text('\n'.join(lines)+'\n')
    return rows


def run(root):
    prepare(root)
    for item in read_json(root/'six_cases.json')['jobs']:
        folder=root/'jobs'/item['name'];folder.mkdir(parents=True,exist_ok=False)
        start=time.time();state={'status':'RUNNING','started_at':start}
        write_json(folder/'state.json',state)
        print('START',item['name'],flush=True)
        with (folder/'run.log').open('w') as log:
            try:
                result=subprocess.run([sys.executable,'-u',str(Path(__file__).resolve()),'--root',str(root),'--job',item['name']],
                                      cwd=REPO,stdout=log,stderr=subprocess.STDOUT,start_new_session=True)
                exit_code=result.returncode
            except OSError:
                log.write(traceback.format_exc());exit_code=1
        state.update(status='COMPLETED' if exit_code==0 else 'ERROR',exit_code=exit_code,
                     elapsed_seconds=time.time()-start)
        write_json(folder/'state.json',state)
        try: summarize(root)
        except Exception:
            (folder/'summary_error.log').write_text(traceback.format_exc())
        print('END',item['name'],state,flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser(__doc__);parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--job');parser.add_argument('--summarize',action='store_true')
    args=parser.parse_args();root=args.root.resolve()
    if args.job: asyncio.run(job(root,args.job))
    elif args.summarize: summarize(root)
    else: run(root)
