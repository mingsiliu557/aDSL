"""Measure explicit frozen records offline; never discover or execute source."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import json
import math
from pathlib import Path
import shutil

from benchmark.metrics.geometry import (load_evaluation_parts, assembly_bounds,
    measure_connectivity, measure_support24, file_hash, digest, CONNECTIVITY_PROTOCOL,
    SUPPORT_PROTOCOL, CONTACT_AREA_CONFIG)
from benchmark.metrics.physical import (summarize_physical_methods, STANDING_PROTOCOL,
    _connectivity_record, _summarize_connectivity_rows)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def write_csv(path, rows, fields):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)


def _read_evidence(ref, base, cache):
    """Read a hash-bound JSON pointer; paths are relative to the frozen records."""
    if (not isinstance(ref, dict) or not isinstance(ref.get('path'), str)
            or not ref.get('file_sha256') or not isinstance(ref.get('json_pointer'), str)):
        raise ValueError('missing frozen evidence path/hash/JSON pointer')
    path = (base / ref['path']).resolve()
    key = (path, ref['file_sha256'])
    if key not in cache:
        if file_hash(path) != ref['file_sha256']:
            raise ValueError('frozen evidence file hash mismatch')
        cache[key] = json.loads(path.read_text())
    value = cache[key]
    pointer = ref['json_pointer']
    if pointer:
        if not pointer.startswith('/'):
            raise ValueError('invalid frozen evidence JSON pointer')
        try:
            for token in pointer[1:].split('/'):
                # Reject malformed escapes, rather than silently interpreting
                # a pointer different from the producer's recorded pointer.
                for suffix in token.split('~')[1:]:
                    if not suffix or suffix[0] not in '01':
                        raise ValueError('invalid JSON pointer escape')
                token = token.replace('~1', '/').replace('~0', '~')
                if isinstance(value, list):
                    if not token.isdigit() or (len(token)>1 and token.startswith('0')):
                        raise ValueError('invalid JSON pointer array index')
                    value = value[int(token)]
                else:
                    value = value[token]
        except (KeyError, IndexError, TypeError, ValueError) as error:
            raise ValueError('frozen evidence JSON pointer is unavailable') from error
    return value, path


def _brief_measurement(value):
    return {k:v for k,v in value.items() if k not in ('details','measurement_config','evidence_ref')}


def _validate_reuse(original, config, base):
    """Validate archived results, never reconstruct a missing raw checker."""
    cache = {}
    summary, _ = _read_evidence(original.get('frozen_summary_ref'), base, cache)
    methods = original['methods']
    if (summary.get('status') != 'COMPLETE' or summary.get('protocol_id') != STANDING_PROTOCOL
            or list(methods) != list(summary.get('samples', {}))
            or list(methods) != [r['method'] for r in summary.get('table', [])]):
        raise ValueError('completed frozen summary methods/protocol mismatch')
    expected_ids, support_configs = None, set()
    for method, records in methods.items():
        sample = summary['samples'][method]
        ids = [r['case_id'] for r in records]
        if (len(set(ids)) != len(ids) or (expected_ids is not None and ids != expected_ids)
                or ids != [r['case_id'] for r in sample['metrics']]
                or ids != [r['case_id'] for r in sample['standing']['cases']]
                or ids != [r['case_id'] for r in sample['connectivity']['cases']]):
            raise ValueError('frozen method/case order mismatch')
        expected_ids = ids
        for record, metric, standing in zip(records, sample['metrics'], sample['standing']['cases']):
            asset, source = record.get('asset_sha256'), record.get('source_sha256')
            if (record.get('state') != 'TERMINAL' or record.get('protocol_id') != STANDING_PROTOCOL
                    or record.get('method',method) != method or not asset or not source
                    or type(record.get('applicable')) is not bool
                    or record['applicable'] != standing.get('applicable')
                    or record.get('connectivity_applicable',True) != metric['connectivity']['applicable']
                    or asset != standing.get('asset_sha256') or source != standing.get('source_sha256')
                    or standing.get('protocol_id') != STANDING_PROTOCOL
                    or record.get('standing_evaluation') != standing or metric['standing'] != standing
                    or (record['applicable'] and standing.get('evaluation_outcome') not in ('PASS','FAIL'))):
                raise ValueError('frozen standing/case/source/asset identity mismatch')
            standing_ref = record.get('standing_evidence_ref')
            observed, _ = _read_evidence(standing_ref, base, cache)
            if observed != standing or standing_ref.get('asset_sha256') != asset:
                raise ValueError('frozen standing evidence mismatch')
            count, support = record.get('part_count'), record.get('support_evaluation')
            if type(count) is not int or count <= 0 or count != metric['N'] or not isinstance(support,dict):
                raise ValueError('frozen support/print-part count is unavailable or mismatched')
            if (support.get('protocol_id') != SUPPORT_PROTOCOL or support.get('asset_sha256') != asset
                    or support.get('part_count') != count
                    or support.get('status') not in ('PASS','UNAVAILABLE')
                    or support.get('orientation_set') != 'axis_aligned_24'
                    or support.get('orientation_objective') != 'support_volume_mm3'):
                raise ValueError('frozen support protocol/asset/count mismatch')
            evidence, _ = _read_evidence(support.get('evidence_ref'), base, cache)
            if (support['evidence_ref'].get('asset_sha256') != asset
                    or _brief_measurement(evidence) != _brief_measurement(support)):
                raise ValueError('frozen support evidence mismatch')
            measurement_config = evidence.get('measurement_config')
            if (not isinstance(measurement_config,dict)
                    or digest(measurement_config) != support.get('measurement_config_sha256')
                    or any(measurement_config.get(k) != config['support'][k] for k in
                        ('protocol_id','overhang_threshold_from_horizontal_deg','bed_tolerance_mm'))):
                raise ValueError('frozen support measurement configuration mismatch')
            support_configs.add(support['measurement_config_sha256'])
            if support['status']=='PASS':
                s,v,n = (support.get(k) for k in ('total_support_volume_mm3','total_volume_mm3','nsv_total'))
                if (any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x)
                        for x in (s,v,n)) or s<0 or v<=0 or n<0
                        or not math.isclose(n,s/v,rel_tol=1e-12,abs_tol=0.) or n != metric['nsv']):
                    raise ValueError('frozen support/NSV value mismatch')
            elif metric['nsv'] is not None:
                raise ValueError('unavailable frozen support cannot supply measured NSV')
            old = record.get('connectivity')
            if not isinstance(old,dict) or old.get('asset_sha256') != asset or old.get('part_count') != count:
                raise ValueError('previous connectivity asset/count mismatch')
            old_evidence, _ = _read_evidence(old.get('evidence_ref'), base, cache)
            if (old['evidence_ref'].get('asset_sha256') != asset
                    or _brief_measurement(old_evidence) != _brief_measurement(old)):
                raise ValueError('previous connectivity evidence mismatch')
    if len(support_configs)>1:
        raise ValueError('frozen support configuration mismatch across methods')
    deferred = original.get('deferred_connectivity', [])
    available = {(m,r['case_id']) for m,rows in methods.items() for r in rows}
    seen = set()
    for row in deferred:
        key = (row['method'],row['case_id'])
        if key not in available or key in seen or not isinstance(row.get('reason'),str) or not row['reason']:
            raise ValueError('invalid deferred connectivity case list')
        seen.add(key)
    return summary, cache


def _evaluate_connectivity_only(original, config, records_path, config_path, output):
    frozen, cache = _validate_reuse(original, config, records_path.parent)
    output.mkdir(parents=True, exist_ok=False)
    methods, summary = deepcopy(original['methods']), deepcopy(frozen)
    evidence_dir = output/'reused_evidence'; evidence_dir.mkdir()
    copied = {}
    def copy_ref(ref):
        _, old_path = _read_evidence(ref, records_path.parent, cache)
        new_path = evidence_dir/(ref['file_sha256']+'.json')
        if not new_path.exists():
            shutil.copyfile(old_path,new_path)
            if file_hash(new_path) != ref['file_sha256']:
                raise ValueError('copied frozen evidence file hash mismatch')
        copied[ref['file_sha256']] = str(new_path.relative_to(output))
        return dict(ref,path=str(new_path.relative_to(output)))
    summary_ref = copy_ref(original['frozen_summary_ref'])
    deferred = {(r['method'],r['case_id']):r for r in original.get('deferred_connectivity', [])}
    audit, configs = [], set()
    for method_index, (method, records) in enumerate(methods.items()):
        connectivity_rows = []
        for record_index, record in enumerate(records):
            record['standing_evidence_ref'] = copy_ref(record['standing_evidence_ref'])
            record['support_evaluation']['evidence_ref'] = copy_ref(record['support_evaluation']['evidence_ref'])
            previous = record.pop('connectivity')
            previous['evidence_ref'] = copy_ref(previous['evidence_ref'])
            record['previous_connectivity'] = previous
            for key in ('mesh_path','manifest_path','source_path'):
                if key in record['geometry_input']:
                    record['geometry_input'][key] = str((records_path.parent/record['geometry_input'][key]).resolve())
            loaded = load_evaluation_parts(record)
            if loaded['status'] != 'PASS':
                raise ValueError('frozen asset unavailable for connectivity reassessment: '+str(loaded.get('reason')))
            if loaded['asset_sha256'] != record['asset_sha256'] or len(loaded['parts']) != record['part_count']:
                raise ValueError('reassessment asset/print-part count mismatch')
            _, diagonal = assembly_bounds(loaded['parts'])
            delta = max(config['connectivity']['delta_ratio']*diagonal,config['connectivity']['delta_floor_mm'])
            key = (method,record['case_id'])
            if key in deferred:
                result = dict(protocol_id=CONNECTIVITY_PROTOCOL,status='UNAVAILABLE',overall_pass=None,
                    part_count=record['part_count'],material_component_count=None,graph_component_count=None,
                    internal_connected=None,assembly_diagonal_mm=diagonal,delta_mm=delta,
                    delta_ratio=config['connectivity']['delta_ratio'],delta_floor_mm=config['connectivity']['delta_floor_mm'],
                    asset_sha256=record['asset_sha256'],measurement_config_sha256=None,evidence_ref=None,
                    reason_codes=['CONNECTIVITY_REEVALUATION_DEFERRED'],
                    details=dict(measured=False,deferred_reason=deferred[key]['reason'],
                        previous_evidence_ref=previous['evidence_ref']))
            else:
                result = measure_connectivity(loaded['parts'],delta_ratio=config['connectivity']['delta_ratio'],
                    delta_floor_mm=config['connectivity']['delta_floor_mm'])
                if result['asset_sha256'] != record['asset_sha256']:
                    raise ValueError('new connectivity asset binding mismatch')
                if result['status']=='PASS':
                    configs.add(result['measurement_config_sha256'])
            directory = output/f'method_{method_index:03d}'/f'case_{record_index:04d}'
            directory.mkdir(parents=True)
            path = directory/'connectivity_details.json'; write_json(path,result)
            brief = {k:v for k,v in result.items() if k not in ('details','measurement_config')}
            brief['evidence_ref'] = dict(path=str(path.relative_to(output)),json_pointer='',
                file_sha256=file_hash(path),asset_sha256=record['asset_sha256'])
            record['connectivity'] = brief
            row = _connectivity_record(record)
            if key in deferred:
                row['evaluation_outcome'] = 'PENDING'
            connectivity_rows.append(row)
            summary['samples'][method]['metrics'][record_index]['connectivity'] = deepcopy(row)
            audit.append(dict(method=method,case_id=record['case_id'],asset_sha256=record['asset_sha256'],
                D_mm=diagonal,delta_mm=delta,connectivity_remeasured=key not in deferred,
                status=result['status'],reason_codes=result['reason_codes']))
            print(f"{method}/{record['case_id']}: "
                f"{'DEFERRED' if key in deferred else result['status']}",flush=True)
        aggregated = _summarize_connectivity_rows(connectivity_rows)
        summary['samples'][method]['connectivity'] = aggregated
        summary['table'][method_index]['structural_connectivity_rate'] = aggregated['success_rate']
    if len(configs)>1:
        raise ValueError('new connectivity measurement configuration mismatch')
    if deferred:
        summary['status'] = 'INCOMPLETE'
        summary['deferred_connectivity'] = deepcopy(original['deferred_connectivity'])
    write_json(output/'input_identity.json',dict(records_sha256=file_hash(records_path),
        config_sha256=file_hash(config_path),connectivity_only=True,audit_only=False,
        model_calls=0,source_executed=False,standing_simulated=False,support_measured=False,
        remeasured_metrics=['connectivity'],reused_metrics=['standing','support_evaluation','N','sample_sets'],
        frozen_summary_ref=summary_ref,deferred_connectivity=list(deferred.values())))
    write_json(output/'reuse_verification.json',dict(frozen_summary_ref=summary_ref,
        unchanged_fields=['standing','nsv','N','common_measurable_case_ids','metric_case_ids'],
        evidence_files=[dict(file_sha256=k,path=v) for k,v in copied.items()],cases=audit))
    write_json(output/'measured_records.json',dict(methods=methods,frozen_summary_ref=summary_ref,
        deferred_connectivity=list(deferred.values())))
    write_json(output/'summary.json',summary)
    write_csv(output/'main_table.csv',summary['table'],['method','standing_success_rate',
        'structural_connectivity_rate','nsv','N'])
    write_csv(output/'connectivity_audit.csv',audit,['method','case_id','asset_sha256','D_mm','delta_mm',
        'connectivity_remeasured','status','reason_codes'])
    (output/'table_note.md').write_text(
        '几何接触连通率使用 geometric_facing_contact_connectivity_v1：'
        'δ=0.005D、相向法向点积<0、双向接近面积取min，面积阈值1.5e-5D²。\n'
        '本轮仅重测连通性；Standing、NSV24、实际打印件数 N 及样本集合原样复用冻结汇总。\n'
        'measured_records.json 保存 standing_evaluation 及冻结汇总引用，不是原始 checker 报告；\n'
        '须通过 --connectivity-only 配合 frozen_summary_ref 读取，不能直接重分类 Standing。\n'
        '暂缓案例是待测，不是观测到的失败；含暂缓案例的方法连通率尚不完整。\n'
        '本轮改变的是评测口径，未重新生成或改善模型。\n')
    return summary


def evaluate(records_path, config_path, output, *, audit_only=False, connectivity_only=False):
    if audit_only and connectivity_only:
        raise ValueError('audit_only and connectivity_only are mutually exclusive')
    records_path, config_path, output = map(Path, (records_path, config_path, output))
    original = json.loads(records_path.read_text())
    config = json.loads(config_path.read_text())
    if (config['standing_protocol'] != STANDING_PROTOCOL or
            config['connectivity']['protocol_id'] != CONNECTIVITY_PROTOCOL or
            config['support']['protocol_id'] != SUPPORT_PROTOCOL):
        raise ValueError('unsupported frozen physical protocol')
    for key in ('min_facing_area_ratio','facing_normal_dot_max','facing_area_reduction'):
        value = config['connectivity'].get(key)
        if isinstance(value,bool) or value != CONTACT_AREA_CONFIG[key]:
            raise ValueError('unsupported frozen facing-contact setting: '+key)
    for value in (config['connectivity']['delta_ratio'], config['connectivity']['delta_floor_mm'],
            config['support']['bed_tolerance_mm']):
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid frozen measurement parameter')
    angle = config['support']['overhang_threshold_from_horizontal_deg']
    if isinstance(angle, bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle) or not 0 < angle < 90:
        raise ValueError('invalid frozen support angle')
    if connectivity_only:
        return _evaluate_connectivity_only(original,config,records_path,config_path,output)
    output.mkdir(parents=True, exist_ok=False)
    methods, audit, missing = deepcopy(original['methods']), [], []
    write_json(output/'input_identity.json', dict(records_sha256=file_hash(records_path),
        config_sha256=file_hash(config_path), audit_only=audit_only,
        model_calls=0, source_executed=False, standing_simulated=False))
    for method_index, (method, records) in enumerate(methods.items()):
        for record_index, record in enumerate(records):
            geometry = record.get('geometry_input')
            if geometry:
                for key in ('mesh_path','manifest_path','source_path'):
                    if key in geometry:
                        p = Path(geometry[key])
                        geometry[key] = str(p.resolve() if p.is_absolute() else (records_path.parent/p).resolve())
            loaded = load_evaluation_parts(record)
            # A binding conflict is a protocol error, never a measured failure.
            if loaded['status'] != 'PASS' and 'mismatch' in loaded.get('reason',''):
                raise ValueError(loaded['reason'])
            row = dict(method=method, case_id=record['case_id'], status=loaded['status'],
                asset_sha256=loaded['asset_sha256'], D_mm=None, delta_ratio_D_mm=None,
                delta_mm=None, clearance_exceeds_delta=None, **loaded['audit'])
            if loaded['status'] == 'PASS':
                bounds, diagonal = assembly_bounds(loaded['parts'])
                delta = max(config['connectivity']['delta_ratio']*diagonal, config['connectivity']['delta_floor_mm'])
                row.update(D_mm=diagonal, delta_ratio_D_mm=config['connectivity']['delta_ratio']*diagonal,
                    delta_mm=delta, assembled_bounds_mm=json.dumps(bounds.tolist()),
                    clearance_exceeds_delta=(row['fit_offset_max_mm']>delta if row['fit_offset_max_mm'] is not None else None))
            else:
                row['reason'] = loaded.get('reason')
                missing.append(dict(method=method,case_id=record['case_id'],reason=loaded.get('reason')))
            audit.append(row)
            if audit_only:
                continue
            # The raw online support/checker record is deliberately left intact.
            record['asset_sha256'] = loaded['asset_sha256']
            record['part_count'] = len(loaded['parts']) if loaded['status']=='PASS' else record.get('part_count')
            details_dir = output/f'method_{method_index:03d}'/f'case_{record_index:04d}'
            details_dir.mkdir(parents=True)
            connectivity = measure_connectivity(loaded['parts'], delta_ratio=config['connectivity']['delta_ratio'],
                delta_floor_mm=config['connectivity']['delta_floor_mm'])
            support = measure_support24(loaded['parts'],
                overhang_threshold_from_horizontal_deg=config['support']['overhang_threshold_from_horizontal_deg'],
                bed_tolerance_mm=config['support']['bed_tolerance_mm'])
            for key, result, filename in [('connectivity',connectivity,'connectivity_details.json'),
                    ('support_evaluation',support,'support24_details.json')]:
                if loaded['status'] != 'PASS':
                    result['reason_codes'] = loaded['reason_codes']
                    result['details']['input_error'] = loaded.get('reason')
                path = details_dir/filename
                write_json(path,result)
                brief = {k:v for k,v in result.items() if k not in ('details','measurement_config')}
                brief['evidence_ref'] = dict(path=str(path.relative_to(output)),json_pointer='',
                    file_sha256=file_hash(path),asset_sha256=result['asset_sha256'])
                record[key] = brief
    write_csv(output/'size_clearance_audit.csv',audit,['method','case_id','status','asset_sha256',
        'D_mm','delta_ratio_D_mm','delta_mm','fit_offset_min_mm','fit_offset_max_mm',
        'clearance_exceeds_delta','unit_source','dimension_source','assembled_bounds_mm','reason'])
    write_json(output/'missing_assets.json',missing)
    if audit_only:
        return dict(audited=len(audit),missing=len(missing))
    summary = summarize_physical_methods(methods,protocol_id=config['standing_protocol'])
    write_json(output/'measured_records.json',dict(methods=methods))
    write_json(output/'summary.json',summary)
    write_csv(output/'main_table.csv',summary['table'],['method','standing_success_rate',
        'structural_connectivity_rate','nsv','N'])
    ids = summary['common_measurable_case_ids']['nsv']
    (output/'table_note.md').write_text(
        f'NSV 与 N 在所有对比方法同协议、共同可测的案例子集上分别取案例均值，n={len(ids)}。\n'
        'Standing 与结构连通成功率按各自预定适用案例统计；不可测原因和名单见 summary.json。\n'
        'N 是预定打印件数，不是材料分量数；min24 为离线打印方向搜索，不是在线直立效果。\n')
    return summary


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',type=Path,required=True)
    parser.add_argument('--config',type=Path,default=Path('benchmark/configs/physical_main_v1.json'))
    parser.add_argument('--output',type=Path,required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--audit-only',action='store_true')
    mode.add_argument('--connectivity-only',action='store_true')
    args = parser.parse_args()
    result = evaluate(args.records,args.config,args.output,audit_only=args.audit_only,
        connectivity_only=args.connectivity_only)
    print(json.dumps({k:v for k,v in result.items() if k in ('status','audited','missing','table')},ensure_ascii=False))


if __name__ == '__main__':
    main()
