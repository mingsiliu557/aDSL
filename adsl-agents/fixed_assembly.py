"""Assembly execution/version adapter using ordinary aDSL generation reviews.

Only explicitly requested assembly tools are enabled. The small loop is initial
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
from .models import GradedImageCriticDecision, GradedCodeCriticDecision
from .utils.execution import execute_asset_source, AssetExecutionError, AssetInfrastructureError, ExecutionResult
from .utils.io import read_json, write_json


def _failure_feedback(report, *, source_sha256=None):
    """Classify saved evidence, not the physical cause of an export exception."""
    rows = []
    for failure in report.get('failures', []):
        row = dict(failure)
        code = row.get('code', '')
        kind = row.get('failure_kind')
        diagnostic = row.get('diagnostic') if isinstance(row.get('diagnostic'), dict) else {}
        evaluation_code = diagnostic.get('code', code)
        evaluation_stage = diagnostic.get('stage', row.get('stage'))
        # These failures are observed source evaluation failures, not a physical
        # verdict. An arbitrary exporter exception remains unassessed.
        explicit_evaluation = (evaluation_code in {
            'INPUT_GEOMETRY_INVALID', 'BOOLEAN_EVALUATION_FAILED',
            'TARGET_PRECISION_UNREPRESENTABLE', 'INVALID_EVALUATED_MESH',
            'EMPTY_REQUIRED_GEOMETRY', 'INVALID_TRANSFORM', 'INVALID_PRIMITIVE',
            'INVALID_BOOLEAN_OPERATION', 'MESH_COMPONENTS_CHANGED'} and evaluation_stage in {
                'input_geometry', 'solid_evaluation', 'internal_evaluation', 'canonical_geometry', 'target_precision'})
        legacy_boolean = (code == 'BOOLEAN_RECOVERY_FAILED' or
            str(row.get('reason', '')).startswith('BOOLEAN_RECOVERY_FAILED:')) and row.get('stage') in {
                'evaluate_part', 'evaluate_body', 'solid_evaluation', 'target_precision'}
        if kind not in ('export', 'environment') and (explicit_evaluation or legacy_boolean):
            kind = 'candidate_evaluation'
            row.update(evaluation_code=evaluation_code if explicit_evaluation else 'BOOLEAN_EVALUATION_FAILED',
                       evaluation_stage=evaluation_stage if explicit_evaluation else 'solid_evaluation')
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
                   geometry_repair_allowed=kind == 'candidate_geometry' or
                       kind == 'candidate_evaluation' and (explicit_evaluation or legacy_boolean))
        if kind == 'candidate_evaluation' and source_sha256 is not None and report.get('source_sha256') != source_sha256:
            row.update(geometry_repair_allowed=False, evidence_source_status='UNAVAILABLE_OR_SOURCE_MISMATCH')
        rows.append(row)
    return rows


FAILURE_INSTRUCTION = (
    'Candidate geometry evidence (empty/missing generated parts or disconnected components) may guide '
    'local source repair. Explicit input/Boolean/target-precision failures may guide a bounded local '
    'source evaluation repair using the recorded operation, node path and attempted actions. '
    'They are not measured physical failures or proof of a particular design cause. Preserve required '
    'parts and visible features; do not change mesh processing, tolerances or checker settings. '
    'File save/read and exporter errors are unassessed, not physical failures: '
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


def _geometry_visual_ready(reviews, display_available):
    """An explicit surface-only rejection permits comparison, never approval."""
    if not display_available:
        return False
    if reviews.get('appearance_approved') is True:
        return True
    if reviews.get('appearance_approved') is not False:
        return False
    from .service import ObjectWorkflow
    resolved=ObjectWorkflow._resolved_visual_feedback(reviews.get('image_critic'),reviews.get('code_critic'))
    high=[issue for issue in resolved['issues'] if issue.get('severity')=='HIGH']
    return bool(high and all(issue.get('aspect','geometry')=='surface' for issue in high))


async def iterate_fixed_assembly(workflow, *, runtime, request, workspace, source_path, plan,
                                 initial_execution=None, initial_topology_run=None, evidence_files=()):
    from .service import _asset_executor_timeout_seconds, _actionable_findings, VISUAL_FEEDBACK_INSTRUCTION
    from .assembly_topology import (NAME, TOPOLOGY_SCOPE_VERSION, run_assembly_topology, engineer,
        prepare_evidence, evaluation_evidence_run, EVIDENCE_PATH_INSTRUCTION)
    from .assembly_physics import NAMES, run_assembly_checks, orientation_only, area_comparison
    specs = [s for s in request.checker_specs if s.name in NAMES]
    if len({s.name for s in specs}) != len(specs):
        raise ValueError('duplicate assembly checker specification')
    topology_spec = next((s for s in request.checker_specs if s.name == NAME), None)
    if sum(s.name == NAME for s in request.checker_specs) > 1:
        raise ValueError('only one assembly_topology specification is allowed')
    config = {**request.fixed_assembly, 'assembly_plan':plan.model_dump()}
    partition = config.get('physics',{}).get('overhang',{}).get('partition_objective')
    if partition is not None:
        from .partition_score import (objective_config, create_reference, load_reference,
            reference_shape_comparison, compare_partition_scores, publish_print_layout)
        objective_config(partition)
        if initial_execution is not None:
            cached_manifest=initial_execution.output_root/'assembly/assembly_manifest.json'
            if not cached_manifest.is_file() or not read_json(cached_manifest).get('partition_reference_inputs'):
                initial_execution=None
                initial_topology_run=None
        if not any(s.name=='assembly_overhang' for s in specs):
            raise ValueError('partition objective requires selected assembly_overhang')
    physics = json.loads(json.dumps(config.get('physics',{})))
    if partition is not None:
        physics['overhang']['partition_editable'] = request.repair_policy.print_partition_editable
    visual_only = config.get('validation_mode', 'geometry') == 'visual_only'
    verification_scope = 'visual_code_only' if visual_only else 'interface_geometry_only'
    frozen = {**config, 'assembly_topology_spec':topology_spec.model_dump()} if topology_spec else config
    # Keep the legacy topology-only resume hash exactly as before.
    if any(s.name != NAME for s in specs):
        frozen = {**frozen,'assembly_physics_specs':[s.model_dump() for s in specs],
                  'print_orientation_editable':request.repair_policy.print_orientation_editable}
    if partition is not None:
        frozen = {**frozen,'print_partition_editable':request.repair_policy.print_partition_editable}
    config_hash = hashlib.sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()
    if topology_spec:
        verification_scope = 'visual_code_export_and_assembly_topology'
    if any(s.name != NAME for s in specs):
        verification_scope = 'visual_code_export_and_selected_assembly_physics'
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
    if partition is not None:
        book.setdefault('partition_reference',physics.get('partition_reference_path'))
    if partition is not None and book.get('partition_reference'):
        load_reference(book['partition_reference'], partition)
    book.setdefault('working', book['retained'])
    book.setdefault('evidence_files', list(evidence_files))
    book.setdefault('qualified', book['retained'] if book['versions'][book['retained']]['reviews'].get('accepted') else None)
    image_history = book.setdefault('image_history', [])
    code_history = book.setdefault('code_history', [])
    corrections = code_history[-1].get('image_critic_corrections', []) if code_history else []
    repairer = runtime.agent(name='object-coder', tools=PATCH_TOOLS,
        instructions=object_prompt('coder', articulation=False, fixed_assembly=True))
    image_critic = runtime.agent(name='object-image-critic', output_type=GradedImageCriticDecision,
        instructions=object_prompt('image_critic_review', articulation=False, fixed_assembly=True))
    code_critic = runtime.agent(name='object-code-critic', tools=READ_TOOLS, output_type=GradedCodeCriticDecision,
        instructions=object_prompt('code_critic_review', articulation=False, fixed_assembly=True))
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
        edit_purpose = feedback.get('next_edit_purpose','required_repair') if number > 1 else 'required_repair'
        comparison_baseline = feedback.get('comparison_baseline_version') if number > 1 else None
        if edit_purpose.endswith('_optimization') and not comparison_baseline:
            comparison_baseline = book['retained'] if retained['reviews'].get('partition_ready') else book['qualified']
        if number > 1:
            # Select afresh from this version's feedback. Engineering is advice,
            # not a second candidate controller or permission to repair.
            proposal = RepairProposal(proposal_id='assembly_or_appearance',
                finding_ids=feedback.get('actionable_finding_ids') or ['assembly_or_appearance'],
                hypothesis='Use current review and measured evidence; preserve task and paired interfaces',
                target=RepairTarget(), action='reshape')
            if feedback.get('engineering_proposal'):
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
                candidate=str(current), fingerprint=fingerprint, status='MODEL_STARTED',
                edit_purpose=edit_purpose,comparison_baseline_version=comparison_baseline))
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
                    'edit_purpose':edit_purpose, 'primary_objective':feedback.get('primary_objective'),
                    'partition_guidance':feedback.get('partition_guidance'), 'grouping_change':proposal.grouping_change.model_dump() if proposal.grouping_change else None,
                    'remaining_repairs_after_this_attempt':book['max_rounds']-number,
                    'assignment':('This repair is already budget-reserved and may proceed even when remaining_repairs_after_this_attempt is 0; that count excludes the current attempt. Read the assigned source. Never change frozen physical specifications, checker configuration or measurement reference. ' + EVIDENCE_PATH_INSTRUCTION +
                        (' Primary objective: partition optimization, including repair of interfaces introduced by this same candidate. Preserve the entire original body and root frame; regroup add_part and every affected connect/frame, remove internal connectors and resolve split-child overlap. Never merge historical STL meshes. Pending HIGH surface issues remain unresolved requirements, deferred from this candidate; do not invent material APIs or modify the body to simulate gloss. Geometric visual defects and physical failures introduced by this candidate must be repaired before adoption. Keep the original comparison baseline.'
                         if edit_purpose.endswith('_optimization') else
                         ' Primary objective: necessary repair from current localized geometry/physical or reviewed appearance evidence. Regrouping may repair a real failure without requiring a score increase. Follow feedback.edit_restriction. Use only supported public APIs; if only an unsupported surface requirement remains, report NO_CHANGE. Visual changes come from feedback.resolved_visual_feedback. '+VISUAL_FEEDBACK_INSTRUCTION))})
            write_json(candidate_root/'edit_outcome.json', outcome)
            if outcome['status'] != 'CHANGED':
                book['versions'][version_id] = version_record(version_id, current, None,
                    reviews={'edit_outcome':outcome, 'accepted':False})
                stop = outcome['status']
                break
            if feedback.get('orientation_only_edit') and not orientation_only(parent.read_text(),current.read_text()):
                book['versions'][version_id] = version_record(version_id,current,None,
                    reviews={'edit_outcome':outcome,'accepted':False,'reason':'overhang_only_shape_edit_rejected'})
                stop = 'overhang_only_shape_edit_rejected'
                break
        else:
            candidate_root = root
        execution, reviews = None, {'appearance_approved':None}
        report = {'status':'ERROR', 'failures':[]}
        report_path = candidate_root/'asset'/'assembly'/'assembly_manifest.json'
        accepted, reason = False, 'interface_or_appearance_rejected'
        gate_passed = display_available = False
        check_regressed = False
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
                execution = execute_asset_source(current, candidate_root/'asset', render=True, render_view_layout='review_eight',
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
        reference_path = book.get('partition_reference') if partition is not None else None
        shape_comparison = None
        if partition is not None and reason != 'FLOW_ERROR':
            try:
                manifest_path = candidate_root/'asset/assembly/assembly_manifest.json'
                if reference_path is None:
                    reference_path = str(create_reference(manifest_path,workspace/'partition_references',partition))
                    book['partition_reference'] = reference_path
                    write_json(book_path,book)
                shape_comparison = reference_shape_comparison(manifest_path,reference_path)
            except (ValueError, KeyError, OSError, RuntimeError) as error:
                shape_comparison = {'status':'UNAVAILABLE','reason':str(error)[:240]}
        if reason != 'FLOW_ERROR':
            assembly_context = _assembly_context(current, report, version_role='current_candidate')
            if partition is not None:
                assembly_context.update(edit_purpose=edit_purpose,body_reference_comparison=shape_comparison,
                    partition_reference=reference_path,print_partition_editable=request.repair_policy.print_partition_editable)
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
                if partition is not None or image_decision is None or not image_decision.approved:
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
        runs = []
        if specs:
            asset_root=execution.output_root if execution else candidate_root/'asset'
            topology_execution = ExecutionResult(asset_root,
                asset_root/'assembly'/'scene.glb',None,(),'', '',
                source_index_path=execution.source_index_path if execution else None)
            if topology_spec and number == 1 and initial_topology_run is not None:
                topology_run=initial_topology_run
                if (topology_run.spec != topology_spec or
                    topology_run.result.assumptions.get('source_sha256') != file_hash(current) or
                    topology_run.result.assumptions.get('manifest_sha256') !=
                        file_hash(topology_execution.glb_path.parent/'assembly_manifest.json')):
                    raise ValueError('cached topology result/source/config mismatch')
                if topology_run.result.assumptions.get('topology_scope_version') != TOPOLOGY_SCOPE_VERSION:
                    topology_run=None
            runs = run_assembly_checks(specs,execution=topology_execution,source=current,
                root=candidate_root,physics=physics,initial_topology_run=topology_run,
                **({'partition_reference':reference_path} if partition is not None else {}))
            topology_run = next((r for r in runs if r.spec.name==NAME),None)
            reviews.update({r.spec.name:r.result.model_dump() for r in runs})
            reviews['checker_source_sha256'] = file_hash(current)
            if any(r.spec.required and r.result.status != 'PASS' for r in runs):
                accepted = False
                if reason != 'FLOW_ERROR': reason = 'assembly_topology_not_passed' if len(specs)==1 and topology_spec else 'assembly_required_checks_not_passed'
            elif accepted:
                reason = 'appearance_export_and_assembly_topology_passed' if len(specs)==1 and topology_spec else 'appearance_export_and_selected_checks_passed'
            # A qualified retained result cannot regress to failed/unmeasurable.
            for r in runs:
                old = retained['reviews'].get(r.spec.name)
                if (old and old['status']=='PASS' and r.result.status!='PASS' and
                    not (partition is not None and r.spec.name=='assembly_overhang' and not r.spec.required)):
                    accepted=False; check_regressed=True; reason='previously_valid_check_regressed'
            overhang = next((r for r in runs if r.spec.name=='assembly_overhang'),None)
            prior_area=retained['reviews'].get('assembly_overhang')
            if partition is None and number>1 and overhang and prior_area:
                comparison=area_comparison(prior_area,overhang.result.model_dump())
                reviews['overhang_change_vs_retained']=comparison
                pure = orientation_only(Path(retained['source']).read_text(),current.read_text())
                reviews['edit_kind']='print_orientation_only' if pure else 'structural'
                if pure and comparison['conclusion']!='IMPROVED':
                    accepted=False;reason='print_orientation_not_reliably_improved'
        optimization_finished = False
        partition_restore = None
        partition_stop_reason = None
        partition_mark_stopped = False
        partition_ready = partition_adopted = False
        if partition is not None:
            necessary_passed = all(r.result.status=='PASS' for r in runs if r.spec.required) and not any(
                spec.required and spec.name not in NAMES for spec in request.checker_specs)
            feasible_without_surface = bool(gate_passed and reason!='FLOW_ERROR' and
                _geometry_visual_ready(reviews,display_available) and necessary_passed and not check_regressed)
            reviews.update(edit_purpose=edit_purpose, comparison_baseline_version=comparison_baseline,
                body_reference_comparison=shape_comparison, partition_reference=reference_path,
                feasible_without_surface=feasible_without_surface)
            overhang = next((r for r in runs if r.spec.name=='assembly_overhang'),None)
            pure_optimization = edit_purpose.endswith('_optimization')
            if pure_optimization:
                baseline = book['versions'].get(comparison_baseline, {})
                comparison = compare_partition_scores(baseline.get('reviews',{}).get('assembly_overhang',{}),
                                                      overhang.result.model_dump() if overhang else {})
                reviews['partition_change_vs_baseline'] = comparison
                shape_matches=bool(shape_comparison and shape_comparison['status']=='MATCH')
                partition_adopted = bool(feasible_without_surface and shape_matches and comparison['conclusion']=='IMPROVED')
                accepted = bool(accepted and partition_adopted)
                if not shape_matches:
                    reason='partition_reference_shape_changed_or_unavailable'
                elif comparison['conclusion'] != 'IMPROVED':
                    reason='partition_score_'+comparison['conclusion'].lower()
                elif partition_adopted and not accepted:
                    reason='partition_improved_surface_unresolved'
                # A known shape violation or completed losing comparison needs no
                # speculative candidate repair. Preserve an existing full approval.
                optimization_finished = bool((feasible_without_surface and not partition_adopted) or
                    (shape_comparison and shape_comparison['status']=='CHANGED') or
                    (partition_adopted and book['qualified'] and not accepted))
                if optimization_finished:
                    partition_restore=comparison_baseline
                    partition_stop_reason=reason
                    partition_mark_stopped=True
            elif feasible_without_surface and shape_comparison and shape_comparison['status']=='CHANGED':
                # A necessary body repair may be geometrically ready while a
                # surface requirement is still unresolved. Start a new reference.
                # Preserve the measurement made against the previous reference.
                refresh_root = candidate_root/'partition_reference_refresh'
                try:
                    new_reference = str(create_reference(candidate_root/'asset/assembly/assembly_manifest.json',
                        workspace/'partition_references',partition))
                    refreshed = run_assembly_checks([overhang.spec], execution=topology_execution,source=current,
                        root=refresh_root,physics=physics,partition_reference=new_reference)[0]
                    runs = [refreshed if r.spec.name=='assembly_overhang' else r for r in runs]
                    reviews['assembly_overhang'] = refreshed.result.model_dump()
                    reference_path = new_reference
                    book['partition_reference'] = new_reference
                    reviews['previous_body_reference_comparison']=shape_comparison
                    shape_comparison=reference_shape_comparison(candidate_root/'asset/assembly/assembly_manifest.json',new_reference)
                    reviews.update(reference_shape_changed=True,partition_reference=new_reference,
                                   body_reference_comparison=shape_comparison)
                    overhang=refreshed
                    if refreshed.spec.required and refreshed.result.status!='PASS':
                        accepted=False; feasible_without_surface=False; reason='assembly_required_checks_not_passed'
                except (ValueError,KeyError,OSError,RuntimeError) as error:
                    reviews['partition_reference_error'] = str(error)[:240]
                    from .checkers import CheckerRun
                    unavailable = overhang.result.model_copy(deep=True)
                    unavailable.status='INDETERMINATE'
                    unavailable.summary='Required repair measured; new partition reference unavailable'
                    unavailable.metrics['partition_objective'].update(score=None,gap_voxels=None,reference_sha256=None)
                    unavailable.metrics['partition_guidance']={'status':'UNAVAILABLE','reason':'new reference unavailable'}
                    unavailable.findings=[]; unavailable.artifacts.pop('print_layout',None)
                    unavailable.artifacts.pop('partition_reference',None)
                    unavailable_dir = refresh_root/'checkers'/'assembly_overhang'
                    unavailable.artifacts['report'] = str(unavailable_dir/'report.json')
                    write_json(unavailable_dir/'result.json',unavailable.model_dump())
                    write_json(unavailable_dir/'report.json',unavailable.model_dump())
                    replacement=CheckerRun(overhang.spec,unavailable,unavailable_dir,())
                    runs=[replacement if r.spec.name=='assembly_overhang' else r for r in runs]
                    reviews['assembly_overhang']=unavailable.model_dump()
                    overhang=replacement
                    book['partition_reference'] = reference_path = None
                    if overhang.spec.required:
                        accepted=False; feasible_without_surface=False; reason='assembly_required_checks_not_passed'
            measured=overhang.result.model_dump() if overhang else {}
            partition_ready=bool(feasible_without_surface and shape_comparison and shape_comparison['status']=='MATCH'
                and compare_partition_scores(measured,measured)['conclusion']=='UNCHANGED')
            reviews.update(partition_ready=partition_ready,partition_adopted=partition_adopted,
                           feasible_without_surface=feasible_without_surface)
        reviews.update(geometry=report, accepted=accepted, reason=reason)
        failure_feedback = _failure_feedback(report, source_sha256=file_hash(current))
        evaluation_run = evaluation_evidence_run(report, failure_feedback, current,
            candidate_root/'evaluation_feedback', execution.source_index_path if execution else None)
        evidence_runs = [*runs, *([evaluation_run] if evaluation_run else [])]
        if evaluation_run:
            reviews['evaluation_failure_feedback'] = evaluation_run.result.model_dump()
        extra = sorted((candidate_root/'asset'/'assembly').rglob('*'))
        extra += sorted((candidate_root/'evaluation_feedback').rglob('*'))
        if specs:
            extra += sorted((candidate_root/'checkers').rglob('*'))
            extra += sorted((candidate_root/'partition_reference_refresh').rglob('*'))
        if partition is not None and reference_path:
            extra += sorted(Path(reference_path).parent.iterdir())
        book['versions'][version_id] = version_record(version_id, current, execution,
                                                      runs=runs,reviews=reviews,
                                                      extra_files=extra)
        book['working'] = version_id
        write_json(candidate_root/'decision.json', {'version':version_id, 'parent':parent_id,
            'source_sha256':file_hash(current), 'accepted':accepted, 'reason':reason,
            'geometry_status':report['status'], 'appearance_approved':reviews.get('appearance_approved'),
            **({k:reviews.get(k) for k in ('edit_purpose','comparison_baseline_version','partition_reference',
                'body_reference_comparison','partition_change_vs_baseline','partition_ready','partition_adopted')} if partition is not None else {})})
        if accepted:
            book['retained'] = version_id
            book['qualified'] = version_id
        elif partition is not None and not book['qualified'] and (
                partition_adopted or (edit_purpose=='required_repair' and partition_ready)):
            book['retained'] = version_id
        feedback = {'geometry_status':report['status'], 'failures':report.get('failures',[])[:6],
            'failure_feedback':failure_feedback[:6], 'failure_instruction':FAILURE_INSTRUCTION,
            'validation_mode':'visual_only' if visual_only else 'geometry',
            'export_status':report.get('export_status'),
            'report_path':str(report_path),
            'source_version':version_id, 'source_sha256':file_hash(current),
            'appearance_approved':reviews.get('appearance_approved'),
            'render_issue':reviews.get('render_issue'),
            'image_critic':reviews.get('image_critic'), 'code_critic':reviews.get('code_critic'),
            'resolved_visual_feedback':workflow._resolved_visual_feedback(
                reviews.get('image_critic'), reviews.get('code_critic'))}
        if evidence_runs:
            from .service import _checker_evidence
            feedback.update(_checker_evidence(evidence_runs,workspace=workspace))
            # Detailed absolute paths can exceed the shared scalar-preview limit.
            # Keep explicit references, not whole domains/logs, in this adapter.
            findings={f.finding_id:f for r in evidence_runs for f in r.result.findings}
            for row in feedback['typed_findings']:
                for field in ('boundary_report','report_path'):
                    value=findings[row['finding_id']].domain.get(field)
                    if value: row[field]=value
            feedback['repair_history'] = [
                {'version':v['id'], 'source_sha256':file_hash(Path(v['source'])),
                 'reason':v['reviews'].get('reason'),
                 'topology_status':v['reviews'].get('assembly_topology',{}).get('status'),
                 'tool_statuses':{s.name:v['reviews'].get(s.name,{}).get('status') for s in specs},
                 'overhang_change':v['reviews'].get('overhang_change_vs_retained')}
                for v in list(book['versions'].values())[-3:]]
        if partition is not None:
            feedback['partition_change_vs_baseline']=reviews.get('partition_change_vs_baseline')
            feedback['body_reference_comparison']=shape_comparison
            feedback['partition_guidance']=reviews.get('assembly_overhang',{}).get('metrics',{}).get('partition_guidance')
        prepare_evidence(feedback,workspace=workspace,source_sha256=file_hash(current),
                         evidence_files=book['evidence_files'])
        feedback['engineering']={'status':'NOT_REQUESTED'}
        actionable = []
        optimize = False
        if evidence_runs:
            objective=reviews.get('assembly_overhang',{}).get('metrics',{}).get('partition_objective',{})
            reference_id=objective.get('reference_sha256')
            optional_allowed=bool(partition_ready and reference_id and book.get('partition_stop_reference')!=reference_id)
            actionable = [f for r in evidence_runs for f in _actionable_findings(r)
                          if f.category!='optimization_opportunity' or
                          (request.repair_policy.print_partition_editable and optional_allowed if partition is not None
                           else request.repair_policy.print_orientation_editable)]
            optimize = any(f.category=='optimization_opportunity' for f in actionable)
            feedback['orientation_only_edit'] = bool(partition is None and actionable and reviews.get('appearance_approved') and
                all(f.category=='optimization_opportunity' for f in actionable))
            if feedback['orientation_only_edit']:
                feedback['edit_restriction']='Only literal set_print_orientation(part_id, rotation_deg=(x,y,z)) calls; keep all geometry and assembly unchanged.'
            if partition is not None:
                continuing_optimization=edit_purpose.endswith('_optimization') and not partition_adopted
                if continuing_optimization:
                    feedback.update(next_edit_purpose=edit_purpose,comparison_baseline_version=comparison_baseline)
                elif partition_ready and optimize:
                    feedback.update(next_edit_purpose='partition_optimization',comparison_baseline_version=version_id)
                else:
                    feedback.update(next_edit_purpose='required_repair',comparison_baseline_version=None)
                feedback['primary_objective']=(
                    'Repair this partition candidate using localized geometry/physical evidence; preserve the original comparison baseline.'
                    if continuing_optimization else
                    'Evaluate one local grouping hypothesis; preserve the body and defer unresolved surface-only requirements.'
                    if feedback['next_edit_purpose']=='partition_optimization' else
                    'Resolve localized necessary geometry/physical or HIGH geometry visual defects first; otherwise use supported surface controls or NO_CHANGE.')
                feedback['edit_restriction']='Preserve the entire pre-connector body shape and root frame for partition optimization. Necessary repairs follow their measured evidence. Unsupported surface controls must not be invented.'
                if partition_ready and not optimization_finished:
                    # Save before attaching a proposal; a rollback must not replay
                    # the rejected candidate's advice against a different source.
                    book.setdefault('partition_baseline_feedback',{})[version_id]=json.loads(json.dumps(feedback))
            feedback['actionable_finding_ids']=[f.finding_id for f in actionable]
            remaining = book['max_rounds']-number
            if (not accepted or optimize) and not optimization_finished and reason != 'FLOW_ERROR' and actionable and remaining > 0:
                budget = RepairController(workspace=workspace,round_root=root,baseline_source=current,
                    source_index=None,findings=[],checker_specs_sha256=config_hash,
                    policy=request.repair_policy.model_copy(update={'max_total_candidates':book['max_rounds']-1}))
                budget.started_at=book['started_at']
                if not budget.budget_error():
                    next_proposal=None
                    try:
                        next_proposal = await engineer(workflow,runtime,request,plan,current,execution,
                            candidate_root,evidence_runs if len(evidence_runs)>1 or not topology_run else topology_run,_assembly_context(current,report,version_role='current_candidate'),
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
                        feedback['engineering']={'status':'UNAVAILABLE','reason':type(error).__name__,
                            'report_path':str((candidate_root/'engineering_error.json').resolve())}
                    if partition is not None and feedback['next_edit_purpose'].endswith('_optimization') and not next_proposal:
                        partition_restore=feedback['comparison_baseline_version']
                        unavailable=feedback['engineering']['status']!='NO_PROPOSAL'
                        partition_stop_reason='partition_engineering_unavailable' if unavailable else 'no_reasonable_partition_proposal'
                        partition_mark_stopped=not unavailable
                    elif partition is None and accepted and optimize and not next_proposal:
                        optimize=False;stop='no_reasonable_orientation_proposal';book['completed']=True
                else:
                    stop='repair_budget_exhausted';book['completed']=True
            elif partition is not None and continuing_optimization and not optimization_finished and not actionable:
                partition_restore=comparison_baseline
                partition_stop_reason='partition_candidate_unverified_no_executable_feedback'
            elif (not accepted and reviews.get('appearance_approved') and not actionable
                  and not report.get('failures') and report.get('export_status') != 'FAIL'
                  and not (not visual_only and report['status']=='FAIL')):
                stop=('topology_unverified_no_executable_feedback' if len(specs)==1 and topology_spec
                      else 'assembly_checks_unverified_no_executable_feedback');book['completed']=True
        # Export unavailability alone is not a reason to change shape. Keep
        # independent visual/topology defects repairable, in the same loop.
        image_pending = bool(reviews.get('appearance_approved') is False
            and feedback['resolved_visual_feedback']['required_changes']
            and not reviews.get('render_issue'))
        if (not accepted and reason != 'FLOW_ERROR' and failure_feedback
                and not any(f['geometry_repair_allowed'] for f in failure_feedback)
                and not actionable and not image_pending
                and not any(f.get('code') == 'EXECUTION_UNAVAILABLE' for f in failure_feedback)):
            stop='export_unassessed_no_geometry_repair';book['completed']=True
        if partition_restore is not None:
            baseline=book['versions'][partition_restore]
            book['working']=partition_restore
            if not book['qualified'] or baseline['reviews'].get('accepted'):
                book['retained']=partition_restore
            if partition_mark_stopped:
                book['partition_stop_reference']=baseline['reviews'].get('assembly_overhang',{}).get('metrics',{}).get('partition_objective',{}).get('reference_sha256')
            previous_engineering=feedback.get('engineering',{})
            feedback=json.loads(json.dumps(book.get('partition_baseline_feedback',{}).get(partition_restore,{})))
            feedback.update(source_version=partition_restore,source_sha256=file_hash(Path(baseline['source'])),
                next_edit_purpose='required_repair',comparison_baseline_version=None,
                primary_objective='Address remaining required baseline appearance using supported APIs, or NO_CHANGE.',
                partition_stop_reason=partition_stop_reason)
            feedback.pop('engineering_proposal',None)
            # Preserve UNAVAILABLE as such, but never reuse candidate localization.
            if partition_restore==version_id:
                feedback['engineering']=previous_engineering
            optimize=False
            pending=feedback.get('resolved_visual_feedback',{}).get('required_changes')
            book['completed']=bool(baseline['reviews'].get('accepted') or not pending)
            stop=partition_stop_reason
        book.update(feedback=feedback, next_round=number+1)
        write_json(book_path, book)
        if accepted and not optimize or reason == 'FLOW_ERROR':
            if not book['completed'] or reason == 'FLOW_ERROR':
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
    unsupported = [s for s in request.checker_specs if s.name not in NAMES]
    approved = retained_approved and not any(spec.required for spec in unsupported)
    statuses = {s.name:{'status':'INDETERMINATE', 'reason':'ASSEMBLY_ANALYSIS_UNSUPPORTED'} for s in unsupported}
    final_topology = selected['reviews'].get('assembly_topology')
    final_checks=[selected['reviews'][s.name] for s in specs if s.name in selected['reviews']]
    requirements_satisfied=bool(all(any(r['checker']==s.name and r['status']=='PASS' for r in final_checks)
        for s in specs if s.required) and not any(s.required for s in unsupported))
    required_passed=bool(any(s.required for s in specs) and requirements_satisfied)
    if specs:
        if selected['reviews'].get('checker_source_sha256') != file_hash(source_path):
            raise ValueError('retained checker/source version mismatch')
        approved = bool(approved and requirements_satisfied)
    write_json(workspace/'checker_results.json', {'required_checkers_passed':required_passed,
        'source_sha256':file_hash(source_path),'results':final_checks,
        'not_executed':{name:'not_enabled' for name in (*NAMES,'topology','standing','overhang','fea') if name not in {s.name for s in specs}},
        'requested_unsupported':statuses})
    print_layout, print_parts = None, []
    if partition is not None:
        print_layout,print_parts=publish_print_layout(selected['reviews'].get('assembly_overhang',{}),
            workspace,file_hash(source_path),book['retained'])
    write_json(workspace/'assembly_result.json', {'version_id':book['retained'], 'source_sha256':file_hash(source_path),
        'working_version':book['working'], 'qualified_version':book['qualified'],
        **({'print_layout':print_layout,'print_parts':print_parts} if partition is not None else {}),
        'approved':approved, 'verification_scope':verification_scope,
        'visual_code_approved':selected['reviews'].get('appearance_approved'),
        'assembly_topology_status':final_topology['status'] if final_topology else 'NOT_EXECUTED',
        'geometry_validation':'NOT_EVALUATED' if visual_only else selected['reviews'].get('geometry',{}).get('status'),
        'interface_and_appearance_approved':None if visual_only else retained_approved,
        'reviews':selected['reviews'], 'stop_reason':stop,
        'physical_validation':('SELECTED_SCOPE_PASSED' if required_passed else
            'NO_REQUIRED_PHYSICAL_CHECKS' if not any(s.required for s in specs) else 'NOT_FULLY_VERIFIED') if specs else 'NOT_EVALUATED',
        'checker_statuses':{r['checker']:r['status'] for r in final_checks},'requested_unsupported':statuses})
    runtime.usage.update_manifest(status='completed', mode='generate', approved=approved,
        source_path=str(source_path), glb_path=str(glb) if glb else None, urdf_path=None,
        render_paths=[str(p) for p in renders], selected_round=1 if book['retained']=='original' else int(book['retained'].split('_')[1])+1,
        finalization_reason=stop, verification_scope=verification_scope, physical_checks_passed=required_passed)
    workflow._write_checkpoint(workspace, mode='generate', stage='completed', approved=approved)
    return ObjectRunResult(workspace, source_path, glb, urdf, tuple(renders),
        1 if book['retained']=='original' else int(book['retained'].split('_')[1])+1, approved, runtime.usage.totals())
