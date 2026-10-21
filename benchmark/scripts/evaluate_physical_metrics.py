"""Measure explicit frozen records offline; never discover or execute source."""
from __future__ import annotations

import argparse
from copy import deepcopy
import csv
import json
import math
from pathlib import Path

from benchmark.metrics.geometry import (load_evaluation_parts, assembly_bounds,
    measure_connectivity, measure_support24, file_hash, CONNECTIVITY_PROTOCOL, SUPPORT_PROTOCOL)
from benchmark.metrics.physical import summarize_physical_methods, STANDING_PROTOCOL


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


def write_csv(path, rows, fields):
    with path.open('w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields, extrasaction='ignore')
        writer.writeheader(); writer.writerows(rows)


def evaluate(records_path, config_path, output, *, audit_only=False):
    records_path, config_path, output = map(Path, (records_path, config_path, output))
    original = json.loads(records_path.read_text())
    config = json.loads(config_path.read_text())
    if (config['standing_protocol'] != STANDING_PROTOCOL or
            config['connectivity']['protocol_id'] != CONNECTIVITY_PROTOCOL or
            config['support']['protocol_id'] != SUPPORT_PROTOCOL):
        raise ValueError('unsupported frozen physical protocol')
    for value in (config['connectivity']['delta_ratio'], config['connectivity']['delta_floor_mm'],
            config['support']['bed_tolerance_mm']):
        if isinstance(value, bool) or not isinstance(value, (int,float)) or not math.isfinite(value) or value < 0:
            raise ValueError('invalid frozen measurement parameter')
    angle = config['support']['overhang_threshold_from_horizontal_deg']
    if isinstance(angle, bool) or not isinstance(angle,(int,float)) or not math.isfinite(angle) or not 0 < angle < 90:
        raise ValueError('invalid frozen support angle')
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
    parser.add_argument('--audit-only',action='store_true')
    args = parser.parse_args()
    result = evaluate(args.records,args.config,args.output,audit_only=args.audit_only)
    print(json.dumps({k:v for k,v in result.items() if k in ('status','audited','missing','table')},ensure_ascii=False))


if __name__ == '__main__':
    main()
