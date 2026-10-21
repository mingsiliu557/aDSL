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


def _measurement(record, key, expected_protocol):
    value = record.get(key)
    if value is None:
        return None
    if value.get('protocol_id') != expected_protocol:
        raise ValueError(f'{key} protocol mismatch')
    if value.get('status') not in {'PASS', 'UNAVAILABLE'}:
        raise ValueError(f'{key} invalid measurement status')
    if value.get('asset_sha256') != record.get('asset_sha256'):
        raise ValueError(f'{key} asset binding mismatch')
    if value['status'] == 'PASS' and (not value.get('asset_sha256') or not value.get('measurement_config_sha256')):
        raise ValueError(f'{key} measured identity/config missing')
    return value


def _connectivity_record(record):
    value = _measurement(record, 'connectivity', 'print_plan_structural_connectivity_v1')
    applicable = record.get('connectivity_applicable', True)
    if type(applicable) is not bool:
        raise ValueError('frozen connectivity applicability must be boolean')
    out = dict(case_id=record['case_id'], applicable=applicable, evaluation_outcome=None,
        reason_codes=[], internal_connected=None, interpart_connected=None, status='UNAVAILABLE')
    if value:
        out.update({k:deepcopy(value.get(k)) for k in ('status','internal_connected','interpart_connected','reason_codes')})
        if value['status'] == 'PASS':
            a, b, overall = (value.get(k) for k in ('internal_connected','interpart_connected','overall_pass'))
            if any(type(x) is not bool for x in (a,b,overall)) or overall != (a and b):
                raise ValueError('inconsistent connectivity success evidence')
            reasons = ([] if a else ['INTERNAL_PART_DISCONNECTED']) + ([] if b else ['INTERPART_GRAPH_DISCONNECTED'])
            if set(value.get('reason_codes', [])) != set(reasons):
                raise ValueError('inconsistent connectivity reason codes')
        elif value.get('overall_pass') is not None:
            raise ValueError('unavailable connectivity cannot declare a verdict')
    if not applicable:
        out.update(reason_codes=['NOT_APPLICABLE'])
    elif record['state'] != 'TERMINAL':
        out.update(evaluation_outcome='PENDING', reason_codes=['NOT_FINISHED'])
    elif value and value['status']=='PASS':
        out['evaluation_outcome'] = 'PASS' if value['overall_pass'] else 'FAIL'
    else:
        out.update(evaluation_outcome='FAIL', reason_codes=(out['reason_codes'] or ['CONNECTIVITY_NOT_MEASURED']))
    return out


def physical_table_row(record, *, protocol_id):
    """Read protocol/asset-bound offline metrics; old support is not NSV24."""
    standing = classify_standing_record(record, protocol_id=protocol_id)
    connectivity = _connectivity_record(record)
    support = _measurement(record, 'support_evaluation', 'support_volume_min24_v1')
    count = record.get('part_count')
    count = count if type(count) is int and count > 0 else None
    nsv = None
    if support and support['status']=='PASS':
        s,v,n = (support.get(k) for k in ('total_support_volume_mm3','total_volume_mm3','nsv_total'))
        if (not _number(s) or not _number(v,positive=True) or not _number(n)
                or not math.isclose(n,s/v,rel_tol=1e-12,abs_tol=0.)
                or support.get('orientation_set')!='axis_aligned_24'
                or support.get('orientation_objective')!='support_volume_mm3'
                or count is None or type(support.get('part_count')) is not int or support.get('part_count') != count):
            raise ValueError('invalid support24 measurement/count')
        nsv = n
    measured_connectivity = record.get('connectivity') or {}
    if measured_connectivity.get('status')=='PASS' and (count is None or
            type(measured_connectivity.get('part_count')) is not int or measured_connectivity.get('part_count') != count):
        raise ValueError('connectivity print-part count mismatch')
    ivr = raw_ivr(record.get('part_volumes_mm3'),record.get('pair_intersections'))
    return dict(case_id=record['case_id'], standing=standing, connectivity=connectivity,
        raw_ivr=ivr['value'], nsv=nsv, N=count,
        missing_reasons=dict(raw_ivr=ivr['reason'], nsv=None if nsv is not None else
            'SUPPORT24_NOT_MEASURED' if support is None else 'SUPPORT24_UNAVAILABLE',
            N=None if count else 'PART_COUNT_UNAVAILABLE'))


def summarize_physical_methods(method_records, *, protocol_id):
    """Fixed success denominators, and one common NSV24/N measurement subset."""
    indexed, details, expected = {}, {}, None
    for method, records in method_records.items():
        rows = list(records)
        keys = {r['case_id']:(r['applicable'],r.get('connectivity_applicable',True)) for r in rows}
        if len(keys)!=len(rows) or (expected is not None and keys!=expected):
            raise ValueError('methods must share the frozen case and applicability list')
        expected = keys
        indexed[method] = {r['case_id']:r for r in rows}
        details[method] = {r['case_id']:physical_table_row(r,protocol_id=protocol_id) for r in rows}
    # A protocol/configuration mismatch is an error, not result-based pruning.
    for key in ('connectivity','support_evaluation'):
        configs = {r[key]['measurement_config_sha256'] for records in indexed.values() for r in records.values()
                   if (r.get(key) or {}).get('status')=='PASS'}
        if len(configs)>1:
            raise ValueError(f'{key} measurement configuration mismatch')
    common = [cid for cid in (expected or {}) if all(
        records[cid]['state']=='TERMINAL' and details[m][cid]['nsv'] is not None
        and details[m][cid]['N'] is not None for m,records in indexed.items())]
    table, samples = [], {}
    for method, records in indexed.items():
        standing = summarize_standing(records.values(),protocol_id=protocol_id)
        connectivity_rows = [row['connectivity'] for row in details[method].values()]
        active = [r for r in connectivity_rows if r['applicable']]
        terminal = [r for r in active if r['evaluation_outcome']!='PENDING']
        pending = len(active)-len(terminal)
        success = sum(r['evaluation_outcome']=='PASS' for r in terminal)
        connectivity = dict(planned_total=len(active), success_count=success,
            terminal_failure_count=len(terminal)-success, pending_count=pending,
            success_rate=success/len(active) if active and not pending else None,
            internal_disconnected_count=sum(r['internal_connected'] is False for r in terminal),
            interpart_disconnected_count=sum(r['interpart_connected'] is False for r in terminal),
            both_failure_count=sum(r['internal_connected'] is False and r['interpart_connected'] is False for r in terminal),
            unavailable_count=sum(r['status']=='UNAVAILABLE' for r in terminal), cases=connectivity_rows)
        means = {key:sum(details[method][cid][key] for cid in common)/len(common) if common else None for key in ('nsv','N')}
        table.append(dict(method=method, standing_success_rate=standing['success_rate'],
            structural_connectivity_rate=connectivity['success_rate'], **means))
        samples[method] = dict(standing=standing, connectivity=connectivity,
            metrics=list(details[method].values()), metric_case_ids={'nsv':list(common),'N':list(common)},
            measured_counts={key:sum(row[key] is not None for row in details[method].values()) for key in ('nsv','N')})
    return dict(status='INCOMPLETE' if any(r['state']!='TERMINAL' for records in indexed.values() for r in records.values()) else 'COMPLETE',
        protocol_id=protocol_id, table=table, common_measurable_case_ids={'nsv':common,'N':list(common)}, samples=samples)
