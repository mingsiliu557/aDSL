"""Assembly execution/version adapter using ordinary aDSL generation reviews.

Only explicitly requested assembly_topology is supported. The small loop is initial
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
from .utils.execution import execute_asset_source, AssetExecutionError, AssetInfrastructureError, ExecutionResult
from .utils.io import read_json, write_json


def _failure_feedback(report):
    """Classify saved evidence, not the physical cause of an export exception."""
    rows = []
    for failure in report.get('failures', []):
        row = dict(failure)
        code = row.get('code', '')
        kind = row.get('failure_kind')
        if kind is None:
            if code in ('DISPLAY_INCOMPLETE', 'DISCONNECTED_PRINT_PART', 'INTERNAL_PART_DISCONNECTED'):
                kind = 'candidate_geometry'
            elif code in ('EXPORTED_FILE_INVALID', 'PART_EXPORT_FAILED'):
                kind = 'export'
            elif code in ('PART_GEOMETRY_INVALID', 'PART_DISPLAY_UNAVAILABLE') and row.get('reason','').startswith((
                    'empty evaluated', 'No finite nonempty mesh available for display',
                    'mesh is not a finite closed oriented volume, or has zero-area faces')):
                kind = 'candidate_geometry'
            else:
                kind = 'unknown'
        row.update(failure_kind=kind, stage=row.get('stage') or report.get('stage') or 'unknown',
                   geometry_repair_allowed=kind == 'candidate_geometry')
        rows.append(row)
    return rows


FAILURE_INSTRUCTION = (
    'Candidate geometry evidence (empty/missing generated parts or disconnected components) may guide '
    'local source repair. File save/read and exporter errors are unassessed, not physical failures: '
    'do not change object shape to fix them. Unknown means cause unknown, not a confirmed shared bug. '
    'Use actual part IDs, stages and evidence; independently justified appearance/topology repairs remain allowed.')


def _assembly_context(source, report, *, version_role):
    """Use only this source's declarations, never the previous mesh as fact."""
    source_hash = file_hash(source)
    current = bool(report and report.get('source_sha256') == source_hash)
    return {
        'initial_plan_role':'reference_proposal_not_immutable_implementation',
        'instruction':(
            'The initial print-part/connection lists, including checklist items that merely repeat them, '
            'are a reference proposal, not an immutable implementation. Original task requirements remain '
            'binding. Default to the existing grouping. When feedback or source '
            'evidence shows a problem, minimally revise print-part boundaries, add_part/connect, frames '
            'and related construction; explain the changed locations and why. Semantic containers need '
            'not be print pieces; independent repeated pieces need their own instances and connections. '
            'Judge the original task, appearance and actual assembly, not exact equality to the initial list. '
            'Do not remove required components or connection requirements, change the root, frozen units, '
            'fit allowance, dimensions, budget or checker/configuration, or bypass existing legality checks. '
            'Declarations are source metadata, not proof of shape completeness or manufacture.'),
        'version_role':version_role, 'source_sha256':source_hash,
        'manifest_status':'CURRENT_SOURCE' if current else 'UNAVAILABLE_OR_SOURCE_MISMATCH',
        'current_assembly':{
            'root_id':report.get('root_id'),
            'parts':report.get('part_declarations', [
                {k:p[k] for k in ('id','components','assembly_transform') if k in p}
                for p in report.get('parts', [])]),
            # Old manifests may lack connection metadata. Do not fill it from plan.
            'connections':report.get('connections'),
        } if current else None,
        'plan_changes':report.get('plan_changes') if current else None,
        'task_constraints':report.get('task_constraints') if current else None,
        'failure_feedback':_failure_feedback(report)[:6] if current else [],
        'failure_instruction':FAILURE_INSTRUCTION,
    }


async def iterate_fixed_assembly(workflow, *, runtime, request, workspace, source_path, plan,
                                 initial_execution=None, initial_topology_run=None, evidence_files=()):
    from .service import _asset_executor_timeout_seconds, _actionable_findings
    from .assembly_topology import NAME, run_assembly_topology, engineer, prepare_evidence, EVIDENCE_PATH_INSTRUCTION
    topology_spec = next((s for s in request.checker_specs if s.name == NAME), None)
    if sum(s.name == NAME for s in request.checker_specs) > 1:
        raise ValueError('only one assembly_topology specification is allowed')
    config = {**request.fixed_assembly, 'assembly_plan':plan.model_dump()}
    visual_only = config.get('validation_mode', 'geometry') == 'visual_only'
    verification_scope = 'visual_code_only' if visual_only else 'interface_geometry_only'
    frozen = {**config, 'assembly_topology_spec':topology_spec.model_dump()} if topology_spec else config
    config_hash = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    if topology_spec:
        verification_scope = 'visual_code_export_and_assembly_topology'
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
    book.setdefault('working', book['retained'])
    book.setdefault('evidence_files', list(evidence_files))
    book.setdefault('qualified', book['retained'] if book['versions'][book['retained']]['reviews'].get('accepted') else None)
    image_history = book.setdefault('image_history', [])
    code_history = book.setdefault('code_history', [])
    corrections = code_history[-1].get('image_critic_corrections', []) if code_history else []
    repairer = runtime.agent(name='object-coder', tools=PATCH_TOOLS,
        instructions=object_prompt('coder', articulation=False, fixed_assembly=True))
    image_critic = runtime.agent(name='object-image-critic', output_type=ImageCriticDecision,
        instructions=object_prompt('image_critic', articulation=False, fixed_assembly=True))
    code_critic = runtime.agent(name='object-code-critic', tools=READ_TOOLS, output_type=CodeCriticDecision,
        instructions=object_prompt('code_critic', articulation=False, fixed_assembly=True))
    feedback = book.get('feedback', {})
    stop = book.get('stop_reason', 'round_budget_exhausted')
    for number in range(book['next_round'], book['max_rounds']+1):
        if book['completed']:
            break
        retained = book['versions'][book['retained']]
        assert_version(retained)
        working = book['versions'][book['working']]
        assert_version(working)
        parent = Path(working['source'])
        parent_id = book['working']
        root = workspace/'rounds'/f'round_{number:02d}'
        root.mkdir(parents=True, exist_ok=True)
        version_id = 'original' if number == 1 else f'attempt_{number-1:04d}'
        current = parent
        if number > 1:
            # Select afresh from this version's feedback. Engineering is advice,
            # not a second candidate controller or permission to repair.
            proposal = RepairProposal(proposal_id='assembly_or_appearance',
                finding_ids=feedback.get('actionable_finding_ids') or ['assembly_or_appearance'],
                hypothesis='Use current review and measured evidence; preserve task and paired interfaces',
                target=RepairTarget(), action='reshape')
            if topology_spec and feedback.get('engineering_proposal'):
                proposal = RepairProposal.model_validate(feedback['engineering_proposal'])
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
            controller.record(dict(attempt_id=version_id, parent=parent_id,
                candidate=str(current), fingerprint=fingerprint, status='MODEL_STARTED'))
            write_json(book_path, book)
            outcome = await workflow._repair(runtime=runtime, repairer=repairer, workspace=workspace,
                source_path=current, role=f'coder:assembly:{number}', stage=f'assembly_repair:{number}',
                reserved_attempt_id=version_id, allow_no_change=True,
                payload={'requirement':request.requirement, 'plan':plan.model_dump(),
                    'fixed_assembly':request.fixed_assembly, 'feedback':feedback,
                    'source_version':parent_id, 'source_sha256':file_hash(parent),
                    'evidence_files':feedback.get('evidence_files', []),
                    'assembly_context':_assembly_context(parent, working['reviews'].get('geometry'),
                                                         version_role='repair_starting_version'),
                    'current_repair_authorized':True,
                    'remaining_repairs_after_this_attempt':book['max_rounds']-number,
                    'assignment':'This repair is already budget-reserved and may proceed even when remaining_repairs_after_this_attempt is 0; that count excludes the current attempt. Read the assigned source and repair the smallest relevant body/interface/assembly code. Combine current appearance and topology facts in this single edit; Engineering advice is optional, never a geometry PASS. Unknown geometry is not confirmed disconnection. If no reasonable edit exists, use NO_CHANGE. Do not edit configuration or checker files. ' + EVIDENCE_PATH_INSTRUCTION})
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
        accepted, reason = False, 'interface_or_appearance_rejected'
        try:
            if number == 1 and initial_execution is not None:
                execution = initial_execution
                cached_report=read_json(execution.output_root/'assembly'/'assembly_manifest.json')
                if cached_report.get('source_sha256') != file_hash(current):
                    raise ValueError('cached initial assembly belongs to another source')
                for name,digest in cached_report.get('files_sha256',{}).items():
                    if file_hash(execution.output_root/'assembly'/name) != digest:
                        raise ValueError('cached initial assembly file changed')
            else:
                execution = execute_asset_source(current, candidate_root/'asset', render=True,
                    export_urdf=False, timeout=_asset_executor_timeout_seconds(), fixed_assembly=config)
            try:
                report = read_json(execution.output_root/'assembly'/'assembly_manifest.json')
                if not isinstance(report, dict) or not isinstance(report.get('failures'), list):
                    raise ValueError('assembly manifest must contain a failures list')
            except (OSError, ValueError) as error:
                report = {'status':'ERROR', 'export_status':'FAIL', 'source_sha256':file_hash(current),
                    'failures':[{'code':'EXPORTED_FILE_INVALID', 'failure_kind':'export',
                        'stage':'read_assembly_manifest', 'reason':str(error)[:240]}]}
                report_path = candidate_root/'export_error.json'
                write_json(report_path, report)
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
                    report['failures'].append({'code':'EXPORTED_FILE_INVALID', 'failure_kind':'export',
                        'stage':'read_assembly_manifest', 'reason':str(report_error)[:240]})
            # Execution may fail before producing any manifest. Give the next
            # Coder a real diagnostic file, never a speculative export path.
            report_path = candidate_root/'execution_error.json'
            write_json(report_path, diagnostic)
            # A valid geometric rejection is not an unavailable measurement.
            # Keep its part/interface findings even if no render was produced.
            if report.get('status') not in ('PASS', 'FAIL', 'NOT_EVALUATED'):
                report['status'] = 'ERROR'
                error_lines = str(error).strip().splitlines()
                if isinstance(error, AssetInfrastructureError):
                    report['failures'].append({'code':'SHARED_ENVIRONMENT_UNAVAILABLE',
                        'failure_kind':'export', 'stage':'external_executor',
                        'reason':error_lines[-1][:240], 'report_path':str(report_path)})
                elif not report['failures']:
                    report['failures'].append({'code':'EXECUTION_UNAVAILABLE',
                        'reason':error_lines[-1][:240] if error_lines else type(error).__name__,
                        'report_path':str(report_path)})
            accepted, reason = False, 'execution_failed'
            saved_execution = candidate_root/'asset'/'execution.json'
            if saved_execution.is_file():
                try:
                    raw = read_json(saved_execution)
                    glb = Path(raw['glb_path'])
                    if glb.is_file():
                        execution = ExecutionResult(candidate_root/'asset', glb, None,
                            tuple(sorted((candidate_root/'asset'/'render').glob('*.png'))), '', str(error))
                except (OSError, ValueError, KeyError, TypeError):
                    pass  # Source-only review still works if no recoverable asset exists.
        except OSError as error:
            report = {'status':'ERROR', 'export_status':'FAIL', 'source_sha256':file_hash(current),
                'failures':[{'code':'EXPORTED_FILE_INVALID', 'failure_kind':'export',
                    'stage':'execute_or_read_export', 'reason':str(error)[:240]}]}
            report_path = candidate_root/'export_error.json'
            write_json(report_path, report)
            accepted, reason = False, 'export_unavailable'
        except Exception as error:
            # Unknown is not proof of a shared process fault. Only these explicit
            # version-contract checks establish one; ordinary API errors do not.
            report_path = candidate_root/'flow_error.json'
            shared = str(error) in ('cached initial assembly belongs to another source',
                                   'cached initial assembly file changed')
            write_json(report_path, {'type':type(error).__name__, 'error':str(error),
                'code':'CURRENT_REPORT_SOURCE_MISMATCH' if shared else 'UNKNOWN_EXECUTION_ERROR'})
            accepted, reason = False, 'FLOW_ERROR'
        if reason != 'FLOW_ERROR':
            assembly_context = _assembly_context(current, report, version_role='current_candidate')
            display = report.get('diagnostic', {})
            # Availability is a controller safeguard, never evidence that the
            # semantic parts or intended shape survived CSG. Read old manifests too.
            display_available = bool(execution and execution.render_paths and reason != 'execution_failed'
                and display.get('display_available', display.get('complete', report['status'] == 'PASS')))
            render_issue = None if display_available else {
                'stage':'render', 'reason':'Current display unavailable or partial; full appearance cannot be approved.',
                'report_path':str(report_path), 'missing_parts':display.get('missing_parts', []),
                'omitted_mesh_nodes':display.get('omitted_mesh_nodes', []),
            }
            try:
                image_decision = await workflow._review_generation_image(
                    runtime=runtime, request=request, plan=plan, execution=execution,
                    round_number=number, max_rounds=book['max_rounds'], round_root=candidate_root,
                    image_critic=image_critic, image_history=image_history,
                    code_critic_corrections=corrections, render_issue=render_issue,
                    assembly_context=assembly_context)
                approved = image_decision.approved if image_decision else None
                code_decision = None
                # Same order as ordinary aDSL: Code reviews a rejected Image
                # judgement, not a NOT_EVALUATED manufacturing status.
                if image_decision is None or not image_decision.approved:
                    code_decision = await workflow._review_generation_code(
                        runtime=runtime, request=request, plan=plan, execution=execution,
                        workspace=workspace, source_path=current, round_number=number,
                        max_rounds=book['max_rounds'], round_root=candidate_root,
                        code_critic=code_critic, image_decision=image_decision,
                        code_history=code_history, render_issue=render_issue,
                        assembly_context=assembly_context)
                    approved = code_decision.approved
                    corrections = code_decision.image_critic_corrections
                if not display_available:
                    approved = None
                reviews = {'appearance_approved':approved,
                    'image_critic':image_decision.model_dump() if image_decision else None,
                    'code_critic':code_decision.model_dump() if code_decision else None,
                    'image_review_status':'COMPLETED' if image_decision else 'SKIPPED',
                    'review_mode':'generation', 'render_issue':render_issue,
                    'assembly_context':assembly_context}
                gate_passed = (report.get('export_status') == 'PASS' and report['status']=='NOT_EVALUATED'
                               if visual_only else report['status'] == 'PASS')
                accepted = bool(approved and gate_passed)
                if accepted:
                    reason = 'visual_code_and_export_passed' if visual_only else 'interface_geometry_and_appearance_passed'
            except Exception as error:
                report_path = candidate_root/'flow_error.json'
                write_json(report_path, {'type':type(error).__name__, 'error':str(error)})
                accepted, reason = False, 'FLOW_ERROR'
        topology_run = None
        if topology_spec:
            topology_execution = ExecutionResult(candidate_root/'asset',
                candidate_root/'asset'/'assembly'/'scene.glb',None,(),'', '',
                source_index_path=execution.source_index_path if execution else None)
            if number == 1 and initial_topology_run is not None:
                topology_run=initial_topology_run
                if (topology_run.spec != topology_spec or
                    topology_run.result.assumptions.get('source_sha256') != file_hash(current) or
                    topology_run.result.assumptions.get('manifest_sha256') !=
                        file_hash(topology_execution.glb_path.parent/'assembly_manifest.json')):
                    raise ValueError('cached topology result/source/config mismatch')
            else:
                topology_run = run_assembly_topology(topology_spec, execution=topology_execution,
                    source=current,root=candidate_root)
            reviews['assembly_topology'] = topology_run.result.model_dump()
            reviews['checker_source_sha256'] = file_hash(current)
            if topology_run.result.status != 'PASS':
                accepted = False
                if reason != 'FLOW_ERROR': reason = 'assembly_topology_not_passed'
            elif accepted:
                reason = 'appearance_export_and_assembly_topology_passed'
        reviews.update(geometry=report, accepted=accepted, reason=reason)
        extra = sorted((candidate_root/'asset'/'assembly').rglob('*'))
        if topology_spec:
            extra += sorted((candidate_root/'checkers').rglob('*'))
        book['versions'][version_id] = version_record(version_id, current, execution,
                                                      runs=[topology_run] if topology_run else (),reviews=reviews,
                                                      extra_files=extra)
        book['working'] = version_id
        write_json(candidate_root/'decision.json', {'version':version_id, 'parent':parent_id,
            'source_sha256':file_hash(current), 'accepted':accepted, 'reason':reason,
            'geometry_status':report['status'], 'appearance_approved':reviews.get('appearance_approved')})
        if accepted:
            book['retained'] = version_id
            book['qualified'] = version_id
        failure_feedback = _failure_feedback(report)
        feedback = {'geometry_status':report['status'], 'failures':report.get('failures',[])[:6],
            'failure_feedback':failure_feedback[:6], 'failure_instruction':FAILURE_INSTRUCTION,
            'validation_mode':'visual_only' if visual_only else 'geometry',
            'export_status':report.get('export_status'),
            'report_path':str(report_path),
            'source_version':version_id, 'source_sha256':file_hash(current),
            'appearance_approved':reviews.get('appearance_approved'),
            'render_issue':reviews.get('render_issue'),
            'image_critic':reviews.get('image_critic'), 'code_critic':reviews.get('code_critic')}
        if topology_run:
            from .service import _checker_evidence
            feedback.update(_checker_evidence([topology_run],workspace=workspace))
            # Detailed absolute paths can exceed the shared scalar-preview limit.
            # Keep explicit references, not whole domains/logs, in this adapter.
            findings={f.finding_id:f for f in topology_run.result.findings}
            for row in feedback['typed_findings']:
                for field in ('boundary_report','report_path'):
                    value=findings[row['finding_id']].domain.get(field)
                    if value: row[field]=value
            feedback['repair_history'] = [
                {'version':v['id'], 'source_sha256':file_hash(Path(v['source'])),
                 'reason':v['reviews'].get('reason'),
                 'topology_status':v['reviews'].get('assembly_topology',{}).get('status')}
                for v in list(book['versions'].values())[-3:]]
        prepare_evidence(feedback,workspace=workspace,source_sha256=file_hash(current),
                         evidence_files=book['evidence_files'])
        feedback['engineering']={'status':'NOT_REQUESTED'}
        actionable = []
        if topology_run:
            actionable = _actionable_findings(topology_run)
            feedback['actionable_finding_ids']=[f.finding_id for f in actionable]
            remaining = book['max_rounds']-number
            if not accepted and reason != 'FLOW_ERROR' and actionable and remaining > 0:
                budget = RepairController(workspace=workspace,round_root=root,baseline_source=current,
                    source_index=None,findings=[],checker_specs_sha256=config_hash,
                    policy=request.repair_policy.model_copy(update={'max_total_candidates':book['max_rounds']-1}))
                budget.started_at=book['started_at']
                if not budget.budget_error():
                    try:
                        next_proposal = await engineer(workflow,runtime,request,plan,current,execution,
                            candidate_root,topology_run,_assembly_context(current,report,version_role='current_candidate'),
                            feedback,remaining)
                        if next_proposal:
                            feedback['engineering_proposal']=next_proposal.model_dump()
                            feedback['engineering']['status']='PROPOSAL'
                        elif feedback['engineering']['status']=='NOT_REQUESTED':
                            feedback['engineering']={'status':'NO_PROPOSAL',
                                'reason':'No structured advice; use trustworthy current feedback or NO_CHANGE.'}
                    except Exception as error:
                        write_json(candidate_root/'engineering_error.json',
                            {'type':type(error).__name__,'reason':str(error)[:300]})
                        # Keep invalid/unparsed output only in the diagnostic.
                        # Coder still receives the already collected facts.
                        feedback['engineering']={'status':'UNAVAILABLE','reason':type(error).__name__,
                            'report_path':str((candidate_root/'engineering_error.json').resolve())}
                else:
                    stop='repair_budget_exhausted';book['completed']=True
            elif (not accepted and reviews.get('appearance_approved') and not actionable
                  and not report.get('failures') and report.get('export_status') != 'FAIL'
                  and not (not visual_only and report['status']=='FAIL')):
                # No infrastructure-driven blind source edits.
                stop='topology_unverified_no_executable_feedback';book['completed']=True
        # Export unavailability alone is not a reason to change shape. Keep
        # independent visual/topology defects repairable, in the same loop.
        image_pending = bool(reviews.get('image_critic') and
            not reviews['image_critic'].get('approved') and not reviews.get('render_issue'))
        if (not accepted and reason != 'FLOW_ERROR' and failure_feedback
                and not any(f['geometry_repair_allowed'] for f in failure_feedback)
                and not actionable and not image_pending
                and not any(f.get('code') == 'EXECUTION_UNAVAILABLE' for f in failure_feedback)):
            stop='export_unassessed_no_geometry_repair';book['completed']=True
        book.update(feedback=feedback, next_round=number+1)
        write_json(book_path, book)
        if accepted or reason == 'FLOW_ERROR':
            stop = reason
            break
    book.update(completed=True, stop_reason=stop)
    write_json(book_path, book)
    write_json(workspace/'working_candidate.json', {'version_id':book['working'],
        'source':book['versions'][book['working']]['source'], 'qualified_version':book['qualified'],
        'approved':bool(book['versions'][book['working']]['reviews'].get('accepted')),
        'note':'Working candidate may be invalid; not a certified final assembly'})
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
    retained_approved = bool(selected['reviews'].get('accepted'))
    unsupported = [s for s in request.checker_specs if s.name != NAME]
    approved = retained_approved and not any(spec.required for spec in unsupported)
    statuses = {s.name:{'status':'INDETERMINATE', 'reason':'ASSEMBLY_ANALYSIS_UNSUPPORTED'} for s in unsupported}
    final_topology = selected['reviews'].get('assembly_topology')
    if topology_spec:
        if selected['reviews'].get('checker_source_sha256') != file_hash(source_path):
            raise ValueError('retained checker/source version mismatch')
        approved = bool(approved and final_topology and final_topology['status']=='PASS')
    write_json(workspace/'checker_results.json', {'required_checkers_passed':bool(topology_spec and final_topology
        and final_topology['status']=='PASS' and not any(s.required for s in unsupported)),
        'source_sha256':file_hash(source_path),'results':[final_topology] if final_topology else [],
        'not_executed':{name:'disabled_for_fixed_assembly_v1' for name in ('topology','standing','overhang','fea')},
        'requested_unsupported':statuses})
    write_json(workspace/'assembly_result.json', {'version_id':book['retained'], 'source_sha256':file_hash(source_path),
        'working_version':book['working'], 'qualified_version':book['qualified'],
        'approved':approved, 'verification_scope':verification_scope,
        'visual_code_approved':selected['reviews'].get('appearance_approved'),
        'assembly_topology_status':final_topology['status'] if final_topology else 'NOT_EXECUTED',
        'geometry_validation':'NOT_EVALUATED' if visual_only else selected['reviews'].get('geometry',{}).get('status'),
        'interface_and_appearance_approved':None if visual_only else retained_approved,
        'reviews':selected['reviews'], 'stop_reason':stop,
        'physical_validation':'NOT_EVALUATED', 'requested_unsupported':statuses})
    runtime.usage.update_manifest(status='completed', mode='generate', approved=approved,
        source_path=str(source_path), glb_path=str(glb) if glb else None, urdf_path=None,
        render_paths=[str(p) for p in renders], selected_round=1 if book['retained']=='original' else int(book['retained'].split('_')[1])+1,
        finalization_reason=stop, verification_scope=verification_scope, physical_checks_passed=False)
    workflow._write_checkpoint(workspace, mode='generate', stage='completed', approved=approved)
    return ObjectRunResult(workspace, source_path, glb, urdf, tuple(renders),
        1 if book['retained']=='original' else int(book['retained'].split('_')[1])+1, approved, runtime.usage.totals())
