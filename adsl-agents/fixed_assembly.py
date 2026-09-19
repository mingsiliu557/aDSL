"""Opt-in assembly gate using existing executor, repair budget, reviews and versions.

No physical-checker execution or new agent role. The small loop is initial
evaluation + bounded isolated repairs, not the overhang optimization workflow.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from .models import ObjectRunResult, RepairProposal, RepairTarget
from .overhang_edit import version_record, version_assets, assert_version, file_hash
from .prompts import object_prompt
from .repair_controller import RepairController
from .tools import PATCH_TOOLS, READ_TOOLS
from .models import ImageCriticDecision, CodeCriticDecision
from .utils.execution import execute_asset_source, AssetExecutionError
from .utils.io import read_json, write_json


async def iterate_fixed_assembly(workflow, *, runtime, request, workspace, source_path, plan):
    from .service import _asset_executor_timeout_seconds
    config = {**request.fixed_assembly, 'assembly_plan':plan.model_dump()}
    config_hash = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    book_path = workspace/'assembly_versions.json'
    if book_path.exists():
        book = read_json(book_path)
        if book['config_sha256'] != config_hash:
            raise ValueError('cannot change frozen assembly conditions on resume')
    else:
        original = workspace/'original'/'source.py'
        original.parent.mkdir()
        shutil.copy2(source_path, original)
        book = dict(original='original', retained='original', candidate=None, versions={},
                    next_round=1, max_rounds=request.max_rounds, started_at=time.time(),
                    config_sha256=config_hash, completed=False)
        book['versions']['original'] = version_record('original', original, None)
        write_json(book_path, book)
    repairer = runtime.agent(name='object-coder', tools=PATCH_TOOLS,
        instructions=object_prompt('coder', articulation=False, fixed_assembly=True))
    image_critic = runtime.agent(name='object-image-critic', output_type=ImageCriticDecision,
        instructions=object_prompt('image_critic', articulation=False, fixed_assembly=True))
    code_critic = runtime.agent(name='object-code-critic', tools=READ_TOOLS, output_type=CodeCriticDecision,
        instructions=object_prompt('code_critic', articulation=False, fixed_assembly=True))
    feedback = book.get('feedback', {})
    stop = book.get('stop_reason', 'round_budget_exhausted')
    proposal = RepairProposal(proposal_id='assembly_or_appearance', finding_ids=['assembly_or_appearance'],
        hypothesis='Preserve requested shape and paired interface geometry', target=RepairTarget(), action='reshape')
    for number in range(book['next_round'], book['max_rounds']+1):
        if book['completed']:
            break
        retained = book['versions'][book['retained']]
        assert_version(retained)
        parent = Path(retained['source'])
        root = workspace/'rounds'/f'round_{number:02d}'
        root.mkdir(parents=True, exist_ok=True)
        version_id = 'original' if number == 1 else f'attempt_{number-1:04d}'
        current = parent
        if number > 1:
            policy = request.repair_policy.model_copy(update={'max_total_candidates':book['max_rounds']-1})
            controller = RepairController(workspace=workspace, round_root=root, baseline_source=parent,
                source_index=None, findings=[], checker_specs_sha256=config_hash, policy=policy)
            controller.started_at = book['started_at']
            if controller.budget_error():
                stop = controller.budget_error()
                break
            # Before the call: persist reservation and advance next round. An
            # interrupted edit is not called again with a fresh budget on resume.
            candidate_root, current, fingerprint = controller.prepare_candidate(proposal, number-1)
            book.update(candidate=version_id, next_round=number+1)
            controller.record(dict(attempt_id=version_id, parent=book['retained'],
                candidate=str(current), fingerprint=fingerprint, status='MODEL_STARTED'))
            write_json(book_path, book)
            outcome = await workflow._repair(runtime=runtime, repairer=repairer, workspace=workspace,
                source_path=current, role=f'coder:assembly:{number}', stage=f'assembly_repair:{number}',
                reserved_attempt_id=version_id, allow_no_change=True,
                payload={'requirement':request.requirement, 'plan':plan.model_dump(),
                    'fixed_assembly':request.fixed_assembly, 'feedback':feedback,
                    'remaining_repairs':book['max_rounds']-number,
                    'assignment':'Read the assigned source and repair the smallest relevant body/interface/assembly code. Do not edit configuration or checker files.'})
            write_json(candidate_root/'edit_outcome.json', outcome)
            if outcome['status'] != 'CHANGED':
                book['versions'][version_id] = version_record(version_id, current, None,
                    reviews={'edit_outcome':outcome, 'accepted':False})
                stop = outcome['status']
                break
        else:
            candidate_root = root
        execution, reviews = None, {'appearance_approved':None}
        report = {'status':'ERROR', 'failures':[]}
        report_path = candidate_root/'asset'/'assembly'/'assembly_manifest.json'
        try:
            execution = execute_asset_source(current, candidate_root/'asset', render=True,
                export_urdf=False, timeout=_asset_executor_timeout_seconds(), fixed_assembly=config)
            report = read_json(execution.output_root/'assembly'/'assembly_manifest.json')
            baseline = version_assets(retained)[1] if retained['execution'] else execution
            approved, reviews = await workflow._review_candidate_appearance(runtime=runtime, request=request,
                workspace=workspace, round_number=number, proposal_index=0, proposal=proposal,
                baseline_execution=baseline, candidate_execution=execution, candidate_source=current,
                candidate_root=candidate_root, image_critic=image_critic, code_critic=code_critic)
            accepted = approved and report['status'] == 'PASS'
            reason = 'interface_geometry_and_appearance_passed' if accepted else 'interface_or_appearance_rejected'
        except (AssetExecutionError, subprocess.TimeoutExpired) as error:
            diagnostic = {'type':type(error).__name__, 'error':str(error)}
            if report_path.exists():
                try:
                    saved_report = read_json(report_path)
                    if not isinstance(saved_report, dict) or not isinstance(saved_report.get('failures'), list):
                        raise ValueError('assembly manifest must contain a failures list')
                    report = saved_report
                    diagnostic['assembly_report_path'] = str(report_path)
                except (OSError, ValueError) as report_error:
                    diagnostic['assembly_report_error'] = str(report_error)[:240]
            # Execution may fail before producing any manifest. Give the next
            # Coder a real diagnostic file, never a speculative export path.
            report_path = candidate_root/'execution_error.json'
            write_json(report_path, diagnostic)
            # A valid geometric rejection is not an unavailable measurement.
            # Keep its part/interface findings even if no render was produced.
            if report.get('status') != 'FAIL':
                report['status'] = 'ERROR'
                error_lines = str(error).strip().splitlines()
                report['failures'].append({'code':'EXECUTION_UNAVAILABLE',
                    'reason':error_lines[-1][:240] if error_lines else type(error).__name__,
                    'report_path':str(report_path)})
            accepted, reason = False, 'execution_failed'
        except Exception as error:
            # Unexpected/API review failure: save evidence and stop, no blind rerun.
            report_path = candidate_root/'flow_error.json'
            write_json(report_path, {'type':type(error).__name__, 'error':str(error)})
            accepted, reason = False, 'FLOW_ERROR'
        reviews.update(geometry=report, accepted=accepted, reason=reason)
        extra = sorted((candidate_root/'asset'/'assembly').rglob('*'))
        book['versions'][version_id] = version_record(version_id, current, execution, reviews=reviews,
                                                      extra_files=extra)
        write_json(candidate_root/'decision.json', {'version':version_id, 'parent':book['retained'],
            'source_sha256':file_hash(current), 'accepted':accepted, 'reason':reason,
            'geometry_status':report['status'], 'appearance_approved':reviews.get('appearance_approved')})
        if accepted:
            book['retained'] = version_id
        feedback = {'geometry_status':report['status'], 'failures':report.get('failures',[])[:6],
            'report_path':str(report_path),
            'image_critic':reviews.get('image_critic'), 'code_critic':reviews.get('code_critic')}
        book.update(feedback=feedback, next_round=number+1)
        write_json(book_path, book)
        if accepted or reason == 'FLOW_ERROR':
            stop = reason
            break
    book.update(completed=True, stop_reason=stop)
    write_json(book_path, book)
    selected = book['versions'][book['retained']]
    assert_version(selected)
    shutil.copy2(selected['source'], source_path)
    glb, urdf, renders = None, None, []
    if selected['execution']:
        _, execution, _ = version_assets(selected)
        glb, urdf, renders = workflow._publish(workspace, execution)
        shutil.copytree(execution.output_root/'assembly', workspace/'assembly', dirs_exist_ok=True)
    else:
        # Preserve an unapproved diagnostic export if initial geometry succeeded
        # but later rendering failed; never borrow a rejected candidate's result.
        initial = workspace/'rounds'/'round_01'/'asset'/'assembly'
        if initial.exists():
            shutil.copytree(initial, workspace/'assembly', dirs_exist_ok=True)
    geometry_approved = bool(selected['reviews'].get('accepted'))
    approved = geometry_approved and not any(spec.required for spec in request.checker_specs)
    statuses = {s.name:{'status':'INDETERMINATE', 'reason':'ASSEMBLY_ANALYSIS_UNSUPPORTED'} for s in request.checker_specs}
    write_json(workspace/'checker_results.json', {'required_checkers_passed':False, 'results':[],
        'not_executed':{name:'disabled_for_fixed_assembly_v1' for name in ('topology','standing','overhang','fea')},
        'requested_unsupported':statuses})
    write_json(workspace/'assembly_result.json', {'version_id':book['retained'], 'source_sha256':file_hash(source_path),
        'approved':approved, 'interface_and_appearance_approved':geometry_approved,
        'reviews':selected['reviews'], 'stop_reason':stop,
        'physical_validation':'NOT_EVALUATED', 'requested_unsupported':statuses})
    runtime.usage.update_manifest(status='completed', mode='generate', approved=approved,
        source_path=str(source_path), glb_path=str(glb) if glb else None, urdf_path=None,
        render_paths=[str(p) for p in renders], selected_round=1 if book['retained']=='original' else int(book['retained'].split('_')[1])+1,
        finalization_reason=stop, verification_scope='interface_geometry_only', physical_checks_passed=False)
    workflow._write_checkpoint(workspace, mode='generate', stage='completed', approved=approved)
    return ObjectRunResult(workspace, source_path, glb, urdf, tuple(renders),
        1 if book['retained']=='original' else int(book['retained'].split('_')[1])+1, approved, runtime.usage.totals())
