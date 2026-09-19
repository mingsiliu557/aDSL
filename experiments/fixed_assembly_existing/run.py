"""Three archived control assets; experiment adapter, not production edit support."""
from __future__ import annotations

import argparse
import asyncio
from dataclasses import asdict
import json
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile
import time
import traceback

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from adsl.agents.fixed_assembly import iterate_fixed_assembly
from adsl.agents.models import FixedAssemblyPlan, ObjectRequest
from adsl.agents.overhang_edit import assert_version, file_hash, version_assets, version_record
from adsl.agents.prompts import object_prompt
from adsl.agents.service import ObjectWorkflow
from adsl.agents.tools import PATCH_TOOLS
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.io import read_json, write_json
from adsl.agents.utils.sessions import SessionManager

IDS = ('SF07', 'SF03', 'SF13')
PROFILE = REPO/'adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml'
PRESERVE = {
    'SF07': 'Circular white tabletop, slender tapered central column, broad smoothly flared round base; retain all three silhouettes and table function.',
    'SF03': 'Wooden seat, four legs, tall slatted/panelled back and visible woodgrain; retain seating surface, back support, leg locations and finishes.',
    'SF13': 'Tall rectangular five-level wooden bookshelf, both sides, five shelves and visible woodgrain; retain shelf count, usable shelf space and finishes.',
}
SHARED_EXPORT_CODES = {'EXPORTED_FILE_GEOMETRY_MISMATCH', 'EXPORTED_FILE_INVALID',
                       'EXPORTED_INTERFACE_GEOMETRY_MISMATCH'}


def requirement(cid):
    return (f'Convert the provided original aDSL {cid}/adsl SOURCE, not a new object, into a static '
            'multi-print-part FixedAssembly using existing TabSlot. Read its existing hierarchy and '
            'choose print-part groupings and interface locations yourself. Several semantic Assets '
            'can belong to one print part. Preserve the original overall appearance, function and '
            'major dimensions. ' + PRESERVE[cid] + '\n'
            'Only make necessary local mount/interface changes, including BOTH mating bodies, '
            'related helpers and upper assembly code. Do not replace the object with a simplified '
            'different shape or delete required features. Preserve within-part attach_part/CSG. '
            'Use connect to generate both sides of every interface, ordered receiver-first tree; '
            'do not globally union distinct print parts. Frozen mm scale, size and fit must not '
            'change during repair. These are demonstration dimensions, not real furniture scale; '
            '+0.2 mm single-sided clearance is NOT proof of physical fixation. No articulation, '
            'four physical checkers, snap_floaters or extra interface types. One initial conversion '
            'and at most one repair. If impossible under these constraints, explicitly stop.')


class ExistingAssetWorkflow(ObjectWorkflow):
    """Keep the original reference across BOTH evaluations; reuse existing reviews."""
    def __init__(self, original):
        super().__init__(PROFILE)
        self.original = original

    def _runtime(self, request, workspace, **kwargs):
        runtime = super()._runtime(request, workspace, **kwargs)
        # Existing local-SQLite facility, not a different session implementation.
        runtime.sessions = SessionManager(workspace, request.task_id,
            Path(tempfile.mkdtemp(prefix='adsl-assembly-sessions-')))
        # No cross-experiment ledger/cap. Keep the normal per-run UsageRecorder
        # and the existing model and edit-attempt limits unchanged.
        return runtime

    async def _review_candidate_appearance(self, **kwargs):
        # A candidate must never serve as its own "original" reference.
        kwargs['baseline_execution'] = version_assets(self.original)[1]
        return await super()._review_candidate_appearance(**kwargs)


async def plan_conversion(workflow, runtime, request, original_source, original_renders):
    payload = {'requirement':request.requirement, 'fixed_assembly':request.fixed_assembly,
               'original_source':original_source.read_text(),
               'instruction':'Plan conversion of THIS existing program. Choose print groups and local interfaces; preserve original geometry outside the mounts.'}
    write_json(request.workspace/'planner_input.json', payload)
    planner = runtime.agent(name='object-planner', output_type=FixedAssemblyPlan,
        instructions=object_prompt('planner', articulation=False, fixed_assembly=True))
    result = await runtime.run(agent=planner, input=user_input(json.dumps(payload), original_renders),
                               role='planner', stage='conversion_plan')
    plan = workflow._typed_output(result.final_output, FixedAssemblyPlan)
    if (plan.mm_per_unit != request.fixed_assembly['mm_per_unit'] or
            plan.final_size_mm != request.fixed_assembly['final_size_mm']):
        raise ValueError('Planner changed frozen scale or target size')
    if len(plan.print_parts) < 2:
        raise ValueError('Conversion requires distinct print parts, not a globally fused object')
    write_json(request.workspace/'plan.json', plan.model_dump())
    return plan


def select_and_publish(folder, original, inner_result):
    """Outer original = archived asset; inner original = first conversion attempt."""
    assert_version(original)
    accepted = bool(inner_result and inner_result.approved)
    selected = original
    if accepted:
        book = read_json(folder/'assembly_run/assembly_versions.json')
        selected = book['versions'][book['retained']]
        if not selected['reviews'].get('accepted'):
            raise ValueError('Retained approval mismatch')
    assert_version(selected)
    source, execution, _ = version_assets(selected)
    final = folder/'final'
    final.mkdir(exist_ok=False)
    shutil.copy2(source, final/'source.py')
    ObjectWorkflow._publish(final, execution)
    extras = []
    if accepted:
        shutil.copytree(execution.output_root/'assembly', final/'assembly')
        extras = list((final/'assembly').rglob('*'))
    # Both the immutable selected version and final copied bytes must agree.
    if file_hash(final/'source.py') != file_hash(source) or file_hash(final/'scene.glb') != file_hash(execution.glb_path):
        raise ValueError('Published source/model mismatch')
    for render in execution.render_paths:
        if file_hash(final/'render'/render.name) != file_hash(render):
            raise ValueError('Published render mismatch')
    write_json(final/'selection.json', {'selected':'conversion' if accepted else 'original',
        'version':selected, 'assembly_approved':accepted,
        'original_is_not_automatically_assembly_approved':True,
        'physical_validation':'NOT_EVALUATED', 'source_sha256':file_hash(final/'source.py'),
        'extra_files':{str(p.relative_to(final)):file_hash(p) for p in extras if p.is_file()}})
    return accepted


def classify(folder, accepted, error=None):
    failures = []
    for path in sorted((folder/'assembly_run/rounds').rglob('assembly_manifest.json')):
        for failure in read_json(path).get('failures', []):
            failures.append({**failure, 'report':str(path)})
    shared = [f for f in failures if f.get('code') in SHARED_EXPORT_CODES]
    flow_errors = list((folder/'assembly_run').rglob('flow_error.json'))
    tool_errors = []
    for path in (folder/'assembly_run').rglob('*edit_outcome.json'):
        outcome = read_json(path)
        if outcome.get('status') == 'TOOL_ERROR':
            tool_errors.append({'report':str(path), 'reason':outcome.get('reason', '')})
    missing_report = [e for e in tool_errors if 'No such file' in e['reason']
                      and 'assembly_manifest.json' in e['reason']]
    if missing_report:
        category = 'MISSING_FEEDBACK_REPORT_TOOL_ERROR'
    elif shared or flow_errors or error:
        category = 'ASSEMBLY_IMPLEMENTATION_OR_FLOW_ERROR_REQUIRES_REVIEW'
    elif accepted:
        category = 'ACCEPTED_CONVERSION'
    else:
        category = 'AGENT_DESIGN_OR_ORIGINAL_GEOMETRY_LIMITATION'
    return {'category':category, 'pause_batch':bool(shared or flow_errors or error or missing_report),
            'geometry_failures':failures, 'shared_error_evidence':shared,
            'flow_errors':[str(p) for p in flow_errors], 'tool_errors':tool_errors, 'exception':error}


async def run_case(root, cid):
    folder = root/cid
    if (folder/'result.json').exists():
        return read_json(folder/'result.json')
    if (folder/'started.json').exists():
        raise RuntimeError(f'{cid}: already started, no automatic replay or extra budget')
    original_path = folder/'original/version.json'
    if not original_path.exists() or not read_json(original_path).get('execution'):
        result = {'case':cid,'category':'ORIGINAL_ASSET_PROBLEM','approved':False,
                  'pause_batch':False, 'reason':'Original execution/render preflight incomplete',
                  'initial_conversions':0,'repairs':0}
        write_json(folder/'result.json', result)
        return result
    original = read_json(folder/'original/version.json')
    source, original_execution, _ = version_assets(original)
    config = read_json(folder/'frozen.json')
    work = folder/'assembly_run'
    work.mkdir(exist_ok=False)
    shutil.copy2(source, work/'source.py')
    request = ObjectRequest(requirement(cid), work, f'fixed_existing_{cid}',
        max_rounds=2, checker_specs=(), fixed_assembly=config)
    workflow = ExistingAssetWorkflow(original)
    start = time.monotonic()
    write_json(folder/'started.json', {'time':time.time(), 'initial_conversion_limit':1,
        'repair_limit':1, 'frozen_config':config, 'original_sha256':file_hash(source)})
    runtime, inner, error, outcome = None, None, None, None
    initial_count = 0
    try:
        runtime = workflow._runtime(request, work, mode='existing_fixed_assembly_experiment')
        print(f'{cid}: planner', flush=True)
        plan = await plan_conversion(workflow, runtime, request, source, original_execution.render_paths)
        coder = runtime.agent(name='object-coder', tools=PATCH_TOOLS,
            instructions=object_prompt('coder', articulation=False, fixed_assembly=True))
        # Reservation is persisted BEFORE the sole initial conversion call.
        write_json(work/'initial_attempt.json', {'id':'initial_conversion', 'status':'MODEL_STARTED',
            'parent_sha256':file_hash(source), 'source':str(work/'source.py')})
        initial_count = 1
        print(f'{cid}: initial conversion (1/1)', flush=True)
        outcome = await workflow._repair(runtime=runtime, repairer=coder, workspace=work,
            source_path=work/'source.py', role='coder:initial_conversion', stage='initial_conversion',
            allow_no_change=True, reserved_attempt_id='initial_conversion',
            payload={'requirement':request.requirement, 'plan':plan.model_dump(), 'fixed_assembly':config,
                     'original_source':source.read_text(),
                     'assignment':'Read the assigned original source and PATCH its hierarchy into the planned assembly; preserve the original object. This is an edit, not random regeneration.'})
        write_json(work/'initial_edit_outcome.json', outcome)
        if outcome['status'] == 'CHANGED':
            print(f'{cid}: geometry/review; at most one repair remains', flush=True)
            inner = await iterate_fixed_assembly(workflow, runtime=runtime, request=request,
                workspace=work, source_path=work/'source.py', plan=plan)
    except Exception as exc:
        error = {'type':type(exc).__name__, 'message':str(exc)[:600]}
        (folder/'error.log').write_text(traceback.format_exc())
    accepted = select_and_publish(folder, original, inner)
    book_path = work/'assembly_versions.json'
    book = read_json(book_path) if book_path.exists() else {}
    history = work/'repair_history.jsonl'
    # Existing controller history counts even failed/no-change repair calls.
    edits = [json.loads(line) for line in history.read_text().splitlines() if line.strip()] if history.exists() else []
    repairs = len(edits)
    result = {'case':cid, 'approved':accepted, **classify(folder, accepted, error),
        'original_source_sha256':file_hash(source), 'initial_conversions':initial_count,
        'repairs':repairs, 'elapsed_seconds':time.monotonic()-start,
        'initial_edit_outcome':outcome and outcome['status'],
        'stop_reason':book.get('stop_reason') or (outcome and outcome.get('reason')) or error,
        'final_selection':str(folder/'final/selection.json'),
        'usage':asdict(runtime.usage.totals()) if runtime else {},
        'unverified':['physical fixation','printability','strength','four physical checkers']}
    if runtime:
        # Preserve actual prompts/tool calls from the existing local session store.
        src = runtime.sessions.database_path
        if src.exists():
            with sqlite3.connect(src) as db, sqlite3.connect(work/'sessions_snapshot.sqlite3') as dest:
                db.backup(dest)
    write_json(folder/'result.json', result)
    print(f'{cid}: approved={accepted}, {result["category"]}, {result["elapsed_seconds"]:.1f}s', flush=True)
    return result


async def run(root, cases):
    for cid in cases:
        result = await run_case(root, cid)
        if result['pause_batch']:
            write_json(root/'paused.json', {'case':cid,'reason':result['category']})
            print('Paused for implementation/flow review; no later cases submitted.', flush=True)
            return 2
    return 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', type=Path, required=True)
    parser.add_argument('--cases', nargs='+', choices=IDS, default=list(IDS))
    args = parser.parse_args()
    raise SystemExit(asyncio.run(run(args.root.resolve(), args.cases)))
