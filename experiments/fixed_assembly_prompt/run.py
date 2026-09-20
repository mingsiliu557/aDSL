"""Frozen original prompts -> native ObjectWorkflow.generate; no asset editing."""
from __future__ import annotations

import argparse
import ast
import asyncio
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from agents.models.interface import Model
from adsl.agents.models import ObjectRequest
from adsl.agents.overhang_edit import assert_version, file_hash
from adsl.agents.service import ObjectWorkflow
from adsl.agents.utils.io import read_json, write_json
from adsl.agents.utils.sessions import SessionManager

IDS = ('SF07', 'SF03', 'SF13')
SIZES = {'SF07':[120.,120.,120.], 'SF03':[90.,80.,180.], 'SF13':[100.,32.,200.]}
PROFILE = REPO/'adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml'
MANIFEST = REPO/'experiments/standing_fea_30/case_manifest.json'
MANUFACTURING = '''Generate a NEW complete aDSL program from the original task below, with static fixed manufacturing assembly, not articulation. Independently plan semantic components, which belong together in a print part, and local interface locations. Do not assume one Asset/class per print part; there must be at least two distinct print parts. Use ONLY existing FixedAssembly / TabSlot and a receiver-first rooted tree. Within a print part retain normal modeling and attach_part. Between print parts use assembly.connect to generate BOTH mating sides and placement; no global union across print parts. The parameters argument of connect must be a TabSlot object (or an entry containing that object), not a dictionary of dimensions. Preserve the requested appearance and function. Frozen scale, overall dimensions and single-sided fit allowance below must not change. These are geometric demonstration dimensions; clearance is not proof of physical fixation. Use one complete initial source.py and repair only within the frozen budget, with existing Image/Code Critic in visual_only mode and export consistency checks. Assembly geometry checks are NOT_EVALUATED; visual approval is not manufacturing approval. No physical checkers, snap_floaters, or extra interface types.'''


def prepare(root, max_rounds=5):
    if not 1 <= max_rounds <= 5:
        raise ValueError('max_rounds must be between 1 and 5')
    root.mkdir(parents=True, exist_ok=True)
    marker = root/'batch.json'
    if marker.exists():
        print('Existing batch: preserve frozen repair limits; --max-rounds only affects new batches.', flush=True)
        return
    cases = {c['case_id']:c for c in read_json(MANIFEST)['cases']}
    for cid in IDS:
        case = cases[cid]
        inputs = {'case_id':cid, 'experiment_type':'fixed_assembly_prompt_to_3d',
            'original_task':{'prompt':case['prompt'], 'image_paths':[]},
            'manufacturing_requirements':MANUFACTURING + (
                f' Budget: at most {max_rounds} evaluation rounds, including the initial review,'
                f' and at most {max_rounds-1} source repairs. Stop early on approval or explicit no change;'
                ' do not force edits to exhaust the budget.'),
            'fixed_assembly':{'mm_per_unit':1., 'fit_offset_mm':.2, 'final_size_mm':SIZES[cid],
                              'validation_mode':'visual_only', 'require_multiple_parts':True},
            'provenance':{'manifest':str(MANIFEST), 'manifest_sha256':file_hash(MANIFEST),
                'field':f'cases[case_id={cid}].prompt', 'dataset':case['dataset'],
                'object_id':case['object_id'], 'caption_source':case['caption_source'],
                'references':'Saved original batch create command had no --image argument; generated renders excluded. Historical serialized requests are unavailable.',
                'reference_evidence':['experiments/standing_fea_30/run_batch.py::generation_command',
                    'adsl-agents/cli.py --image default []'],
                'geometry_dimensions':'New demonstration specification, not inferred from any saved model.'},
            'initial_generation_limit':1, 'source_repair_limit':max_rounds-1}
        write_json(root/cid/'input.json', inputs)
    paths = [Path(__file__), PROFILE, REPO/'adsl-agents/service.py', REPO/'adsl-agents/fixed_assembly.py',
             REPO/'adsl-core/core/export/export_assembly.py']
    write_json(marker, {'experiment_type':'fixed_assembly_prompt_to_3d','case_order':IDS,
        'commit':subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        'file_sha256':{str(p):file_hash(p) for p in paths}, 'created_at':time.time(),
        'input_sha256':{cid:file_hash(root/cid/'input.json') for cid in IDS},
        'physical_checkers':[], 'geometry_timeout_seconds':120, 'render_timeout_seconds':300,
        'render':{'engine':'CYCLES','width':512,'height':512,'samples':32},
        'cross_experiment_token_accounting':False})


class RequestLogModel(Model):
    """Only per-call evidence and actual usage; no cumulative ledger or limits."""
    def __init__(self, model, workspace):
        self.model, self.workspace, self.stage = model, workspace, 'unset'
        self.count = 0

    async def get_response(self, *args, **kwargs):
        self.count += 1
        path = self.workspace/'api_calls'/f'{self.count:03d}.json'
        row = {'stage':self.stage, 'started_at':time.time(), 'status':'RUNNING',
            'system_instructions':kwargs.get('system_instructions'), 'input':kwargs.get('input'),
            'tools':[getattr(t,'name',type(t).__name__) for t in kwargs.get('tools',[])]}
        write_json(path,row)
        try:
            response = await self.model.get_response(*args, **kwargs)
            usage = response.usage
            row.update(status='COMPLETED', input_tokens=usage.input_tokens,
                       output_tokens=usage.output_tokens, total_tokens=usage.input_tokens+usage.output_tokens)
            return response
        except Exception as error:
            row.update(status='ERROR', error_type=type(error).__name__, reason=str(error)[:400], total_tokens=None)
            raise
        finally:
            row.update(finished_at=time.time(), elapsed_seconds=time.time()-row['started_at'])
            write_json(path,row)

    async def stream_response(self, *args, **kwargs):
        async for item in self.model.stream_response(*args, **kwargs):
            yield item


class PromptWorkflow(ObjectWorkflow):
    def _runtime(self, request, workspace, **kwargs):
        runtime = super()._runtime(request, workspace, **kwargs)
        self.runtime = runtime
        runtime.sessions = SessionManager(workspace, request.task_id,
            Path(tempfile.mkdtemp(prefix='adsl-prompt-assembly-sessions-')))
        runtime.model = RequestLogModel(runtime.model, workspace)
        call = runtime.run

        async def recorded_run(**kw):
            stage = kw['stage']
            runtime.model.stage = stage
            path = workspace/'stage_inputs'/(stage.replace(':','_')+'.json')
            row = {'stage':stage, 'role':kw['role'], 'input':kw['input'],
                   'source_empty_before_call':not (workspace/'source.py').read_text().strip(),
                   'started_at':time.time()}
            if stage in {'plan','initial_code'} and (not row['source_empty_before_call'] or request.image_paths):
                raise ValueError('Prompt-only initial input violated: source not empty or unexpected reference images')
            write_json(path,row)
            try:
                return await call(**kw)
            finally:
                row.update(elapsed_seconds=time.time()-row['started_at'],
                           tools=[asdict(e) for e in getattr(kw.get('context'), 'events', [])])
                write_json(path,row)
        runtime.run = recorded_run
        return runtime


def input_audit(work, inputs):
    evidence = {}
    for stage in ('plan','initial_code'):
        path = work/'stage_inputs'/(stage+'.json')
        if not path.exists():
            evidence[stage] = {'verified':False,'reason':'stage not reached'}
            continue
        record = read_json(path)
        payload = json.loads(record['input'])
        expected = {'requirement','fixed_assembly'} if stage=='plan' else {
            'requirement','fixed_assembly','articulation_required','plan','assignment'}
        actual = [read_json(p) for p in sorted((work/'api_calls').glob('*.json'))
                  if read_json(p)['stage']==stage]
        first_input = actual[0]['input'] if actual else None
        expected_text = record['input']
        # SDK may normalize a string into an input_text message. Check actual
        # first model input, not merely that our staging payload was constructed.
        matched = first_input == expected_text
        if isinstance(first_input,list) and len(first_input)==1 and isinstance(first_input[0],dict):
            message = first_input[0]
            content = message.get('content')
            matched = message.get('role')=='user' and (content==expected_text or
                content==[{'type':'input_text','text':expected_text}])
        evidence[stage] = {'verified':bool(record['source_empty_before_call'] and set(payload)==expected and matched),
            'source_empty':record['source_empty_before_call'], 'payload_fields':list(payload),
            'actual_model_input_matches_stage_input':matched, 'api_call_count':len(actual),
            'frozen_config_unchanged':payload['fixed_assembly']==inputs['fixed_assembly'],
            'original_prompt_present':inputs['original_task']['prompt'] in payload['requirement'],
            'reference_images':0, 'tool_events':record.get('tools',[])}
    programs = []
    paths = [work/'original/source.py', *sorted((work/'rounds').rglob('source.py'))]
    for path in paths:
        if not path.is_file():continue
        try:
            calls = [node.func for node in ast.walk(ast.parse(path.read_text())) if isinstance(node,ast.Call)]
            names = [f.id if isinstance(f,ast.Name) else f.attr if isinstance(f,ast.Attribute) else '' for f in calls]
            programs.append({'source':str(path),'sha256':file_hash(path),
                             'assembly_api_present':all(n in names for n in ('FixedAssembly','TabSlot','connect'))})
        except SyntaxError:
            programs.append({'source':str(path),'sha256':file_hash(path),'assembly_api_present':False})
    result = {'experiment_type':'fixed_assembly_prompt_to_3d', 'stages':evidence,'programs':programs,
              'input_verified':all(evidence[s].get('verified') and evidence[s].get('frozen_config_unchanged') and evidence[s].get('original_prompt_present') for s in ('plan','initial_code')),
              'assembly_api_verified':any(p['assembly_api_present'] for p in programs)}
    write_json(work.parent/'input_audit.json', result)
    return result


def shared_export_failures(failures):
    """Bad candidate geometry is not proof of a shared export implementation bug.

    EXPORTED_FILE_INVALID also wraps file/ID/read errors. Only the existing
    mesh_solid validity error is known to be a case-level geometry rejection;
    unknown reasons and actual geometry mismatches still pause the batch.
    """
    invalid_mesh = 'mesh is not a finite closed oriented volume, or has zero-area faces'
    return [failure for failure in failures if failure['code'].startswith('EXPORTED_')
            and not (failure['code'] == 'EXPORTED_FILE_INVALID'
                     and failure.get('reason') == invalid_mesh)]


def reclassify_saved_geometry_pause(folder):
    """Re-evaluate only the old export-prefix pause, without replaying a case."""
    result_path, audit_path = folder/'result.json', folder/'input_audit.json'
    result, audit = read_json(result_path), read_json(audit_path)
    work = folder/'generate'
    paths = sorted((work/'rounds').rglob('assembly_manifest.json'))
    failures = [failure for path in paths for failure in read_json(path).get('failures', [])]
    calls = sorted((work/'api_calls').glob('*.json'))
    # Do not turn an old/partial summary with missing evidence into permission.
    evidence_complete = bool(paths and calls and failures == result.get('geometry_failures'))
    old_export_pause = any(failure['code'].startswith('EXPORTED_') for failure in failures)
    blocked = bool(
        not evidence_complete or not old_export_pause or shared_export_failures(failures)
        or result.get('exception', True) is not None
        or result.get('tool_errors', True) != [] or result.get('flow_errors', True) != []
        or list(work.rglob('flow_error.json'))
        or any(read_json(path).get('status') != 'COMPLETED' for path in calls)
        or not audit.get('input_verified') or not audit.get('assembly_api_verified'))
    gate = {'case': folder.name, 'original_pause_batch': result.get('pause_batch'),
        'pause_batch': blocked, 'case_approved': result.get('approved', False),
        'reason': ('saved evidence confirms only case-level exported mesh invalidity'
                   if not blocked else 'shared error or incomplete evidence; keep batch paused'),
        'evidence_sha256': {str(path): file_hash(path)
                           for path in [result_path, audit_path, *paths, *calls]},
        'shared_export_failures': shared_export_failures(failures),
        'case_replayed': False, 'checked_at': time.time()}
    write_json(folder/'continuation_gate.json', gate)
    return not blocked


async def run_case(root, cid):
    folder = root/cid
    if (folder/'result.json').exists():return read_json(folder/'result.json')
    if (folder/'started.json').exists():raise RuntimeError('No automatic replay of a started case')
    inputs = read_json(folder/'input.json')
    if file_hash(folder/'input.json') != read_json(root/'batch.json')['input_sha256'][cid]:
        raise ValueError('Frozen task input changed')
    # Old saved inputs used one repair; never expand an existing case's budget.
    max_rounds = inputs.get('source_repair_limit', 1) + 1
    requirement = '[ORIGINAL TASK]\n'+inputs['original_task']['prompt']+'\n\n[UNIFORM MANUFACTURING REQUIREMENTS]\n'+inputs['manufacturing_requirements']
    work = folder/'generate'
    workflow = PromptWorkflow(PROFILE)
    request = ObjectRequest(requirement,work,f'prompt_assembly_{cid}',image_paths=(),
        articulation=False,max_rounds=max_rounds,checker_specs=(),fixed_assembly=inputs['fixed_assembly'])
    start = time.time()
    write_json(folder/'started.json',{'time':start,'route':'ObjectWorkflow.generate','source_preloaded':False})
    print(f'{cid}: native generate, fresh source, no reference images; max_rounds={max_rounds}, max_repairs={max_rounds-1}',flush=True)
    result, error = None, None
    try:
        result = await workflow.generate(request)
    except Exception as exc:
        error = {'type':type(exc).__name__,'reason':str(exc)[:600]}
        (folder/'error.log').write_text(traceback.format_exc())
    audit = input_audit(work,inputs)
    book_path = work/'assembly_versions.json'
    book = read_json(book_path) if book_path.exists() else {}
    if book:
        selected = book['versions'][book['retained']]
        assert_version(selected)
        if file_hash(work/'source.py') != file_hash(Path(selected['source'])):
            raise ValueError('Retained/source mismatch')
    reports = [read_json(p) for p in sorted((work/'rounds').rglob('assembly_manifest.json'))]
    failures = [f for report in reports for f in report.get('failures',[])]
    shared = shared_export_failures(failures)
    tool_errors = [read_json(p) for p in work.rglob('*edit_outcome.json') if read_json(p).get('status')=='TOOL_ERROR']
    missing_reports = [e for e in tool_errors if 'No such file' in e.get('reason','') and 'assembly_manifest.json' in e['reason']]
    flow_errors = [str(p) for p in work.rglob('flow_error.json')]
    calls = [read_json(p) for p in sorted((work/'api_calls').glob('*.json'))]
    repair_log = work/'repair_history.jsonl'
    repairs = len(repair_log.read_text().splitlines()) if repair_log.exists() else 0
    pause = bool(error or shared or missing_reports or flow_errors or not audit['input_verified'])
    if cid==IDS[0] and not audit['assembly_api_verified']:pause=True
    summary = {'case':cid,'experiment_type':'fixed_assembly_prompt_to_3d',
        'approved':bool(result and result.approved),'pause_batch':pause,
        'initial_generations':int((work/'stage_inputs/initial_code.json').exists()),'repair_attempts':repairs,
        'max_rounds':max_rounds,'source_repair_limit':max_rounds-1,
        'source':str(work/'source.py'),'retained_version':book.get('retained'),
        'stop_reason':book.get('stop_reason'), 'elapsed_seconds':time.time()-start,
        'geometry_failures':failures,'tool_errors':tool_errors,'flow_errors':flow_errors,'exception':error,
        'actual_api_requests':len(calls),'known_actual_tokens':sum(c.get('total_tokens') or 0 for c in calls),
        'unknown_usage_requests':sum(c.get('total_tokens') is None for c in calls),
        'physical_checkers':'NOT_EXECUTED'}
    runtime = getattr(workflow,'runtime',None)
    if runtime and runtime.sessions.database_path.exists():
        with sqlite3.connect(runtime.sessions.database_path) as db, sqlite3.connect(work/'sessions_snapshot.sqlite3') as out:
            db.backup(out)
    write_json(folder/'result.json',summary)
    print(f'{cid}: approved={summary["approved"]} pause={pause} repairs={repairs}/{max_rounds-1} tokens={summary["known_actual_tokens"]}',flush=True)
    return summary


async def main(root, cases, max_rounds=5):
    prepare(root, max_rounds=max_rounds)
    if any(c!=IDS[0] for c in cases):
        first = root/IDS[0]
        if not (first/'result.json').exists():
            raise ValueError('SF07 must finish without a shared error before later cases')
        if read_json(first/'result.json')['pause_batch'] and not reclassify_saved_geometry_pause(first):
            raise ValueError('SF07 saved evidence still requires a batch pause')
        audit = read_json(first/'input_audit.json')
        if not (audit['input_verified'] and audit['assembly_api_verified']):
            raise ValueError('SF07 actual input/API audit must pass first')
    for cid in cases:
        result = await run_case(root,cid)
        if result['pause_batch']:
            write_json(root/'paused.json',{'case':cid,'reason':'shared flow/export/input error; inspect evidence before continuing'})
            return 2
    return 0


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--cases',nargs='+',choices=IDS,default=[IDS[0]])
    parser.add_argument('--max-rounds',type=int,choices=range(1,6),default=5,
                        help='New batches only: total review rounds including initial evaluation (default 5, at most 4 repairs); existing frozen limits remain unchanged')
    args=parser.parse_args()
    raise SystemExit(asyncio.run(main(args.root.resolve(),args.cases,args.max_rounds)))
