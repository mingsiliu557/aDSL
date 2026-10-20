"""Fixed-denominator physical reporting. Never rewrites checker verdicts."""
from copy import deepcopy
import math


STANDING_PROTOCOL = 'independent_contact_warning_v1'
_REASONS = {
    'GENERATED_SOURCE_EXECUTION_FAILED': 'GENERATION_FAILED',
    'GENERATION_FAILED': 'GENERATION_FAILED',
    'INVALID_PHYSICAL_INPUT': 'INVALID_INPUT',
    'DEPENDENCY_MESH_UNAVAILABLE': 'INVALID_INPUT',
    'PRINT_MESH_UNMEASURABLE': 'INVALID_INPUT',
    'RIGID_CONTACT_INTERFERENCE_UNSUPPORTED': 'UNSUPPORTED_INPUT',
    'RIGID_FLEX_CONTACT_UNVERIFIED': 'UNSUPPORTED_INPUT',
    'COLLISION_PROXY_UNVERIFIED': 'UNSUPPORTED_INPUT',
    'DEPENDENCY_UNAVAILABLE': 'UNSUPPORTED_INPUT',
    'NEEDS_SPEC': 'INVALID_INPUT',
    'SIMULATION_UNVERIFIED': 'SIMULATION_ERROR',
    'CHECKER_INFRASTRUCTURE_ERROR': 'SIMULATION_ERROR',
    'CHECKER_TIMEOUT': 'TIMEOUT',
    'TIMEOUT': 'TIMEOUT',
    'OBSERVATION_UNAVAILABLE': 'OBSERVATION_UNAVAILABLE',
    'SELF_WEIGHT_TIPPING': 'TIPPING',
    'SELF_WEIGHT_INTERFACE_EXIT': 'INTERFACE_EXIT',
}


def _number(value, *, positive=False):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and (value > 0 if positive else value >= 0))


def classify_standing_record(record, *, protocol_id):
    """record: case_id, applicable, state, protocol_id, checker, source/asset hashes.

    state is PENDING/RUNNING/TERMINAL, separate from the raw checker status.
    Only TERMINAL records enter the failure count. A protocol mismatch is an
    explicit comparison error, never an opportunity to reinterpret old results.
    """
    if not record.get('case_id') or type(record.get('applicable')) is not bool:
        raise ValueError('case_id and frozen boolean applicability are required')
    if record.get('protocol_id') != protocol_id:
        raise ValueError('standing protocol mismatch')
    state = record.get('state')
    if state not in {'PENDING', 'RUNNING', 'TERMINAL'}:
        raise ValueError('explicit evaluation state is required')
    checker = record.get('checker') or {}
    declared = checker.get('assumptions', {}).get('standing_protocol')
    if checker and declared != protocol_id:
        raise ValueError('checker standing protocol missing or mismatched')
    bound_source = checker.get('assumptions', {}).get('source_sha256')
    if bound_source and record.get('source_sha256') and bound_source != record['source_sha256']:
        raise ValueError('standing source binding mismatch')
    observation = checker.get('metrics', {}).get('standing_observation')
    out = dict(case_id=record['case_id'], applicable=record['applicable'],
        evaluation_outcome=None, reason_codes=[], raw_status=checker.get('status'),
        observation=deepcopy(observation), source_sha256=record.get('source_sha256'),
        asset_sha256=record.get('asset_sha256'), protocol_id=protocol_id)
    if not record['applicable']:
        out['reason_codes'] = ['NOT_APPLICABLE']
        return out
    if state != 'TERMINAL':
        out.update(evaluation_outcome='PENDING', reason_codes=['NOT_FINISHED'])
        return out
    reasons = []
    # Reporting termination reasons do not become geometric findings.
    reasons.extend(r for r in record.get('termination_reason_codes', []) if r in {
        'GENERATION_FAILED', 'INVALID_INPUT', 'UNSUPPORTED_INPUT', 'SIMULATION_ERROR',
        'TIMEOUT', 'OBSERVATION_UNAVAILABLE', 'UNKNOWN'})
    if record.get('generation_status', '').upper() in {
            'FAIL', 'FAILED', 'ERROR', 'GENERATION_FAILED', 'TIMEOUT', 'API_INTERRUPTED'}:
        reasons.append('GENERATION_FAILED')
    for row in [checker, *checker.get('violations', []), *checker.get('findings', [])]:
        for key in ('code', 'rule_id', 'summary', 'reason'):
            text = row.get(key)
            if isinstance(text, str):
                # Explicit producer prefixes only, not arbitrary prose inference.
                code = text.split(':', 1)[0]
                if code in _REASONS:
                    reasons.append(_REASONS[code])
                if code == 'ANALYSIS_UNAVAILABLE' and ':' in text:
                    inner = text.split(':', 1)[1].strip().split(':', 1)[0]
                    if inner in _REASONS:
                        reasons.append(_REASONS[inner])
    obs = observation or {}
    complete = (obs.get('status') in {'PASS', 'FAIL'}
        and type(obs.get('observed_tipping')) is bool
        and type(obs.get('observed_interface_exit')) is bool
        and all(_number(obs.get(k)) for k in ('peak_tilt_deg', 'final_tilt_deg'))
        and _number(obs.get('duration_s'), positive=True))
    if complete:
        if obs['observed_tipping']:
            reasons.append('TIPPING')
        if obs['observed_interface_exit']:
            reasons.append('INTERFACE_EXIT')
    if checker.get('status') == 'PASS' and complete and obs['status'] == 'PASS' and not reasons:
        out['evaluation_outcome'] = 'PASS'
    else:
        if not complete:
            reasons.append('OBSERVATION_UNAVAILABLE')
        if checker.get('status') == 'ERROR' and not any(r in reasons for r in (
                'TIMEOUT', 'INVALID_INPUT', 'UNSUPPORTED_INPUT', 'SIMULATION_ERROR')):
            reasons.append('UNKNOWN')
        out.update(evaluation_outcome='FAIL', reason_codes=list(dict.fromkeys(reasons or ['UNKNOWN'])))
    return out


def summarize_standing(records, *, protocol_id):
    rows = [classify_standing_record(r, protocol_id=protocol_id) for r in records]
    if len({r['case_id'] for r in rows}) != len(rows):
        raise ValueError('duplicate case in frozen method sample')
    selected = [r for r in rows if r['applicable']]
    success = sum(r['evaluation_outcome'] == 'PASS' for r in selected)
    failed = sum(r['evaluation_outcome'] == 'FAIL' for r in selected)
    pending = sum(r['evaluation_outcome'] == 'PENDING' for r in selected)
    return dict(protocol_id=protocol_id, status='INCOMPLETE' if pending else 'COMPLETE',
        planned_total=len(selected), success_count=success, terminal_failure_count=failed,
        pending_count=pending, success_rate=success/len(selected) if selected and not pending else None,
        observed_tipping_count=sum('TIPPING' in r['reason_codes'] for r in selected),
        observed_interface_exit_count=sum('INTERFACE_EXIT' in r['reason_codes'] for r in selected),
        cases=rows)


def raw_ivr(part_volumes, pair_intersections):
    """Final connector-inclusive volumes; all unordered pairs, no fit subtraction."""
    if not isinstance(part_volumes, dict) or not part_volumes:
        return dict(value=None, reason='PART_VOLUMES_UNAVAILABLE')
    if len(part_volumes) == 1:
        return dict(value=None, reason='SINGLE_PART_NOT_APPLICABLE')
    if not all(_number(v, positive=True) for v in part_volumes.values()):
        return dict(value=None, reason='INVALID_PART_VOLUME')
    expected = {frozenset((a, b)) for a in part_volumes for b in part_volumes if a != b}
    seen = set()
    total = 0.
    for row in pair_intersections or ():
        pair = frozenset(row.get('part_ids', ()))
        value = row.get('raw_intersection_mm3')
        # Preserve raw evidence, interpreting only an already bounded negative
        # round-off as zero. No new Topology tolerance or fit subtraction.
        bound = row.get('volume_tolerance_mm3')
        if (isinstance(value, (int, float)) and not isinstance(value, bool) and value < 0
                and _number(bound) and -value <= bound):
            value = 0.
        if pair not in expected or pair in seen or not _number(value):
            return dict(value=None, reason='INVALID_PAIR_MEASUREMENT')
        seen.add(pair)
        total += value
    if seen != expected:
        return dict(value=None, reason='INCOMPLETE_PAIR_MEASUREMENT')
    return dict(value=total/sum(part_volumes.values()), reason=None)


def physical_table_row(record, *, protocol_id):
    """Consume saved S/V and intersection measurements; never run or alter them."""
    standing = classify_standing_record(record, protocol_id=protocol_id)
    ivr = raw_ivr(record.get('part_volumes_mm3'), record.get('pair_intersections'))
    support = record.get('support') or {}
    nsv = support.get('nsv_total')
    valid_support = (support.get('status') == 'PASS' and _number(nsv)
        and _number(support.get('total_volume_mm3'), positive=True)
        and _number(support.get('total_support_volume_mm3')))
    count = record.get('part_count')
    count = count if type(count) is int and count > 0 else None
    return dict(case_id=record['case_id'], standing=standing,
        raw_ivr=ivr['value'], nsv=nsv if valid_support else None, N=count,
        missing_reasons=dict(raw_ivr=ivr['reason'],
            nsv=None if valid_support else 'SUPPORT_UNAVAILABLE',
            N=None if count else 'PART_COUNT_UNAVAILABLE'))


def summarize_physical_methods(method_records, *, protocol_id):
    """Same frozen case/applicability set for every method; no result-based pruning.

    Standing uses all applicable cases. IVR/NSV/N means use explicitly recorded
    common measurable sets, separately per metric. NSV also requires matching
    measurement configuration within each paired case.
    """
    indexed, details = {}, {}
    expected = None
    for method, records in method_records.items():
        rows = list(records)
        keys = {r['case_id']:r['applicable'] for r in rows}
        if len(keys) != len(rows) or (expected is not None and keys != expected):
            raise ValueError('methods must share the frozen case and applicability list')
        expected = keys
        indexed[method] = {r['case_id']:r for r in rows}
        details[method] = {r['case_id']:physical_table_row(r, protocol_id=protocol_id) for r in rows}
    common = {}
    for metric in ('raw_ivr', 'nsv', 'N'):
        common[metric] = [cid for cid in (expected or {}) if all(
            indexed[m][cid]['state'] == 'TERMINAL' and details[m][cid][metric] is not None
            for m in indexed)]
    mismatched = []
    for cid in list(common['nsv']):
        configs = {indexed[m][cid].get('support_measurement_config_sha256') for m in indexed}
        if None in configs or len(configs) != 1:
            common['nsv'].remove(cid)
            mismatched.append(cid)
    table, samples = [], {}
    for method, records in indexed.items():
        standing = summarize_standing(records.values(), protocol_id=protocol_id)
        means = {metric:sum(details[method][cid][metric] for cid in ids)/len(ids) if ids else None
                 for metric, ids in common.items()}
        table.append(dict(method=method, standing_success_rate=standing['success_rate'],
            raw_ivr=means['raw_ivr'], nsv=means['nsv'], N=means['N']))
        samples[method] = dict(standing=standing, metrics=list(details[method].values()),
            measured_counts={key:sum(row[key] is not None for row in details[method].values())
                             for key in common})
    return dict(status='INCOMPLETE' if any(
        s['standing']['pending_count'] or any(r['state'] != 'TERMINAL' for r in indexed[m].values())
        for m, s in samples.items()) else 'COMPLETE',
        protocol_id=protocol_id, table=table, common_measurable_case_ids=common,
        nsv_configuration_mismatch_case_ids=mismatched, samples=samples)
