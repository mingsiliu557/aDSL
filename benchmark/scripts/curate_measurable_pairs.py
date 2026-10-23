"""Select a paired measurable dataset from completed results; no measurements."""
from __future__ import annotations

import argparse
from copy import deepcopy
import json
import os
from pathlib import Path

from benchmark.metrics.geometry import file_hash
from benchmark.metrics.physical import (
    _connectivity_record, _summarize_connectivity_rows, select_measurable_pairs,
)
from benchmark.scripts.evaluate_physical_metrics import (
    _validate_reuse, write_json, write_csv,
)


def _subset_summary(original, case_ids):
    """Reaggregate saved observations, without reclassifying Standing."""
    out, keep = deepcopy(original), set(case_ids)
    for key, ids in out['common_measurable_case_ids'].items():
        out['common_measurable_case_ids'][key] = [cid for cid in ids if cid in keep]
    for table_row in out['table']:
        sample = out['samples'][table_row['method']]
        sample['metrics'] = [r for r in sample['metrics'] if r['case_id'] in keep]
        standing = sample['standing']
        standing['cases'] = [r for r in standing['cases'] if r['case_id'] in keep]
        active = [r for r in standing['cases'] if r['applicable']]
        success = sum(r['evaluation_outcome']=='PASS' for r in active)
        failed = sum(r['evaluation_outcome']=='FAIL' for r in active)
        pending = sum(r['evaluation_outcome']=='PENDING' for r in active)
        standing.update(planned_total=len(active),success_count=success,
            terminal_failure_count=failed,pending_count=pending,
            status='INCOMPLETE' if pending else 'COMPLETE',
            success_rate=success/len(active) if active and not pending else None,
            observed_tipping_count=sum('TIPPING' in r['reason_codes'] for r in active),
            observed_interface_exit_count=sum('INTERFACE_EXIT' in r['reason_codes'] for r in active))
        sample['connectivity'] = _summarize_connectivity_rows(
            r for r in sample['connectivity']['cases'] if r['case_id'] in keep)
        table_row['standing_success_rate'] = standing['success_rate']
        table_row['structural_connectivity_rate'] = sample['connectivity']['success_rate']
        metrics = {r['case_id']:r for r in sample['metrics']}
        for key in ('nsv','N'):
            ids = [cid for cid in sample['metric_case_ids'][key] if cid in keep]
            sample['metric_case_ids'][key] = ids
            table_row[key] = sum(metrics[cid][key] for cid in ids)/len(ids) if ids else None
            sample['measured_counts'][key] = sum(r[key] is not None for r in metrics.values())
    out['population'] = dict(kind='paired_connectivity_measurable_subset',case_count=len(case_ids),
        case_ids=list(case_ids),note='A subset table, not the original fixed-sample success rate.')
    return out


def _rebase_refs(value, base, output):
    if isinstance(value,dict):
        if {'path','file_sha256','json_pointer'} <= value.keys():
            value['path'] = os.path.relpath((base/value['path']).resolve(),output)
        for child in value.values():
            _rebase_refs(child,base,output)
    elif isinstance(value,list):
        for child in value:
            _rebase_refs(child,base,output)


def curate(records_path, summary_path, output):
    records_path, summary_path, output = (Path(p).resolve() for p in (records_path,summary_path,output))
    if output.exists():
        raise FileExistsError('curation requires a new output directory')
    original = json.loads(records_path.read_text())
    summary = json.loads(summary_path.read_text())
    # Use the summary for these measured records, not the older summary which
    # connectivity-only evaluation may have preserved as a provenance link.
    bound = deepcopy(original)
    bound['frozen_summary_ref'] = dict(path=os.path.relpath(summary_path,records_path.parent),
        json_pointer='',file_sha256=file_hash(summary_path))
    config = json.loads((Path(__file__).resolve().parents[1]/'configs/physical_main_v1.json').read_text())
    _validate_reuse(bound,config,records_path.parent)
    for method, records in original['methods'].items():
        sample = summary['samples'][method]
        expected = [_connectivity_record(r) for r in records]
        if (sample['connectivity']['cases'] != expected
                or [r['connectivity'] for r in sample['metrics']] != expected):
            raise ValueError('current connectivity summary does not match the measured records')
    selection = select_measurable_pairs(original['methods'])
    if selection['pending_cases']:
        raise ValueError('dataset curation requires completed method results')
    keep = set(selection['included_case_ids'])
    updated = _subset_summary(summary,selection['included_case_ids'])
    methods = deepcopy(original['methods'])
    for method, records in methods.items():
        methods[method] = [r for r in records if r['case_id'] in keep]
        for record in methods[method]:
            for key in ('mesh_path','manifest_path','source_path'):
                if key in record.get('geometry_input',{}):
                    record['geometry_input'][key] = os.path.relpath(
                        (records_path.parent/record['geometry_input'][key]).resolve(),output)
    _rebase_refs(methods,records_path.parent,output)
    indexed = {method:{r['case_id']:r for r in records} for method,records in original['methods'].items()}
    for exclusion in selection['excluded_cases']:
        for method, decision in exclusion['methods'].items():
            original_record = indexed[method][exclusion['case_id']]
            decision['source_sha256'] = original_record['source_sha256']
            decision['evidence_ref'] = deepcopy(original_record['connectivity'].get('evidence_ref'))
    _rebase_refs(selection,records_path.parent,output)
    output.mkdir(parents=True,exist_ok=False)
    write_json(output/'summary.json',updated)
    write_json(output/'selection.json',selection)
    write_json(output/'measured_records.json',dict(methods=methods,
        frozen_summary_ref=dict(path='summary.json',json_pointer='',file_sha256=file_hash(output/'summary.json')),
        source_summary_ref=dict(path=os.path.relpath(summary_path,output),json_pointer='',file_sha256=file_hash(summary_path))))
    write_csv(output/'main_table.csv',updated['table'],
        ['method','standing_success_rate','structural_connectivity_rate','nsv','N'])
    write_json(output/'curation_identity.json',dict(policy_id=selection['policy_id'],
        source_records_sha256=file_hash(records_path),source_summary_sha256=file_hash(summary_path),
        original_case_count=len(next(iter(original['methods'].values()))),included_case_count=len(keep),
        excluded_case_ids=[r['case_id'] for r in selection['excluded_cases']],
        remeasured_metrics=[],model_calls=0,original_files_modified=False))
    (output/'table_note.md').write_text(
        f'共同可测配对子集：{len(keep)} 例；任一方法连通性不可测则整对排除。\n'
        '测得图不连通或 Standing FAIL 不因此排除。原始全样本表及不可测原因保留。\n'
        '本表只重汇总保存的逐例结果，没有重测、重生成或修补模型；不代表原全样本成功率。\n'
        'NSV24 与 N 继续在此子集内共同可测的同一名单上取案例均值。\n',encoding='utf-8')
    return updated


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--records',required=True,type=Path)
    parser.add_argument('--summary',required=True,type=Path)
    parser.add_argument('--output',required=True,type=Path)
    args = parser.parse_args()
    result = curate(args.records,args.summary,args.output)
    print(json.dumps(dict(population=result['population'],table=result['table']),ensure_ascii=False))


if __name__ == '__main__':
    main()
