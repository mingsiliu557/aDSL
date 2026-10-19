"""Thin internal Review/Edit entrypoints; the existing controller owns policy.

No model tools, result DTOs, retries or evidence records are introduced here.
Native/checker adapters are imported only when their entrypoint is used.
"""
from __future__ import annotations

from typing import Any, Literal


async def review_image(
    workflow, *, runtime, request, plan, execution,
    round_number, max_rounds, round_root, image_critic,
    image_history, code_critic_corrections, render_issue=None,
    assembly_context=None, source_path=None, source_version=None,
):
    return await workflow._review_generation_image(
        runtime=runtime, request=request, plan=plan, execution=execution,
        round_number=round_number, max_rounds=max_rounds, round_root=round_root,
        image_critic=image_critic, image_history=image_history,
        code_critic_corrections=code_critic_corrections, render_issue=render_issue,
        assembly_context=assembly_context, source_path=source_path,
        source_version=source_version,
    )


async def review_code(
    workflow, *, runtime, request, plan, execution,
    workspace, source_path, round_number, max_rounds,
    round_root, code_critic, image_decision, code_history,
    render_issue=None, assembly_context=None,
):
    return await workflow._review_generation_code(
        runtime=runtime, request=request, plan=plan, execution=execution,
        workspace=workspace, source_path=source_path, round_number=round_number,
        max_rounds=max_rounds, round_root=round_root, code_critic=code_critic,
        image_decision=image_decision, code_history=code_history,
        render_issue=render_issue, assembly_context=assembly_context,
    )


_DOMAIN_SPECS = {
    'topology': 'assembly_topology',
    'standing': 'assembly_standing',
    'overhang': 'assembly_overhang',
    'fea': 'assembly_fea',
}


def review_physics(
    specs, *, execution, source, root, physics,
    domains: tuple[Literal['topology', 'standing', 'overhang', 'fea'], ...] | None = None,
    initial_topology_run=None, partition_reference=None,
):
    from .assembly_physics import run_assembly_checks

    selected = specs
    if domains is not None:
        for domain in domains:
            if domain not in _DOMAIN_SPECS:
                raise ValueError(f'unknown review domain: {domain}')
        allowed = {_DOMAIN_SPECS[domain] for domain in domains}
        selected = []
        for spec in specs:
            if spec.name not in _DOMAIN_SPECS.values():
                raise ValueError(f'unknown assembly checker: {spec.name}')
            if spec.name in allowed:
                selected.append(spec)
    return run_assembly_checks(
        selected, execution=execution, source=source, root=root, physics=physics,
        initial_topology_run=initial_topology_run, partition_reference=partition_reference,
    )


async def edit(
    workflow, *, runtime, repairer, workspace, source_path,
    role, stage, payload, reserved_attempt_id=None, allow_no_change=False,
) -> dict[str, Any] | None:
    return await workflow._repair(
        runtime=runtime, repairer=repairer, workspace=workspace,
        source_path=source_path, role=role, stage=stage, payload=payload,
        reserved_attempt_id=reserved_attempt_id, allow_no_change=allow_no_change,
    )


class InvalidPartitionProposal(ValueError):
    pass


def regroup_precheck(proposal, *, purpose, continuing=False, assembly=None, editable=False,
                     support_metric='area', shape_editable=False):
    if purpose != 'partition_optimization' or continuing:
        return
    if (support_metric == 'nsv' and shape_editable and proposal.grouping_change is None
            and proposal.action in {'reshape', 'resize', 'add_local_structure'}):
        return
    if not editable or proposal.action != 'regroup_print_parts' or proposal.grouping_change is None:
        raise InvalidPartitionProposal('INVALID_PROPOSAL: first partition optimization requires authorized regroup_print_parts and grouping_change')
    try:
        proposal.grouping_change.validate_current_assembly(assembly or {})
    except ValueError as error:
        raise InvalidPartitionProposal(str(error)) from error


def reorient_postcheck(before_text: str, after_text: str) -> bool:
    from .assembly_physics import orientation_only

    return orientation_only(before_text, after_text)


def regroup_realization(intent, baseline_assembly, current_assembly, *, source_sha256):
    """Compare bound declarations only; MATCH is not a geometric verdict."""
    if intent is None:
        return None
    result = dict(schema_version=1, status='UNAVAILABLE', proposal_id=intent['proposal_id'],
        source_sha256=source_sha256, missing_part_ids=[], unexpected_part_ids=[],
        component_mismatches=[], unaffected_part_mismatches=[],
        reason='Bound baseline or current declarations unavailable')
    if not baseline_assembly or not current_assembly:
        return result
    change = intent['grouping_change']
    from .models import PrintGroupingChange
    try:
        PrintGroupingChange.model_validate(change).validate_current_assembly(baseline_assembly)
        original, actual = _group_mapping(baseline_assembly), _group_mapping(current_assembly)
        expected = {k:v for k,v in original.items() if k not in change['source_part_ids']}
        unaffected = set(expected)
        expected.update({p['id']:set(p['components']) for p in change['target_print_parts']})
    except (KeyError, TypeError, ValueError):
        return result
    result['missing_part_ids'] = sorted(expected.keys() - actual.keys())
    result['unexpected_part_ids'] = sorted(actual.keys() - expected.keys())
    for name in sorted(expected.keys() & actual.keys()):
        if expected[name] != actual[name]:
            result['component_mismatches'].append(dict(part_id=name,
                expected=sorted(expected[name]), actual=sorted(actual[name])))
    result['unaffected_part_mismatches'] = sorted(name for name in unaffected
        if name not in actual or actual[name] != expected[name])
    mismatch = any(result[k] for k in ('missing_part_ids','unexpected_part_ids',
                                      'component_mismatches','unaffected_part_mismatches'))
    result.update(status='MISMATCH' if mismatch else 'MATCH', reason=(
        'The declared target print groups have not been implemented' if mismatch else
        'Declared groups match; material separation and interfaces require geometric checks'))
    return result


def _group_mapping(assembly):
    parts = assembly['parts']
    if not parts or len({p['id'] for p in parts}) != len(parts):
        raise ValueError('missing or duplicate part declarations')
    return {p['id']:set(p['components']) for p in parts}


def support_postcheck(baseline, candidate, *, tolerance_mm3):
    """Local S/N acceptance only; never overrides necessary feasibility gates."""
    from .support_volume import _nonnegative
    if not _nonnegative(tolerance_mm3):
        raise ValueError('tolerance_mm3 must be finite and nonnegative')
    result = dict(conclusion='REJECT', rule='support_unavailable',
        delta_support_mm3=None, delta_part_count=None, tolerance_mm3=tolerance_mm3)
    for value in (baseline, candidate):
        if not isinstance(value, dict):
            return result
        support = value.get('support') or {}
        count = value.get('part_count')
        if (support.get('status') != 'PASS' or type(count) is not int or count <= 0 or
                not _nonnegative(support.get('total_support_volume_mm3')) or
                not _nonnegative(support.get('total_volume_mm3')) or support['total_volume_mm3'] <= 0 or
                not _nonnegative(support.get('total_risky_projected_area_mm2')) or
                not _nonnegative(support.get('nsv_total'))):
            return result
    ds = candidate['support']['total_support_volume_mm3'] - baseline['support']['total_support_volume_mm3']
    dn = candidate['part_count'] - baseline['part_count']
    if dn < 0:
        accepted = ds <= tolerance_mm3
        rule = 'merge_acceptable_support' if accepted else 'merge_worsens_support'
    else:
        accepted = ds < -tolerance_mm3
        rule = ('split_support_gain' if accepted else 'split_without_support_gain') if dn else (
            'support_gain' if accepted else 'no_support_gain')
    return dict(result, conclusion='ACCEPT' if accepted else 'REJECT', rule=rule,
                delta_support_mm3=ds, delta_part_count=dn)


def print_pose_postcheck(baseline_parts, candidate_parts):
    """Mode A: one identified piece, upright rotation; bed translation may vary."""
    import numpy as np
    result = dict(conclusion='REJECT', rule='print_pose_unavailable',
                  baseline_transform_mm=None, candidate_transform_mm=None,
                  rotation_rtol=0, rotation_atol=1e-9)
    if not isinstance(baseline_parts, list) or not isinstance(candidate_parts, list):
        return result
    if len(baseline_parts) != 1 or len(candidate_parts) != 1:
        return dict(result, rule='part_count_changed')
    try:
        a, b = baseline_parts[0], candidate_parts[0]
        if not a.get('id') or not b.get('id'):
            return result
        if a['id'] != b['id']:
            return dict(result, rule='part_identity_changed')
        matrices = [np.asarray(p['print_transform_mm'], dtype=float) for p in (a, b)]
        for m in matrices:
            if m.shape != (4, 4) or not np.isfinite(m).all():
                return result
            r = m[:3, :3]
            if (not np.allclose(r.T @ r, np.eye(3), rtol=0, atol=1e-9) or
                    not np.isclose(np.linalg.det(r), 1, rtol=0, atol=1e-9)):
                return result
        result.update(baseline_transform_mm=matrices[0].tolist(), candidate_transform_mm=matrices[1].tolist())
        if not all(np.allclose(m[:3, :3], np.eye(3), rtol=0, atol=1e-9) for m in matrices):
            return dict(result, rule='print_pose_changed')
    except (KeyError, TypeError, ValueError):
        return result
    return dict(result, conclusion='ACCEPT', rule='print_pose_preserved')
