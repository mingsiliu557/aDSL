"""Assembly execution/version adapter using ordinary aDSL generation reviews.

Only explicitly requested assembly tools are enabled. The small loop is initial
evaluation + bounded isolated repairs, not the overhang optimization workflow.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

from .models import ObjectRunResult, RepairProposal, RepairTarget
from .overhang_edit import version_record, version_assets, assert_version, file_hash
from .prompts import object_prompt, support_prompt_options
from .repair_controller import RepairController
from .tools import PATCH_TOOLS, READ_TOOLS
from .models import GradedImageCriticDecision, GradedCodeCriticDecision
from .utils.execution import execute_asset_source, AssetExecutionError, AssetInfrastructureError, ExecutionResult
from .utils.io import read_json, write_json
from .actions import (review_image, review_code, review_physics, edit,
                      regroup_precheck, reorient_postcheck, regroup_realization,
                      support_postcheck, print_pose_postcheck, _group_mapping)
from .agent_feedback import evaluation_details
from .feedback_schema import execution_failure_summary, SOURCE_FAILURE_CODE
from .utils.request_errors import (classify_model_request_error, terminal_stop_reason,
                                   propagate_request_error, safe_request_reason)


def _failure_feedback(report, *, source_sha256=None):
    """Classify saved evidence, not the physical cause of an export exception."""
    rows = []
    failures = [(failure, 'manufacturing') for failure in report.get('failures', [])]
    failures += [(failure, 'display') for failure in report.get('display_failures', [])]
    for failure, output_role in failures:
        row = dict(failure)
        row.setdefault('output_role', output_role)
        code = row.get('code', '')
        kind = row.get('failure_kind')
        diagnostic = evaluation_details(row)
        evaluation_code = diagnostic['code'] if diagnostic['code'] is not None else code
        evaluation_stage = diagnostic['stage']
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
        if code == SOURCE_FAILURE_CODE and row.get('stage') in {'source_execution','source_contract'}:
            kind = 'candidate_source'
            row['repair_target'] = 'generated_source'
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
        if kind in {'candidate_evaluation', 'candidate_geometry'} and source_sha256 is not None and report.get('source_sha256') != source_sha256:
            row.update(geometry_repair_allowed=False, evidence_source_status='UNAVAILABLE_OR_SOURCE_MISMATCH')
        rows.append(row)
    return rows


def _bound_manifest(report, path, source, versions):
    """Missing legacy source hashes require an existing checked file binding."""
    expected = file_hash(source)
    if report.get('source_sha256') == expected:
        return report
    if report.get('source_sha256') is None:
        for version in versions.values():
            if version.get('files', {}).get(version.get('source')) != expected or str(path) not in version.get('files', {}):
                continue
            assert_version(version)
            return {**report, 'source_sha256':expected, 'source_binding_basis':'verified_version_files'}
    # Preserve the original hash and rows. A mismatched report is neither a
    # current manufacturing verdict nor authorization to change this source.
    return {**report, 'status':'ERROR', 'export_status':'FAIL',
        'source_binding':'STALE' if report.get('source_sha256') else 'UNKNOWN',
        'failures':[*report.get('failures', []), {'code':'MANIFEST_SOURCE_UNBOUND',
            'failure_kind':'export', 'stage':'read_assembly_manifest',
            'reason':'Manifest is not bound to the current source'}]}


FAILURE_INSTRUCTION = (
    'Candidate geometry evidence (empty/missing generated parts or disconnected components) may guide '
    'local source repair. Explicit input/Boolean/target-precision failures may guide a bounded local '
    'source evaluation repair using the recorded operation, node path and attempted actions. '
    'They are not measured physical failures or proof of a particular design cause. Preserve required '
    'parts and visible features; do not change mesh processing, tolerances or checker settings. '
    'File save/read and exporter errors are unassessed, not physical failures: '
    'do not change object shape to fix them. Unknown means cause unknown, not a confirmed shared bug. '
    'Use actual part IDs, stages and evidence; independently justified appearance/topology repairs remain allowed.')


def _assembly_context(source, report, *, version_role, report_ref=None):
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
        'failure_feedback':_failure_feedback(report) if current else [],
        'report_ref':report_ref,
        'failure_instruction':FAILURE_INSTRUCTION,
    }



def _partition_intent(book, working_id):
    """Restore only a recorded proposal on the current, hash-checked parent chain."""
    intent = book.get('active_partition_intent')
    if intent:
        baseline = book['versions'].get(intent['comparison_baseline_version'])
        if baseline is None:
            raise ValueError('partition intent baseline unavailable')
        assert_version(baseline)
        if file_hash(Path(baseline['source'])) != intent['comparison_baseline_source_sha256']:
            raise ValueError('partition intent baseline source changed')
        return intent
    seen = set()
    version_id = working_id
    while version_id and version_id not in seen:
        seen.add(version_id)
        version = book['versions'].get(version_id)
        if not version or version['reviews'].get('edit_purpose') != 'partition_optimization':
            break
        assert_version(version)
        review = version['reviews']
        if review.get('partition_adopted'):
            break
        proposal = review.get('repair_proposal') or {}
        baseline_id = review.get('comparison_baseline_version')
        baseline = book['versions'].get(baseline_id)
        if proposal.get('action') == 'regroup_print_parts' and proposal.get('grouping_change') and baseline:
            assert_version(baseline)
            from .models import PrintGroupingChange
            grouping = PrintGroupingChange.model_validate(proposal['grouping_change'])
            assembly = _assembly_context(Path(baseline['source']), baseline['reviews'].get('geometry'),
                                         version_role='comparison_baseline')['current_assembly']
            grouping.validate_current_assembly(assembly or {})
            return dict(schema_version=1, proposal_id=proposal['proposal_id'],
                comparison_baseline_version=baseline_id,
                comparison_baseline_source_sha256=file_hash(Path(baseline['source'])),
                grouping_change=grouping.model_dump())
        version_id = review.get('parent_version')
    return None


# Compatibility for existing internal imports.
_grouping_realization = regroup_realization


def _use_pose_display_valid(report):
    """A failed saved assembly display cannot be approved by a visual critic."""
    scene_checks = [row for row in report.get('export_consistency', [])
                    if row.get('file') == 'scene.glb']
    if any(row.get('status') != 'PASS' for row in scene_checks):
        return False
    if 'scene_glb' in report and not report['scene_glb']:
        return False
    if report.get('display_status') == 'FAIL':
        # An exploded/standalone display may fail independently. Require saved
        # assembly readback evidence rather than an earlier diagnostic preview.
        return bool(report.get('scene_glb') and scene_checks)
    return True


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


def _repair_history(book, specs):
    rows = []
    for version in list(book['versions'].values())[-3:]:
        reviews = version['reviews']
        proposal = reviews.get('repair_proposal') or {}
        geometry = reviews.get('geometry') or {}
        failure = reviews.get('execution_failure') or {}
        rows.append(dict(source_version=version['id'], source_sha256=version['files'].get(version['source']),
            parent_version=reviews.get('parent_version'), edit_purpose=reviews.get('edit_purpose'),
            action=proposal.get('action'), hypothesis=(proposal.get('hypothesis') or '')[:240],
            reason=(reviews.get('reason') or '')[:240],
            tool_statuses={s.name:reviews.get(s.name,{}).get('status') for s in specs},
            failure_codes=list(dict.fromkeys(f.get('code') for f in
                [*geometry.get('failures',[]),*geometry.get('display_failures',[])] if f.get('code'))),
            execution_error={k:failure.get(k) for k in ('exception_type','message')} if failure else None,
            candidate_report=reviews.get('geometry_report_ref') or reviews.get('execution_error_ref')))
    return rows


def _previous_failed_source(book, binding):
    for version in book['versions'].values():
        reviews = version['reviews']
        if reviews.get('execution_binding') != binding or reviews.get('accepted'):
            continue
        geometry = reviews.get('geometry') or {}
        if reviews.get('execution_failure') or any(
                f.get('geometry_repair_allowed') for f in _failure_feedback(geometry,
                    source_sha256=binding['source_sha256'])):
            assert_version(version)
            return version
    return None


def _save_support_checkpoint(book, *, baseline_id, baseline_feedback):
    baseline = book['versions'][baseline_id]
    assert_version(baseline)
    saved = deepcopy(baseline_feedback)
    saved.pop('engineering_proposal', None)
    for key in ('support_baseline_version', 'support_round'):
        saved.pop(key, None)
    book.setdefault('support_checkpoints', {}).setdefault(baseline_id, dict(
        source_sha256=file_hash(Path(baseline['source'])), retained_version=book['retained'],
        qualified_version=book['qualified'], partition_reference=book.get('partition_reference'),
        feedback=saved))


def _restore_support_baseline(book, *, baseline_id, stop_reason):
    checkpoint = book['support_checkpoints'][baseline_id]
    baseline = book['versions'][baseline_id]
    assert_version(baseline)
    if checkpoint['source_sha256'] != file_hash(Path(baseline['source'])):
        raise ValueError('support baseline source changed')
    for key in ('retained_version', 'qualified_version'):
        if checkpoint[key] is not None:
            assert_version(book['versions'][checkpoint[key]])
    feedback = deepcopy(checkpoint['feedback'])
    for key in ('engineering_proposal', 'support_baseline_version', 'support_round',
                'comparison_baseline_version', 'partition_candidate_in_progress',
                'partition_intent', 'grouping_realization'):
        feedback.pop(key, None)
    feedback.update(source_version=baseline_id, source_sha256=checkpoint['source_sha256'],
                    next_edit_purpose='required_repair', support_stop_reason=stop_reason)
    if book.get('active_partition_intent'):
        book['partition_implementation_result'] = dict(status='NOT_COMPLETED', reason=stop_reason,
            partition_intent=deepcopy(book['active_partition_intent']))
    book.update(working=baseline_id, retained=checkpoint['retained_version'],
        qualified=checkpoint['qualified_version'], partition_reference=checkpoint['partition_reference'],
        active_partition_intent=None, feedback=feedback)
    return feedback


def _support_evidence(reviews, *, source_version, source_sha256, layer_height_mm):
    from .support_volume import _nonnegative
    report = reviews.get('geometry') or {}
    result = reviews.get('assembly_overhang') or {}
    metrics = result.get('metrics') or {}
    declarations = report.get('part_declarations') or []
    ids = [p.get('id') for p in declarations]
    parts = report.get('parts') or []
    bound = (report.get('source_sha256') == source_sha256 and
             reviews.get('checker_source_sha256') == source_sha256 and
             result.get('assumptions',{}).get('source_sha256') == source_sha256)
    complete = bool(ids and all(isinstance(i,str) and i for i in ids) and len(ids)==len(set(ids)) and set(ids)=={p.get('id') for p in parts}
                    and len(parts)==len(ids))
    support = deepcopy(metrics.get('support') or {'status':'INDETERMINATE','reason':'support_unavailable'})
    count = len(ids) if complete else None
    measured_count = metrics.get('partition_objective',{}).get('print_part_count')
    if not bound or not complete or measured_count is not None and measured_count != count:
        support = dict(status='INDETERMINATE',reason='support_source_or_declarations_unavailable')
    if result.get('status') != 'PASS':
        support = dict(status='INDETERMINATE',reason='overhang_measurement_incomplete')
    area = support.get('total_risky_projected_area_mm2')
    tolerance = layer_height_mm * area if _nonnegative(area) else None
    connections = report.get('connections')
    assembled_size = report.get('assembled_size_mm')
    if assembled_size is None:
        canonical = report.get('canonical_scene_consistency') or {}
        if canonical.get('status') == 'PASS':
            assembled_size = canonical.get('assembled_size_mm')
    return dict(source_version=source_version, source_sha256=source_sha256,
        measurement_config_sha256=metrics.get('support_measurement_config_sha256'),
        support=support, part_count=count, tolerance_mm3=tolerance,
        length_unit='mm', assembled_size_mm=assembled_size,
        items=[dict(part_id=r['part_id'],support=deepcopy(r.get('support')),
                    bounds_mm=next((p.get('bounds_mm') for p in parts if p.get('id')==r['part_id']),None),
                    print_transform_mm=r.get('support_print_transform_mm'),
                    largest_regions=r.get('support_regions',[])[:3],evidence_ref=r.get('support_evidence_ref'))
               for r in metrics.get('items',[])],
        interface_count=len(connections) if isinstance(connections,list) else None,
        topology_status=reviews.get('assembly_topology',{}).get('status','NOT_EXECUTED'),
        standing_status=reviews.get('assembly_standing',{}).get('status','NOT_EXECUTED'),
        acceptance_rule='Both absolute and normalized checks must pass; S_hat=S/D^3; tau_hat=tau/D_baseline^3; split/same-count: delta_S < -tau AND delta_S_hat < -tau_hat; merge: delta_S <= tau AND delta_S_hat <= tau_hat',
        scale_note='Assembled bounding-box diagonal normalization for comparison only; fixed task dimensions still required; geometry and STL are not rescaled')


def _compare_support(baseline, candidate):
    """Require both S and S/D³ checks; preserve each decision and its units."""
    import math
    from .support_volume import _nonnegative

    tau = baseline.get('tolerance_mm3')
    result = dict(conclusion='REJECT',rule='support_unavailable',delta_support_mm3=None,
        delta_part_count=None,tolerance_mm3=tau,
        comparison_metric='absolute_and_normalized_support_v1',
        absolute_decision=None,normalized_decision=None,
        baseline_diagonal_mm=None,candidate_diagonal_mm=None,
        baseline_normalized_support=None,candidate_normalized_support=None,
        delta_normalized_support=None,normalized_tolerance=None)
    if (tau is None or not baseline.get('measurement_config_sha256') or
            baseline['measurement_config_sha256'] != candidate.get('measurement_config_sha256')):
        return dict(result,reason='measurement_contract_unavailable_or_mismatched')
    raw = support_postcheck(baseline,candidate,tolerance_mm3=tau)
    result.update(delta_support_mm3=raw['delta_support_mm3'],delta_part_count=raw['delta_part_count'],
        absolute_decision={k:raw[k] for k in ('conclusion','rule')})
    if raw['rule']=='support_unavailable':
        return result
    diagonals, cubes = [], []
    for evidence in (baseline,candidate):
        size = evidence.get('assembled_size_mm')
        if (not isinstance(size,(list,tuple)) or len(size)!=3 or
                not all(_nonnegative(v) for v in size)):
            return dict(result,reason='assembled_size_unavailable')
        diagonal = math.hypot(*size)
        cube = diagonal * diagonal * diagonal
        if not math.isfinite(cube) or cube <= 0:
            return dict(result,reason='assembled_diagonal_unusable')
        diagonals.append(diagonal)
        cubes.append(cube)
    normalized = []
    for evidence,diagonal,cube in zip((baseline,candidate),diagonals,cubes):
        support = dict(evidence['support'])
        support['total_support_volume_mm3'] /= cube
        support['total_volume_mm3'] /= cube
        support['total_risky_projected_area_mm2'] /= diagonal * diagonal
        if (not all(_nonnegative(support[k]) for k in ('total_support_volume_mm3',
                'total_volume_mm3','total_risky_projected_area_mm2')) or support['total_volume_mm3'] <= 0):
            return dict(result,reason='support_normalization_unusable')
        normalized.append(dict(support=support,part_count=evidence['part_count']))
    normalized_tau = tau / cubes[0]
    if not _nonnegative(normalized_tau) or tau > 0 and normalized_tau == 0:
        return dict(result,reason='tolerance_normalization_unusable')
    # The Action's inequalities are reused on dimensionless local copies only.
    # Never publish its normalized numbers under the *_mm3 field names.
    decision = support_postcheck(*normalized,tolerance_mm3=normalized_tau)
    accepted = raw['conclusion']=='ACCEPT' and decision['conclusion']=='ACCEPT'
    # A normalized improvement cannot override an absolute-volume rejection.
    final_decision = raw if raw['conclusion']=='REJECT' else decision
    result.update(conclusion='ACCEPT' if accepted else 'REJECT',rule=final_decision['rule'],
        normalized_decision={k:decision[k] for k in ('conclusion','rule')},
        baseline_diagonal_mm=diagonals[0],candidate_diagonal_mm=diagonals[1],
        baseline_normalized_support=normalized[0]['support']['total_support_volume_mm3'],
        candidate_normalized_support=normalized[1]['support']['total_support_volume_mm3'],
        delta_normalized_support=decision['delta_support_mm3'],normalized_tolerance=normalized_tau)
    return result


def _mode_a_pose(baseline, candidate, *, baseline_hash, candidate_hash):
    for report, digest in ((baseline,baseline_hash),(candidate,candidate_hash)):
        declarations = report.get('part_declarations') or []
        parts = report.get('parts') or []
        if (report.get('source_sha256') != digest or not declarations or
                len(declarations)!=len(parts) or
                {p.get('id') for p in declarations}!={p.get('id') for p in parts}):
            return dict(conclusion='REJECT',rule='print_pose_unavailable')
    return print_pose_postcheck(baseline.get('parts'),candidate.get('parts'))


def _shape_only_origin(book, working_id, baseline_id):
    """Recover a recorded shape-only proposal, never excuse a missing regroup intent."""
    visited = set()
    while working_id and working_id != baseline_id and working_id not in visited:
        visited.add(working_id)
        record = book['versions'].get(working_id)
        if not record:
            return False
        assert_version(record)
        review = record['reviews']
        if review.get('support_baseline_version') != baseline_id:
            return False
        proposal = review.get('repair_proposal') or {}
        if proposal.get('grouping_change'):
            return False
        if review.get('parent_version') == baseline_id:
            return proposal.get('action') in {'reshape','resize','add_local_structure'}
        working_id = review.get('parent_version')
    return False


def _shape_only_declarations(baseline, current):
    try:
        if _group_mapping(baseline) != _group_mapping(current):
            return dict(status='MISMATCH',reason='shape-only edit changed print grouping')
        root = baseline['root_id']
        if root != current['root_id']:
            return dict(status='MISMATCH',reason='shape-only edit changed root ID')
        before = next(p for p in baseline['parts'] if p['id']==root)
        after = next(p for p in current['parts'] if p['id']==root)
        if 'assembly_transform' not in before or 'assembly_transform' not in after:
            return dict(status='UNAVAILABLE',reason='root frame unavailable')
        if before['assembly_transform'] != after['assembly_transform']:
            return dict(status='MISMATCH',reason='shape-only edit changed root frame')
    except (KeyError, TypeError, ValueError, StopIteration):
        return dict(status='UNAVAILABLE',reason='bound declarations unavailable')
    return dict(status='MATCH',reason='grouping and root frame preserved; geometry checked separately')


def _refresh_support_reference(*, manifest_path, output_root, partition_config,
                               overhang_run, execution, source, physics):
    from .partition_score import create_reference
    try:
        reference = str(create_reference(manifest_path,output_root/'references',partition_config))
        refreshed = review_physics([overhang_run.spec], domains=('overhang',),
            execution=execution,source=source,root=output_root,physics=physics,
            partition_reference=reference)[0]
        if (refreshed.result.status != 'PASS' or
                refreshed.result.metrics.get('support',{}).get('status') != 'PASS'):
            return dict(status='INDETERMINATE',reference_path=None,overhang_run=refreshed,
                        reason='refreshed_support_unavailable')
        return dict(status='PASS',reference_path=reference,overhang_run=refreshed,reason=None)
    except (ValueError, OSError, RuntimeError) as error:
        return dict(status='INDETERMINATE',reference_path=None,overhang_run=None,reason=str(error)[:240])


async def iterate_fixed_assembly(workflow, *, runtime, request, workspace, source_path, plan,
                                 initial_execution=None, initial_topology_run=None, evidence_files=()):
    from .service import _asset_executor_timeout_seconds, _actionable_findings, VISUAL_FEEDBACK_INSTRUCTION
    from .assembly_topology import (NAME, TOPOLOGY_SCOPE_VERSION, run_assembly_topology, engineer,
        prepare_evidence, evaluation_evidence_run, EVIDENCE_PATH_INSTRUCTION, InvalidPartitionProposal)
    from .assembly_physics import NAMES, orientation_only, area_comparison
    specs = [s for s in request.checker_specs if s.name in NAMES]
    if len({s.name for s in specs}) != len(specs):
        raise ValueError('duplicate assembly checker specification')
    topology_spec = next((s for s in request.checker_specs if s.name == NAME), None)
    if sum(s.name == NAME for s in request.checker_specs) > 1:
        raise ValueError('only one assembly_topology specification is allowed')
    config = {**request.fixed_assembly, 'assembly_plan':plan.model_dump()}
    overhang_config = config.get('physics',{}).get('overhang',{})
    from .support_volume import support_options
    support_metric, shape_editable = support_options(overhang_config)
    nsv = support_metric == 'nsv'
    partition = overhang_config.get('partition_objective')
    mode_a = bool(nsv and shape_editable and partition is None and not request.repair_policy.print_orientation_editable)
    prompt_options = support_prompt_options(config)
    if nsv:
        import math
        layer = overhang_config.get('layer_height_mm')
        angle = overhang_config.get('overhang_threshold_from_horizontal_deg')
        if (isinstance(layer,bool) or not isinstance(layer,(int,float)) or not math.isfinite(layer) or layer <= 0 or
                isinstance(angle,bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle) or not 0 < angle < 90):
            raise ValueError('NSV requires a finite positive layer height and overhang angle in (0,90)')
        if not any(s.name=='assembly_overhang' for s in specs):
            raise ValueError('NSV requires selected assembly_overhang')
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
    if nsv:
        physics['overhang']['orientation_editable'] = request.repair_policy.print_orientation_editable
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
    relevant_inputs = dict(requirement_sha256=hashlib.sha256(request.requirement.encode()).hexdigest(),
        image_sha256=[file_hash(Path(path)) if Path(path).is_file() else None for path in request.image_paths])
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
        instructions=object_prompt('coder', articulation=False, fixed_assembly=True, **prompt_options))
    image_critic = runtime.agent(name='object-image-critic', output_type=GradedImageCriticDecision,
        instructions=object_prompt('image_critic_review', articulation=False, fixed_assembly=True))
    code_critic = runtime.agent(name='object-code-critic', tools=READ_TOOLS, output_type=GradedCodeCriticDecision,
        instructions=object_prompt('code_critic_review', articulation=False, fixed_assembly=True, **prompt_options))
    feedback = book.get('feedback', {})
    stop = book.get('stop_reason', 'round_budget_exhausted')
    try:
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
            proposal = None
            incoming_feedback = deepcopy(feedback) if nsv else None
            support_baseline_id = feedback.get('support_baseline_version') if nsv else None
            support_round = bool(nsv and feedback.get('support_round'))
            support_restore = None
            support_check = None
            if support_baseline_id:
                baseline_record = book['versions'].get(support_baseline_id)
                checkpoint = book.get('support_checkpoints',{}).get(support_baseline_id)
                if not baseline_record or not checkpoint:
                    raise ValueError('support baseline/checkpoint unavailable')
                assert_version(baseline_record)
                if file_hash(Path(baseline_record['source'])) != checkpoint['source_sha256']:
                    raise ValueError('support baseline source changed')
            edit_purpose = feedback.get('next_edit_purpose','required_repair') if number > 1 else 'required_repair'
            comparison_baseline = feedback.get('comparison_baseline_version') if number > 1 else None
            if edit_purpose.endswith('_optimization') and not comparison_baseline:
                comparison_baseline = book['retained'] if retained['reviews'].get('partition_ready') else book['qualified']
            if support_baseline_id and partition is not None:
                comparison_baseline = support_baseline_id
            intent = None
            if edit_purpose == 'partition_optimization':
                try:
                    intent = _partition_intent(book, parent_id)
                except ValueError:
                    stop = 'partition_intent_unavailable'
                    break
                if intent:
                    comparison_baseline = intent['comparison_baseline_version']
                    book['active_partition_intent'] = intent
                elif (working['reviews'].get('edit_purpose') == 'partition_optimization' and
                      not (nsv and shape_editable and support_baseline_id and
                           _shape_only_origin(book,parent_id,support_baseline_id))):
                    stop = 'partition_intent_unavailable'
                    break
            if number > 1:
                # Select afresh from this version's feedback. Engineering is advice,
                # not a second candidate controller or permission to repair.
                proposal = RepairProposal(proposal_id='assembly_or_appearance',
                    finding_ids=feedback.get('actionable_finding_ids') or ['assembly_or_appearance'],
                    hypothesis='Use current review and measured evidence; preserve task and paired interfaces',
                    target=RepairTarget(), action='reshape')
                if feedback.get('engineering_proposal'):
                    proposal = RepairProposal.model_validate(feedback['engineering_proposal'])
                try:
                    if intent and proposal.grouping_change and proposal.grouping_change.model_dump() != intent['grouping_change']:
                        raise ValueError('INVALID_PROPOSAL: active partition target cannot be replaced')
                    regroup_precheck(proposal,purpose=edit_purpose,
                        continuing=bool(comparison_baseline and comparison_baseline != parent_id and
                            working['reviews'].get('edit_purpose') == 'partition_optimization'),
                        assembly=_assembly_context(parent,working['reviews'].get('geometry'),
                            version_role='repair_starting_version')['current_assembly'],
                        editable=request.repair_policy.print_partition_editable,
                            **(dict(support_metric=support_metric,shape_editable=shape_editable) if nsv else {}))
                except ValueError as error:
                    book.update(feedback={**feedback,'engineering':{'status':'UNAVAILABLE',
                        'code':'INVALID_PROPOSAL','reason':str(error)}},stop_reason='partition_engineering_unavailable')
                    stop='partition_engineering_unavailable'
                    break
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
                if edit_purpose == 'partition_optimization' and intent is None and proposal.grouping_change is not None:
                    baseline = book['versions'][comparison_baseline]
                    assert_version(baseline)
                    intent = dict(schema_version=1, proposal_id=proposal.proposal_id,
                        comparison_baseline_version=comparison_baseline,
                        comparison_baseline_source_sha256=file_hash(Path(baseline['source'])),
                        grouping_change=proposal.grouping_change.model_dump())
                if intent:
                    book['active_partition_intent'] = intent
                book.update(candidate=version_id, next_round=number+1)
                controller.record(dict(attempt_id=version_id, parent=parent_id,
                    candidate=str(current), fingerprint=fingerprint, status='MODEL_STARTED',
                    edit_purpose=edit_purpose,comparison_baseline_version=comparison_baseline))
                write_json(book_path, book)
                try:
                    outcome = await edit(workflow, runtime=runtime, repairer=repairer, workspace=workspace,
                        source_path=current, role=f'coder:assembly:{number}', stage=f'assembly_repair:{number}',
                        reserved_attempt_id=version_id, allow_no_change=True,
                        payload={'requirement':request.requirement, 'plan':plan.model_dump(),
                            'fixed_assembly':request.fixed_assembly, 'feedback':feedback,
                            'source_version':parent_id, 'source_sha256':file_hash(parent),
                            'evidence_files':feedback.get('evidence_files', []),
                            'assembly_context':_assembly_context(parent, working['reviews'].get('geometry'),
                                version_role='repair_starting_version',
                                report_ref=working['reviews'].get('geometry_report_ref')),
                            'current_repair_authorized':True,
                            'edit_purpose':edit_purpose, 'primary_objective':feedback.get('primary_objective'),
                            **({'support_evidence':feedback.get('support_evidence'),
                                'support_baseline_version':support_baseline_id,
                                'support_round':support_round} if nsv else
                                {'partition_guidance':feedback.get('partition_guidance')}),
                            'comparison_baseline_version':comparison_baseline, 'partition_intent':intent,
                            'grouping_realization':working['reviews'].get('grouping_realization') or (
                                dict(schema_version=1,status='UNAVAILABLE',proposal_id=intent['proposal_id'],
                                    source_sha256=file_hash(parent),reason='No evaluated implementation of this intent yet') if intent else None),
                            'grouping_change':intent['grouping_change'] if intent else proposal.grouping_change.model_dump() if proposal.grouping_change else None,
                            'remaining_repairs_after_this_attempt':book['max_rounds']-number,
                            'assignment':('This repair is already budget-reserved and may proceed even when remaining_repairs_after_this_attempt is 0; that count excludes the current attempt. Read the assigned source. Never change frozen physical specifications, checker configuration or measurement reference. ' + EVIDENCE_PATH_INSTRUCTION +
                                ((' Primary objective: support optimization or repair of this same candidate. Follow support_evidence and the fixed support_baseline_version. Keep nominal scale, appearance, semantic ownership and root frame. ' + feedback.get('edit_restriction',''))
                                 if nsv and support_baseline_id else
                                 ' Primary objective: partition optimization, including repair of interfaces introduced by this same candidate. Preserve the entire original body and root frame; regroup add_part and every affected connect/frame, remove internal connectors and resolve split-child overlap. Never merge historical STL meshes. Pending HIGH surface issues remain unresolved requirements, deferred from this candidate; do not invent material APIs or modify the body to simulate gloss. Geometric visual defects and physical failures introduced by this candidate must be repaired before adoption. Keep the original comparison baseline.'
                                 if edit_purpose.endswith('_optimization') else
                                 ' Primary objective: necessary repair from current localized geometry/physical or reviewed appearance evidence. Regrouping may repair a real failure without requiring a score increase. Follow feedback.edit_restriction. Use only supported public APIs; if only an unsupported surface requirement remains, report NO_CHANGE. Visual changes come from feedback.resolved_visual_feedback. '+VISUAL_FEEDBACK_INSTRUCTION))})
                except Exception as error:
                    # Reservation was persisted before the call; never refund it.
                    classification = classify_model_request_error(error)
                    error_path = current.parent/'repair_request_error.json'
                    outcome = read_json(error_path) if error_path.is_file() else {
                        'status':'TOOL_ERROR', 'reason':safe_request_reason(error),
                        'request_error':classification, 'before_sha256':file_hash(parent),
                        'after_sha256':file_hash(current)}
                    write_json(candidate_root/'edit_outcome.json', outcome)
                    book['versions'][version_id] = version_record(version_id, current, None,
                        reviews={'edit_outcome':outcome, 'accepted':False, 'partition_intent':intent,
                                 **({'support_baseline_version':support_baseline_id,'support_round':support_round} if nsv else {}),
                                 'parent_version':parent_id,'edit_purpose':edit_purpose,
                                 'comparison_baseline_version':comparison_baseline})
                    book.update(feedback={**feedback, 'request_error':classification},
                                stop_reason='agent_call_error', completed=False)
                    if nsv and support_baseline_id:
                        feedback = _restore_support_baseline(book,baseline_id=support_baseline_id,stop_reason='agent_call_error')
                    write_json(book_path, book)
                    raise
                write_json(candidate_root/'edit_outcome.json', outcome)
                if outcome['status'] != 'CHANGED':
                    book['versions'][version_id] = version_record(version_id, current, None,
                        reviews={'edit_outcome':outcome, 'accepted':False, 'partition_intent':intent,
                                 **({'support_baseline_version':support_baseline_id,'support_round':support_round} if nsv else {}),
                                 'parent_version':parent_id,'edit_purpose':edit_purpose,
                                 'comparison_baseline_version':comparison_baseline})
                    stop = terminal_stop_reason(outcome.get('request_error')) or outcome['status']
                    break
                if feedback.get('orientation_only_edit') and not reorient_postcheck(parent.read_text(),current.read_text()):
                    book['versions'][version_id] = version_record(version_id,current,None,
                        reviews={'edit_outcome':outcome,'accepted':False,'reason':'overhang_only_shape_edit_rejected'})
                    stop = 'overhang_only_shape_edit_rejected'
                    break
            else:
                candidate_root = root
            binding = dict(source_sha256=file_hash(current), execution_config_sha256=config_hash,
                           relevant_input_hashes=relevant_inputs)
            duplicate = _previous_failed_source(book, binding) if number > 1 else None
            if duplicate:
                prior_failure = duplicate['reviews'].get('execution_failure') or next((
                    f for f in _failure_feedback(duplicate['reviews'].get('geometry') or {})
                    if f.get('geometry_repair_allowed')), {})
                previous_summary = {k:prior_failure[k] for k in ('code','stage','exception_type',
                    'evaluation_code','evaluation_stage','node_path') if k in prior_failure}
                previous_summary.update(source_sha256=file_hash(current),source_version=duplicate['id'],
                    message=str(prior_failure.get('message') or prior_failure.get('reason') or '')[:240])
                duplicate_info = dict(status='DUPLICATE_PREVIOUS_FAILED_SOURCE',
                    previous_failure_summary=previous_summary,
                    duplicate_of_version=duplicate['id'], source_sha256=file_hash(current), executed=False,
                    previous_result_ref=duplicate['reviews'].get('geometry_report_ref') or
                        duplicate['reviews'].get('execution_error_ref'))
                duplicate_reviews = dict(accepted=False, reason='DUPLICATE_PREVIOUS_FAILED_SOURCE',
                    duplicate_source=duplicate_info, parent_version=parent_id, edit_purpose=edit_purpose,
                    partition_intent=intent, comparison_baseline_version=comparison_baseline,
                    **({'support_baseline_version':support_baseline_id} if nsv else {}),
                    repair_proposal=proposal.model_dump() if proposal else None)
                write_json(candidate_root/'decision.json', duplicate_reviews)
                book['versions'][version_id] = version_record(version_id,current,None,reviews=duplicate_reviews,
                    extra_files=[candidate_root/'decision.json',candidate_root/'edit_outcome.json'])
                feedback = json.loads(json.dumps(feedback))
                feedback.update(duplicate_source=duplicate_info, repair_history=_repair_history(book,specs))
                # The next assigned source remains working, not this rejected repeat.
                book.update(feedback=feedback,candidate=None,next_round=number+1)
                write_json(book_path,book)
                stop = 'round_budget_exhausted'
                continue
            terminal_request_error, propagate_error = None, None
            execution, reviews = None, {'appearance_approved':None}
            source_failure = None
            report = {'status':'ERROR', 'source_sha256':file_hash(current), 'failures':[]}
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
                    report = _bound_manifest(report, execution.output_root/'assembly'/'assembly_manifest.json',
                                             current, book['versions'])
                except (OSError, ValueError) as error:
                    report = {'status':'ERROR', 'export_status':'FAIL', 'source_sha256':file_hash(current),
                        'failures':[{'code':'EXPORTED_FILE_INVALID', 'failure_kind':'export',
                            'stage':'read_assembly_manifest', 'reason':str(error)[:240]}]}
                    report_path = candidate_root/'export_error.json'
                    write_json(report_path, report)
            except (AssetExecutionError, subprocess.TimeoutExpired) as error:
                diagnostic = {'type':type(error).__name__, 'error':str(error)}
                source_failure = execution_failure_summary(getattr(error,'diagnostic',None),
                    source_path=current, source_version=version_id)
                if source_failure is not None:
                    diagnostic['execution_failure'] = source_failure
                    source_failure['evidence_ref'] = dict(path=str(candidate_root/'execution_error.json'),
                        json_pointer='/execution_failure', source_sha256=file_hash(current), source_version=version_id)
                    report = {'status':'ERROR', 'source_sha256':file_hash(current), 'failures':[source_failure]}
                    reviews['execution_failure'] = source_failure
                if source_failure is None and report_path.exists():
                    try:
                        saved_report = read_json(report_path)
                        if not isinstance(saved_report, dict) or not isinstance(saved_report.get('failures'), list):
                            raise ValueError('assembly manifest must contain a failures list')
                        report = _bound_manifest(saved_report, report_path, current, book['versions'])
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
                classification = classify_model_request_error(error)
                terminal_request_error = classification if terminal_stop_reason(classification) else None
                if propagate_request_error(error, classification):
                    propagate_error = error
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
                    shape_comparison = reference_shape_comparison(manifest_path,reference_path,config=partition)
                except (ValueError, KeyError, OSError, RuntimeError) as error:
                    shape_comparison = {'status':'UNAVAILABLE','reason':str(error)[:240]}
            if reason != 'FLOW_ERROR':
                # Critics precede evaluation_feedback/result.json. Bind to the
                # already written manifest, not to a future report or error text.
                geometry_path = (execution.output_root/'assembly'/'assembly_manifest.json'
                                 if execution else candidate_root/'asset'/'assembly'/'assembly_manifest.json')
                if not geometry_path.is_file():
                    geometry_path = candidate_root/'geometry_report.json'
                    write_json(geometry_path, report)
                geometry_report_ref = {'path':str(geometry_path),
                    'source_sha256':report.get('source_sha256'), 'source_version':version_id}
                reviews['geometry_report_ref'] = geometry_report_ref
                if report_path != geometry_path:
                    reviews['execution_error_ref'] = {'path':str(report_path),
                        'source_sha256':file_hash(current), 'source_version':version_id}
                assembly_context = _assembly_context(current, report, version_role='current_candidate',
                                                      report_ref=geometry_report_ref)
                if partition is not None:
                    assembly_context.update(edit_purpose=edit_purpose,body_reference_comparison=shape_comparison,
                        partition_reference=reference_path,print_partition_editable=request.repair_policy.print_partition_editable,
                        **(dict(support_metric=support_metric,shape_editable=shape_editable) if nsv else {}))
                display = report.get('diagnostic', {})
                # Availability is a controller safeguard, never evidence that the
                # semantic parts or intended shape survived CSG. Read old manifests too.
                display_available = bool(execution and execution.render_paths and reason != 'execution_failed'
                    and _use_pose_display_valid(report)
                    and display.get('display_available', display.get('complete', report['status'] == 'PASS')))
                render_issue = None if display_available else {
                    'stage':'render', 'reason':'Current display unavailable or partial; full appearance cannot be approved.',
                    'report_path':str(report_path), 'missing_parts':display.get('missing_parts', []),
                    'omitted_mesh_nodes':display.get('omitted_mesh_nodes', []),
                }
                try:
                    image_decision = None if source_failure else await review_image(workflow,
                        runtime=runtime, request=request, plan=plan, execution=execution,
                        round_number=number, max_rounds=book['max_rounds'], round_root=candidate_root,
                        image_critic=image_critic, image_history=image_history,
                        code_critic_corrections=corrections, render_issue=render_issue,
                        assembly_context=assembly_context, source_path=current, source_version=version_id)
                    approved = image_decision.approved if image_decision else None
                    code_decision = None
                    # Same order as ordinary aDSL: Code reviews a rejected Image
                    # judgement, not a NOT_EVALUATED manufacturing status.
                    if source_failure is None and (partition is not None or image_decision is None or not image_decision.approved):
                        code_decision = await review_code(workflow,
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
                        'assembly_context':assembly_context,
                        'geometry_report_ref':geometry_report_ref,
                        'execution_error_ref':reviews.get('execution_error_ref'),
                        'execution_failure':source_failure}
                    if source_failure:
                        reviews.update(image_review_status='SKIPPED_SOURCE_EXECUTION_FAILED',
                                       code_review_status='SKIPPED_SOURCE_EXECUTION_FAILED')
                    gate_passed = (report.get('manufacturing_status', report.get('export_status')) == 'PASS'
                                  and report['status']=='NOT_EVALUATED' and not report.get('failures') if visual_only else
                                  report['status']=='PASS' and report.get('manufacturing_status','PASS')=='PASS')
                    accepted = bool(approved and gate_passed)
                    if accepted:
                        reason = 'visual_code_and_export_passed' if visual_only else 'interface_geometry_and_appearance_passed'
                except Exception as error:
                    report_path = candidate_root/'flow_error.json'
                    classification = classify_model_request_error(error)
                    write_json(report_path, {'type':type(error).__name__,
                        'error':safe_request_reason(error), 'request_error':classification})
                    reviews['request_error'] = classification
                    terminal_request_error = classification if terminal_stop_reason(classification) else None
                    if propagate_request_error(error, classification):
                        propagate_error = error
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
                if source_failure:
                    from .checkers import CheckerRun
                    from .models import CheckerResult
                    runs = []
                    for spec in specs:
                        result = CheckerResult(checker=spec.name, status='INDETERMINATE',
                            summary='NOT_EVALUATED: generated source execution failed',
                            assumptions={'reason':'SOURCE_EXECUTION_FAILED','source_sha256':file_hash(current)})
                        folder = candidate_root/'checkers'/spec.name
                        write_json(folder/'result.json',result.model_dump())
                        runs.append(CheckerRun(spec,result,folder,()))
                else:
                    runs = review_physics(specs,domains=None,execution=topology_execution,source=current,
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
                if not nsv and partition is None and number>1 and overhang and prior_area:
                    comparison=area_comparison(prior_area,overhang.result.model_dump())
                    reviews['overhang_change_vs_retained']=comparison
                    pure = orientation_only(Path(retained['source']).read_text(),current.read_text())
                    reviews['edit_kind']='print_orientation_only' if pure else 'structural'
                    if pure and comparison['conclusion']!='IMPROVED':
                        accepted=False;reason='print_orientation_not_reliably_improved'
            baseline_assembly = None
            if intent:
                baseline_record = book['versions'][intent['comparison_baseline_version']]
                baseline_assembly = _assembly_context(Path(baseline_record['source']),
                    baseline_record['reviews'].get('geometry'),version_role='comparison_baseline')['current_assembly']
            realization = regroup_realization(intent, baseline_assembly,
                _assembly_context(current,report,version_role='current_candidate')['current_assembly'],
                source_sha256=file_hash(current))
            if nsv and support_baseline_id and intent is None and partition is not None:
                baseline_record = book['versions'][support_baseline_id]
                baseline_assembly = _assembly_context(Path(baseline_record['source']),baseline_record['reviews'].get('geometry'),
                    version_role='support_baseline')['current_assembly']
                realization = _shape_only_declarations(baseline_assembly,
                    _assembly_context(current,report,version_role='current_candidate')['current_assembly'])
            if nsv and support_baseline_id and partition is not None and shape_comparison and (
                    shape_comparison.get('semantic_ownership_preserved') is False or
                    shape_comparison.get('root_frame_preserved') is False):
                realization = {**(realization or {}), 'status':'MISMATCH',
                    'source_sha256':file_hash(current),
                    'reason':'Support candidate must preserve semantic ownership and root frame'}
            repairable_implementation_issue = bool(source_failure or realization and realization['status']=='MISMATCH')
            implementation_complete = (realization is None if intent is None else False) or (realization is not None and realization['status']=='MATCH')
            if not implementation_complete:
                accepted = False
            reviews.update(partition_intent=intent, grouping_realization=realization)
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
                if pure_optimization and nsv:
                    pass  # S/N decision below, after any required reference refresh.
                elif pure_optimization:
                    baseline = book['versions'].get(comparison_baseline, {})
                    comparison = compare_partition_scores(baseline.get('reviews',{}).get('assembly_overhang',{}),
                                                          overhang.result.model_dump() if overhang else {})
                    reviews['partition_change_vs_baseline'] = comparison
                    shape_matches=bool(shape_comparison and shape_comparison['status']=='MATCH')
                    partition_adopted = bool(implementation_complete and feasible_without_surface and shape_matches and comparison['conclusion']=='IMPROVED')
                    accepted = bool(accepted and partition_adopted)
                    if not shape_matches:
                        reason='partition_reference_shape_changed_or_unavailable'
                    elif comparison['conclusion'] != 'IMPROVED':
                        reason='partition_score_'+comparison['conclusion'].lower()
                    elif partition_adopted and not accepted:
                        reason='partition_improved_surface_unresolved'
                    # A known shape violation or completed losing comparison needs no
                    # speculative candidate repair. Preserve an existing full approval.
                    optimization_finished = bool(implementation_complete and ((feasible_without_surface and not partition_adopted) or
                        (shape_comparison and shape_comparison['status']=='CHANGED') or
                        (partition_adopted and book['qualified'] and not accepted)))
                    if not implementation_complete:
                        reason = 'partition_implementation_incomplete'
                    if optimization_finished:
                        partition_restore=comparison_baseline
                        partition_stop_reason=reason
                        partition_mark_stopped=True
                elif not nsv and feasible_without_surface and shape_comparison and shape_comparison['status']=='CHANGED':
                    # A necessary body repair may be geometrically ready while a
                    # surface requirement is still unresolved. Start a new reference.
                    # Preserve the measurement made against the previous reference.
                    refresh_root = candidate_root/'partition_reference_refresh'
                    try:
                        new_reference = str(create_reference(candidate_root/'asset/assembly/assembly_manifest.json',
                            workspace/'partition_references',partition))
                        refreshed = review_physics([overhang.spec], domains=('overhang',), execution=topology_execution,source=current,
                            root=refresh_root,physics=physics,partition_reference=new_reference)[0]
                        runs = [refreshed if r.spec.name=='assembly_overhang' else r for r in runs]
                        reviews['assembly_overhang'] = refreshed.result.model_dump()
                        reference_path = new_reference
                        book['partition_reference'] = new_reference
                        reviews['previous_body_reference_comparison']=shape_comparison
                        shape_comparison=reference_shape_comparison(candidate_root/'asset/assembly/assembly_manifest.json',new_reference,config=partition)
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
                partition_ready=bool(implementation_complete and feasible_without_surface and shape_comparison and shape_comparison['status']=='MATCH'
                    and compare_partition_scores(measured,measured)['conclusion']=='UNCHANGED')
                reviews.update(partition_ready=partition_ready,partition_adopted=partition_adopted,
                               feasible_without_surface=feasible_without_surface)
            if nsv:
                reviews['geometry'] = report
                pose_check = None
                if mode_a:
                    # No pose is inferred from a source execution failure. The
                    # first bound, actual output must itself be a valid A baseline.
                    original = next((v for v in book['versions'].values() if
                        (v['reviews'].get('mode_a_pose_check') or {}).get('conclusion')=='ACCEPT'),None)
                    initial_report = original['reviews']['geometry'] if original else report
                    pose_check = _mode_a_pose(initial_report,report,
                        baseline_hash=file_hash(Path(original['source'])) if original else file_hash(current),
                        candidate_hash=file_hash(current))
                    if pose_check['conclusion'] != 'ACCEPT':
                        accepted = False
                        reason = pose_check['rule']
                        if original is not None and pose_check['rule'] in {'part_count_changed','part_identity_changed','print_pose_changed'}:
                            repairable_implementation_issue = True
                        if original is None and report.get('parts'):
                            book['completed'] = True
                            stop = 'mode_a_initial_conditions_not_met'
                if partition is not None:
                    can_refresh = bool(feasible_without_surface and implementation_complete and shape_comparison and
                        shape_comparison['status']=='CHANGED' and
                        shape_comparison.get('semantic_ownership_preserved') is True and
                        shape_comparison.get('root_frame_preserved') is True and (shape_editable or not support_baseline_id))
                    if can_refresh and overhang is not None:
                        refreshed = _refresh_support_reference(
                            manifest_path=candidate_root/'asset/assembly/assembly_manifest.json',
                            output_root=candidate_root/'partition_reference_refresh',partition_config=partition,
                            overhang_run=overhang,execution=topology_execution,source=current,physics=physics)
                        reviews['support_reference_refresh'] = {k:v for k,v in refreshed.items() if k!='overhang_run'}
                        if refreshed['overhang_run'] is not None:
                            overhang = refreshed['overhang_run']
                        else:
                            from .checkers import CheckerRun
                            failed = overhang.result.model_copy(deep=True)
                            failed.status = 'INDETERMINATE'
                            failed.metrics['support'] = dict(status='INDETERMINATE',reason=refreshed['reason'])
                            failed.findings = []
                            failed.artifacts.pop('print_layout',None)
                            failed.artifacts.pop('partition_reference',None)
                            failure_root=candidate_root/'partition_reference_refresh/checkers/assembly_overhang'
                            write_json(failure_root/'result.json',failed.model_dump())
                            overhang=CheckerRun(overhang.spec,failed,failure_root,())
                        runs = [overhang if r.spec.name=='assembly_overhang' else r for r in runs]
                        reviews['assembly_overhang'] = overhang.result.model_dump()
                        reference_path = refreshed['reference_path']
                        book['partition_reference'] = reference_path
                        reviews['previous_body_reference_comparison'] = shape_comparison
                        shape_comparison = (reference_shape_comparison(
                            candidate_root/'asset/assembly/assembly_manifest.json',reference_path,config=partition)
                            if reference_path else {'status':'UNAVAILABLE','reason':refreshed['reason']})
                        reviews.update(partition_reference=reference_path,body_reference_comparison=shape_comparison)
                    measured = overhang.result.model_dump() if overhang else {}
                    partition_ready = bool(implementation_complete and feasible_without_surface and shape_comparison and
                        shape_comparison['status']=='MATCH' and measured.get('status')=='PASS' and
                        measured.get('metrics',{}).get('support',{}).get('status')=='PASS' and
                        compare_partition_scores(measured,measured)['conclusion']=='UNCHANGED')
                support_evidence = _support_evidence(reviews,source_version=version_id,
                    source_sha256=file_hash(current),layer_height_mm=overhang_config['layer_height_mm'])
                if support_evidence['support'].get('status') != 'PASS':
                    accepted = False
                if support_baseline_id:
                    base = book['versions'][support_baseline_id]
                    base_evidence = _support_evidence(base['reviews'],source_version=support_baseline_id,
                        source_sha256=file_hash(Path(base['source'])),layer_height_mm=overhang_config['layer_height_mm'])
                    support_evidence['tolerance_mm3'] = base_evidence['tolerance_mm3']
                    support_evidence['comparison_baseline'] = {k:deepcopy(base_evidence[k]) for k in (
                        'source_version','source_sha256','measurement_config_sha256','support','part_count','tolerance_mm3','assembled_size_mm')}
                    # Physical/geometric failures may still be repaired. Lack of support
                    # evidence alone is not a repair authorization.
                    required_ready = bool(gate_passed and implementation_complete and
                        not any(retained['reviews'].get(r.spec.name,{}).get('status')=='PASS' and r.result.status!='PASS'
                                for r in runs if r.spec.name!='assembly_overhang') and
                        all(r.result.status=='PASS' for r in runs if r.spec.required and r.spec.name!='assembly_overhang') and
                        not any(spec.required and spec.name not in NAMES for spec in request.checker_specs) and
                        _geometry_visual_ready(reviews,display_available) and
                        (not mode_a or pose_check['conclusion']=='ACCEPT'))
                    if required_ready:
                        support_check = _compare_support(base_evidence,support_evidence)
                        body_ok = bool(partition is None or shape_comparison and shape_comparison['status']=='MATCH')
                        if support_check['conclusion']=='ACCEPT' and body_ok:
                            partition_adopted = bool(partition is not None and partition_ready)
                            accepted = bool(accepted and (partition is None or partition_adopted))
                            if partition_adopted and not accepted and book['qualified']:
                                support_restore = support_baseline_id
                                reason = 'support_improved_surface_unresolved'
                        else:
                            accepted = False
                            reason = support_check['rule'] if body_ok else 'support_body_constraint_unavailable'
                            support_restore = support_baseline_id
                    else:
                        accepted = False
                    if support_restore:
                        optimization_finished = True
                        partition_restore = None
                        partition_stop_reason = reason
                reviews.update(support_round=support_round,support_baseline_version=support_baseline_id,
                    support_postcheck=support_check,support_postcheck_reason=None if support_check else 'not_ready_or_independent_repair',
                    support_evidence=support_evidence,mode_a_pose_check=pose_check)
                if support_baseline_id:
                    reviews['support_baseline_evidence'] = base_evidence
                    body_status = reviews.get('previous_body_reference_comparison',shape_comparison) or {}
                    reviews['edit_kind'] = ('unknown' if body_status.get('status') not in {'MATCH','CHANGED'} else
                        'both' if intent and body_status['status']=='CHANGED' else 'grouping_only' if intent else
                        'shape_only' if body_status['status']=='CHANGED' else 'none')
                if partition is not None:
                    reviews.update(partition_ready=partition_ready,partition_adopted=partition_adopted)
            reviews.update(geometry=report, accepted=accepted, reason=reason,
                execution_binding=binding, parent_version=parent_id if number > 1 else None,
                edit_purpose=edit_purpose, repair_proposal=proposal.model_dump() if proposal else None)
            failure_feedback = _failure_feedback(report, source_sha256=file_hash(current))
            evaluation_run = evaluation_evidence_run(report, failure_feedback, current,
                candidate_root/'evaluation_feedback', execution.source_index_path if execution else None)
            evidence_runs = [*runs, *([evaluation_run] if evaluation_run else [])]
            if evaluation_run:
                reviews['evaluation_failure_feedback'] = evaluation_run.result.model_dump()
            extra = [candidate_root/name for name in ('geometry_report.json','execution_error.json', 'asset/source_execution_error.json')
                     if (candidate_root/name).is_file()]
            extra += sorted((candidate_root/'asset'/'assembly').rglob('*'))
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
                    'body_reference_comparison','partition_change_vs_baseline','partition_ready','partition_adopted','partition_intent','grouping_realization')} if partition is not None else {})})
            if accepted:
                book['retained'] = version_id
                book['qualified'] = version_id
            elif partition is not None and not book['qualified'] and not (nsv and support_baseline_id and not partition_adopted) and (
                    partition_adopted or (edit_purpose=='required_repair' and partition_ready)):
                book['retained'] = version_id
            if partition_adopted:
                book['active_partition_intent'] = None
            feedback = {'partition_intent':intent, 'grouping_realization':realization, 'geometry_status':report['status'], 'failures':report.get('failures',[]),
                'manufacturing_status':report.get('manufacturing_status'),
                'display_status':report.get('display_status'), 'display_failures':report.get('display_failures',[]),
                'failure_feedback':failure_feedback, 'failure_instruction':FAILURE_INSTRUCTION,
                'validation_mode':'visual_only' if visual_only else 'geometry',
                'export_status':report.get('export_status'),
                'report_path':str(report_path),
                'geometry_report_ref':reviews.get('geometry_report_ref'),
                'execution_error_ref':reviews.get('execution_error_ref'),
                'execution_failure':source_failure,
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
            if nsv:
                feedback['support_evidence'] = support_evidence
                if mode_a:
                    feedback['mode_a_pose_check'] = pose_check
                if support_baseline_id and not accepted and not partition_adopted:
                    feedback.update(support_baseline_version=support_baseline_id,support_round=False)
                elif support_baseline_id and (accepted or partition_adopted):
                    support_baseline_id = None
            feedback['repair_history'] = _repair_history(book,specs)
            if partition is not None and not nsv:
                feedback['partition_change_vs_baseline']=reviews.get('partition_change_vs_baseline')
                feedback['body_reference_comparison']=shape_comparison
                feedback['partition_guidance']=reviews.get('assembly_overhang',{}).get('metrics',{}).get('partition_guidance')
            if source_failure:
                feedback.update(primary_objective='Fix the bound generated-source API/runtime error first. Do not propose appearance changes without a current render. Keep mesh backends and checker settings unchanged.',
                                next_edit_purpose='required_repair')
            prepare_evidence(feedback,workspace=workspace,source_sha256=file_hash(current),
                             evidence_files=book['evidence_files'])
            feedback['engineering']={'status':'NOT_REQUESTED'}
            actionable = []
            optimize = False
            if evidence_runs:
                objective=reviews.get('assembly_overhang',{}).get('metrics',{}).get('partition_objective',{})
                reference_id=objective.get('reference_sha256')
                optional_allowed=bool(partition_ready and reference_id and book.get('partition_stop_reference')!=reference_id)
                if nsv:
                    all_findings = [f for r in evidence_runs for f in _actionable_findings(r)]
                    def permitted(f):
                        if f.category != 'optimization_opportunity':
                            return True
                        if f.rule_id == 'SUPPORT_SHAPE_OPPORTUNITY':
                            return shape_editable and support_evidence['support'].get('status')=='PASS' and (partition is None or optional_allowed)
                        if f.rule_id == 'PRINT_PARTITION_OPPORTUNITY':
                            return request.repair_policy.print_partition_editable and optional_allowed
                        if f.rule_id == 'PRINT_ORIENTATION_OPPORTUNITY':
                            return request.repair_policy.print_orientation_editable and (partition is None or optional_allowed)
                        return False
                    actionable = [f for f in all_findings if permitted(f)]
                    necessary = [f for f in actionable if f.category!='optimization_opportunity']
                    appearance_pending = not reviews.get('appearance_approved')
                    if necessary or appearance_pending or repairable_implementation_issue or not gate_passed or book['completed']:
                        actionable = necessary
                    optimize = any(f.category=='optimization_opportunity' for f in actionable)
                    feedback['orientation_only_edit'] = bool(partition is None and actionable and request.repair_policy.print_orientation_editable and
                        reviews.get('appearance_approved') and all(f.rule_id=='PRINT_ORIENTATION_OPPORTUNITY' for f in actionable))
                else:
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
                    feedback['partition_candidate_in_progress'] = continuing_optimization
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
                if nsv:
                    if support_baseline_id:
                        feedback.update(support_baseline_version=support_baseline_id,support_round=False,
                            next_edit_purpose='partition_optimization' if partition is not None else 'required_repair',
                            comparison_baseline_version=support_baseline_id if partition is not None else None,
                            partition_candidate_in_progress=partition is not None)
                    elif optimize:
                        new_baseline = version_id if partition is not None else book['retained']
                        if new_baseline != version_id:
                            optimize=False
                            actionable=[f for f in actionable if f.category!='optimization_opportunity']
                        else:
                            _save_support_checkpoint(book,baseline_id=new_baseline,baseline_feedback=feedback)
                            feedback.update(support_baseline_version=new_baseline,support_round=True,
                                next_edit_purpose='partition_optimization' if partition is not None else 'required_repair',
                                comparison_baseline_version=new_baseline if partition is not None else None,
                                partition_candidate_in_progress=False)
                            book['feedback']=feedback
                            write_json(book_path,book)
                    feedback['primary_objective'] = ('Complete the same support candidate; eventual adoption requires both absolute S and normalized S_hat=S/D^3 checks, each with its N-dependent rule, to pass against the original baseline.'
                        if support_baseline_id else 'Resolve necessary defects first; optional edits must pass both absolute S and normalized S_hat=S/D^3 checks, each with its N-dependent rule from support_evidence; never use volume or bounding-box dilution.')
                    if not feedback['orientation_only_edit']:
                        feedback['edit_restriction'] = ('Authorized local shape edits may reduce support; preserve nominal size, semantic ownership, root frame, appearance and interfaces.'
                            if shape_editable else 'Optional support edits preserve pre-connector body shape; necessary repairs retain evidence-based permissions.')
                    if mode_a:
                        feedback['edit_restriction'] += ' Mode A always requires one same-ID print part and identity print rotation, including necessary repairs.'
                    if book['completed']:
                        optimize=False
                feedback['actionable_finding_ids']=[f.finding_id for f in actionable]
                remaining = book['max_rounds']-number
                if ((not accepted or optimize) and (not nsv or not book['completed']) and not optimization_finished
                        and terminal_request_error is None and propagate_error is None
                        and not repairable_implementation_issue and reason != 'FLOW_ERROR' and actionable and remaining > 0):
                    budget = RepairController(workspace=workspace,round_root=root,baseline_source=current,
                        source_index=None,findings=[],checker_specs_sha256=config_hash,
                        policy=request.repair_policy.model_copy(update={'max_total_candidates':book['max_rounds']-1}))
                    budget.started_at=book['started_at']
                    if not budget.budget_error():
                        next_proposal=None
                        try:
                            next_proposal = await engineer(workflow,runtime,request,plan,current,execution,
                                candidate_root,evidence_runs if len(evidence_runs)>1 or not topology_run else topology_run,
                                _assembly_context(current,report,version_role='current_candidate',
                                    report_ref=reviews.get('geometry_report_ref')),
                                feedback,remaining)
                            if next_proposal:
                                regroup_precheck(next_proposal,purpose=feedback.get('next_edit_purpose'),
                                    continuing=feedback.get('partition_candidate_in_progress',False),
                                    assembly=_assembly_context(current,report,version_role='current_candidate')['current_assembly'],
                                    editable=request.repair_policy.print_partition_editable,
                                    **(dict(support_metric=support_metric,shape_editable=shape_editable) if nsv else {}))
                                feedback['engineering_proposal']=next_proposal.model_dump()
                                feedback['engineering']['status']='PROPOSAL'
                            elif feedback['engineering']['status']=='NOT_REQUESTED':
                                feedback['engineering']={'status':'NO_PROPOSAL',
                                    'reason':'No structured advice; use trustworthy current feedback or NO_CHANGE.'}
                        except InvalidPartitionProposal as error:
                            next_proposal = None
                            feedback['engineering']={'status':'UNAVAILABLE','code':'INVALID_PROPOSAL','reason':str(error)}
                            write_json(candidate_root/'proposal_rejected.json',feedback['engineering'])
                        except Exception as error:
                            write_json(candidate_root/'engineering_error.json',
                                {'type':type(error).__name__,'reason':safe_request_reason(error),
                                 'request_error':classify_model_request_error(error)})
                            classification = classify_model_request_error(error)
                            terminal_request_error = classification if terminal_stop_reason(classification) else None
                            if propagate_request_error(error, classification):
                                propagate_error = error
                            feedback['engineering']={'status':'UNAVAILABLE','reason':type(error).__name__,
                                'report_path':str((candidate_root/'engineering_error.json').resolve())}
                        if not terminal_request_error and propagate_error is None and partition is not None and feedback['next_edit_purpose'].endswith('_optimization') and not next_proposal:
                            partition_restore=feedback['comparison_baseline_version']
                            unavailable=feedback['engineering']['status']!='NO_PROPOSAL'
                            partition_stop_reason='partition_engineering_unavailable' if unavailable else 'no_reasonable_partition_proposal'
                            partition_mark_stopped=not unavailable
                        elif not terminal_request_error and propagate_error is None and partition is None and accepted and optimize and not next_proposal:
                            optimize=False;stop='no_reasonable_support_proposal' if nsv else 'no_reasonable_orientation_proposal';book['completed']=True
                    else:
                        stop='repair_budget_exhausted';book['completed']=True
                elif partition is not None and continuing_optimization and not optimization_finished and not actionable and not repairable_implementation_issue:
                    partition_restore=comparison_baseline
                    partition_stop_reason='partition_candidate_unverified_no_executable_feedback'
                elif (not accepted and not repairable_implementation_issue and reviews.get('appearance_approved') and not actionable
                      and not report.get('failures') and report.get('export_status') != 'FAIL'
                      and not (not visual_only and report['status']=='FAIL')):
                    stop=('topology_unverified_no_executable_feedback' if len(specs)==1 and topology_spec
                          else 'assembly_checks_unverified_no_executable_feedback');book['completed']=True
            if repairable_implementation_issue:
                feedback.pop('engineering_proposal', None)
                feedback.update(next_edit_purpose=edit_purpose, comparison_baseline_version=comparison_baseline,
                    partition_candidate_in_progress=bool(intent), partition_intent=intent, grouping_realization=realization)
                feedback['primary_objective'] = ('Restore mode A single-part identity print rotation; keep its baseline part ID. ' if mode_a and pose_check and pose_check['conclusion']!='ACCEPT' and not source_failure else '') + ('Fix the bound generated-source API/runtime error first. No appearance task without current render. ' if source_failure else 'Implement the authorized print-group declarations before proposing further physical changes. ') + 'Preserve partition_intent, independent bodies, edit purpose and comparison baseline; do not modify mesh backends.'
            # Export unavailability alone is not a reason to change shape. Keep
            # independent visual/topology defects repairable, in the same loop.
            image_pending = bool(reviews.get('appearance_approved') is False
                and feedback['resolved_visual_feedback']['required_changes']
                and not reviews.get('render_issue'))
            if (not accepted and not repairable_implementation_issue and reason != 'FLOW_ERROR' and failure_feedback
                    and not any(f['geometry_repair_allowed'] for f in failure_feedback)
                    and not actionable and not image_pending
                    and not any(f.get('code') in {'EXECUTION_UNAVAILABLE', SOURCE_FAILURE_CODE} for f in failure_feedback)):
                stop='export_unassessed_no_geometry_repair';book['completed']=True
            if nsv:
                active_support = feedback.get('support_baseline_version') or support_baseline_id
                if partition_restore and active_support:
                    support_restore = active_support
                    partition_restore = None
                if active_support and (terminal_request_error or propagate_error is not None or book['completed']):
                    support_restore = active_support
                if support_restore:
                    feedback = _restore_support_baseline(book,baseline_id=support_restore,
                        stop_reason=terminal_stop_reason(terminal_request_error) or partition_stop_reason or stop)
                    reference_path = book.get('partition_reference')
                    intent = None
                    support_baseline_id = comparison_baseline = None
                    partition_restore = None
                    optimize = False
                    book['completed'] = propagate_error is None
                    stop = terminal_stop_reason(terminal_request_error) or partition_stop_reason or stop
            if partition_restore is not None and not terminal_request_error and propagate_error is None:
                book['active_partition_intent'] = None
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
            if terminal_request_error or propagate_error is not None:
                classification = terminal_request_error or classify_model_request_error(propagate_error)
                feedback['request_error'] = classification
                book.update(feedback=feedback, next_round=number+1,
                    terminal_request_error=classification,
                    stop_reason=terminal_stop_reason(classification) or 'agent_call_error',
                    completed=propagate_error is None)
                write_json(book_path, book)
                if propagate_error is not None:
                    raise propagate_error
                stop = book['stop_reason']
                break
            book.update(feedback=feedback, next_round=number+1)
            write_json(book_path, book)
            if accepted and not optimize or reason == 'FLOW_ERROR':
                if not book['completed'] or reason == 'FLOW_ERROR':
                    stop = reason
                break
    except BaseException:
        active = book.get('feedback',{}).get('support_baseline_version') if nsv else None
        if active:
            _restore_support_baseline(book,baseline_id=active,stop_reason='support_candidate_interrupted')
            write_json(book_path,book)
        raise
    if nsv and book.get('feedback',{}).get('support_baseline_version'):
        feedback = _restore_support_baseline(book,baseline_id=book['feedback']['support_baseline_version'],stop_reason=stop)
        reference_path = book.get('partition_reference')
    # An unfinished optional implementation never replaces its retained baseline.
    if book.get('active_partition_intent'):
        pending_intent = book['active_partition_intent']
        baseline_id = pending_intent['comparison_baseline_version']
        baseline = book['versions'][baseline_id]
        book['working'] = baseline_id
        if not book['qualified'] or baseline['reviews'].get('accepted'):
            book['retained'] = baseline_id
        book['partition_implementation_result'] = dict(status='NOT_COMPLETED',
            reason=stop, partition_intent=pending_intent)
        book['active_partition_intent'] = None
        book['feedback'] = {**book.get('partition_baseline_feedback',{}).get(baseline_id,{}),
            'source_version':baseline_id, 'source_sha256':file_hash(Path(baseline['source'])),
            'next_edit_purpose':'required_repair', 'comparison_baseline_version':None,
            'partition_stop_reason':stop}
    book.update(completed=True, stop_reason=stop)
    write_json(book_path, book)
    write_json(workspace/'working_candidate.json', {'version_id':book['working'],
        'source':book['versions'][book['working']]['source'], 'qualified_version':book['qualified'],
        'approved':bool(book['versions'][book['working']]['reviews'].get('accepted')),
        'note':'Working candidate may be invalid; not a certified final assembly'})
    selected = book['versions'][book['retained']]
    assert_version(selected)
    if mode_a:
        initial = next((v for v in book['versions'].values() if
            (v['reviews'].get('mode_a_pose_check') or {}).get('conclusion')=='ACCEPT'),book['versions'][book['original']])
        final_pose = _mode_a_pose(initial['reviews'].get('geometry',{}),selected['reviews'].get('geometry',{}),
            baseline_hash=file_hash(Path(initial['source'])),candidate_hash=file_hash(Path(selected['source'])))
        if final_pose['conclusion'] != 'ACCEPT':
            selected['reviews']['accepted'] = False
            selected['reviews']['mode_a_pose_check'] = final_pose
            write_json(book_path,book)
    shutil.copy2(selected['source'], source_path)
    glb, urdf, renders = None, None, []
    if selected['execution']:
        _, execution, _ = version_assets(selected)
        if str(execution.glb_path) in selected['files'] and execution.glb_path.is_file():
            glb, urdf, renders = workflow._publish(workspace, execution)
        else:
            # A complete manufacturing bundle can lack a display artifact.
            # Publish its print/checker inputs without borrowing an old render.
            (workspace/'scene.glb').unlink(missing_ok=True)
            if (workspace/'render').exists():
                shutil.rmtree(workspace/'render')
            if execution.source_index_path and execution.source_index_path.is_file():
                shutil.copy2(execution.source_index_path, workspace/'source_index.json')
            else:
                (workspace/'source_index.json').unlink(missing_ok=True)
        # Publish exactly this version: a missing display or merged print part
        # must not leave an artifact from a previously selected assembly.
        if (workspace/'assembly').exists():
            shutil.rmtree(workspace/'assembly')
        shutil.copytree(execution.output_root/'assembly', workspace/'assembly')
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
        'manufacturing_status':selected['reviews'].get('geometry',{}).get('manufacturing_status'),
        'display_status':selected['reviews'].get('geometry',{}).get('display_status'),
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
