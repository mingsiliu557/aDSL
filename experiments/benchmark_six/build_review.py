"""Small report/contact-image builder; never selects or changes a candidate."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from PIL import Image
from importlib.util import find_spec
sys.path.insert(0, str(Path(find_spec('adsl.agents').origin).resolve().parents[1]))
from benchmark.metrics.physical import (
    STANDING_PROTOCOL, _connectivity_record, _summarize_connectivity_rows,
    summarize_physical_methods,
)


def read(path, default=None):
    return json.loads(Path(path).read_text()) if Path(path).is_file() else default


def shown(value):
    if value is None:
        return "unknown"
    return f"{value:.3f}" if isinstance(value, float) else str(value)


def _summarize_review_connectivity(*, case_ids, results_by_method,
                                   physical_records_by_method):
    """Read final admission measurements without changing frozen input records."""
    missing, pending, per_method = [], [], {}
    applicability, config_hash = {}, None
    for method, results in results_by_method.items():
        rows, classified, missing_count, pending_count = [], [], 0, 0
        for case_id in case_ids:
            def conflict(field):
                raise ValueError(f'{case_id} {method}: {field} conflict')

            original = physical_records_by_method[method][case_id]
            applicable = original.get('connectivity_applicable', True)
            if type(applicable) is not bool:
                conflict('connectivity_applicable')
            if case_id in applicability and applicability[case_id] != applicable:
                conflict('connectivity_applicable')
            applicability[case_id] = applicable
            if original.get('case_id') != case_id:
                conflict('physical_evaluation_record.case_id')
            state = original.get('state')
            if state not in {'PENDING', 'RUNNING', 'TERMINAL'}:
                conflict('state')
            result = results[case_id]
            admission, value = None, None
            if result is not None:
                if not isinstance(result, dict):
                    conflict('result')
                for field, expected in (('case_id', case_id), ('arm', method)):
                    if result.get(field) != expected:
                        conflict(field)
                source_hash = result.get('selected_source_sha256')
                admission = result.get('admission_record')
                if admission is not None and not isinstance(admission, dict):
                    conflict('admission_record')
                for record in (original, admission or {},
                               (original.get('checker') or {}).get('assumptions', {})):
                    if record.get('source_sha256') and record['source_sha256'] != source_hash:
                        conflict('source_sha256')
                if admission is not None:
                    if admission.get('case_id') != case_id:
                        conflict('admission_record.case_id')
                    if admission.get('state') != state:
                        conflict('state')
                    value = admission.get('connectivity')
                    if value is not None:
                        if not isinstance(value, dict):
                            conflict('connectivity')
                        if ('connectivity' in result and result['connectivity'] != value):
                            conflict('top-level connectivity')
                        asset_hash = admission.get('asset_sha256')
                        if asset_hash is not None and (
                                not isinstance(asset_hash, str) or not asset_hash):
                            conflict('asset_sha256')
                        if (value.get('asset_sha256') != asset_hash or
                                result.get('connectivity_asset_sha256') != asset_hash):
                            conflict('asset_sha256')
                        for record in (value, admission):
                            if (record.get('source_sha256') and
                                    record['source_sha256'] != source_hash):
                                conflict('source_sha256')
                            ref = record.get('evidence_ref')
                            if ref is not None:
                                if not isinstance(ref, dict):
                                    conflict('evidence_ref')
                                if ref.get('asset_sha256') != asset_hash:
                                    conflict('evidence_ref.asset_sha256')
                                if (ref.get('source_sha256') and
                                        ref['source_sha256'] != source_hash):
                                    conflict('evidence_ref.source_sha256')
                        if (not isinstance(value.get('reason_codes'), list) or
                                any(not isinstance(r, str) for r in value['reason_codes'])):
                            conflict('connectivity.reason_codes')
                        measured_config = value.get('measurement_config_sha256')
                        if measured_config is not None and (
                                not isinstance(measured_config, str) or not measured_config):
                            conflict('measurement_config_sha256')
                        if measured_config:
                            if config_hash is not None and measured_config != config_hash:
                                conflict('measurement_config_sha256')
                            config_hash = measured_config
                        if value.get('status') == 'PASS':
                            if type(value.get('overall_pass')) is not bool:
                                conflict('overall_pass')
                            count = admission.get('part_count')
                            if (type(count) is not int or count <= 0 or
                                    type(value.get('part_count')) is not int or
                                    value['part_count'] != count):
                                conflict('part_count')
                        elif (value.get('status') == 'UNAVAILABLE' and
                              value.get('overall_pass') is not None):
                            conflict('overall_pass')
                        classification_record = dict(admission)
                        classification_record['connectivity_applicable'] = applicable
                        try:
                            row = _connectivity_record(classification_record)
                        except (ValueError, TypeError, KeyError) as error:
                            raise ValueError(f'{case_id} {method}: connectivity {error}') from error
            if value is None:
                # Do not pass missing applicable rows to the legacy classifier:
                # it interprets terminal missing measurements as failures.
                row = dict(case_id=case_id, applicable=applicable,
                    evaluation_outcome=None, reason_codes=['NOT_APPLICABLE'],
                    internal_connected=None, material_component_count=None,
                    graph_component_count=None, status='NOT_APPLICABLE')
                if applicable:
                    identity = dict(case_id=case_id, method=method)
                    if result is None and state != 'TERMINAL':
                        pending.append(identity)
                        pending_count += 1
                        row.update(evaluation_outcome='PENDING',
                            reason_codes=['NOT_FINISHED'], status='PENDING')
                    else:
                        missing.append(identity)
                        missing_count += 1
                        row.update(reason_codes=['REASSESSMENT_REQUIRED'],
                            status='REASSESSMENT_REQUIRED')
            else:
                if applicable and state != 'TERMINAL':
                    pending.append(dict(case_id=case_id, method=method))
                    pending_count += 1
                classified.append(row)
            rows.append(row)
        summary = _summarize_connectivity_rows(classified)
        summary.update(planned_total=sum(r['applicable'] for r in rows),
            missing_count=missing_count, pending_count=pending_count, cases=rows)
        total = summary['planned_total']
        summary['success_rate'] = (summary['success_count'] / total
            if total and not missing_count and not pending_count else None)
        per_method[method] = summary
    status = ('REASSESSMENT_REQUIRED' if missing else
              'INCOMPLETE' if pending else 'COMPLETE')
    if status != 'COMPLETE':
        for summary in per_method.values():
            summary['success_rate'] = None
    return dict(status=status, missing_records=missing, pending_records=pending,
        measurement_config_sha256=config_hash, per_method=per_method)


def build(root):
    root = Path(root)
    output = root / "review"
    output.mkdir(parents=True, exist_ok=True)
    configured = read(root / "config/cases.json", {"cases": []})
    cases = configured.get("cases", []) if isinstance(configured, dict) else configured
    environments = read(root / 'config/envs.json', {})
    arms = tuple(environments.get('arms', {})) or ('official', 'ours')
    physical_records = {arm:[] for arm in arms}
    results_by_method = {arm:{} for arm in arms}
    physical_records_by_method = {arm:{} for arm in arms}
    rows, pictures = [], []
    for case in cases:
        cid = case.get("case_id", case.get("id"))
        applicable = bool(case.get('standing_applicable', True)) and cid not in (
            'dragon_007','Toys4K_dragon_007')
        views = {}
        for arm in arms:
            directory = root / "jobs" / cid / arm
            job = read(directory / "job.json", {})
            result = read(directory / "evaluation/result.json")
            results_by_method[arm][cid] = result
            measured = result if result is not None else {}
            review = read(directory / "review/image_critic.json", {})
            metrics = measured.get("metrics", {})
            record = measured.get('physical_evaluation_record')
            if record is None:
                if measured:
                    raise ValueError('legacy evaluation has no independent standing protocol record; do not mix reports')
                generation = str(job.get('generation', {}).get('status', '')).upper()
                terminal_failure = generation in {'FAIL','FAILED','ERROR','GENERATION_FAILED','TIMEOUT','API_INTERRUPTED'}
                record = dict(case_id=cid, applicable=applicable,
                    protocol_id=STANDING_PROTOCOL, state='TERMINAL' if terminal_failure else 'PENDING',
                    generation_status=generation, checker={},
                    source_sha256=job.get('selected_source_sha256'), asset_sha256=job.get('selected_glb_sha256'))
            if record.get('case_id') != cid or record.get('applicable') is not applicable:
                raise ValueError('evaluation identity/applicability differs from frozen case')
            physical_records[arm].append(record)
            physical_records_by_method[arm][cid] = record
            dapper = measured.get("native_dapper", {}).get("partition_objective", {})
            rows.append(dict(case_id=cid, arm=arm,
                generation=job.get("generation", {}).get("status", "PENDING"),
                native_approved=job.get("approved"),
                common_appearance_approved=review.get("decision", {}).get("approved"),
                topology=measured.get("topology", {}).get("status", "NOT_MEASURED"),
                standing=measured.get("standing", {}).get("status", "NA" if not case.get("standing_applicable", True) else "NOT_MEASURED"),
                min_overhang_area_mm2=metrics.get("min_overhang_area_mm2"),
                G=metrics.get("G"), N=metrics.get("N"),
                voxel_pitch_mm=case.get("voxel_pitch_mm"),
                internal_dapper_score=dapper.get("score"),
                cross_method_dapper_comparable=False,
                source_repairs=job.get("generation", {}).get("actual_source_repairs"),
                generation_requests=job.get("usage", {}).get("requests"),
                generation_tokens=job.get("usage", {}).get("total_tokens"),
                final_review_requests=review.get("usage", {}).get("requests"),
                final_review_tokens=review.get("usage", {}).get("total_tokens"),
                generation_seconds=job.get("generation", {}).get("elapsed_seconds"),
                native_selected_source=job.get("source"),
                evaluation=str(directory / "evaluation/result.json")))
            views[arm] = measured.get("render_paths", {}).get("native_views", [])
        if len(views.get("official", [])) == len(views.get("ours", [])) == 8:
            pair_dir = output / cid
            pair_dir.mkdir(exist_ok=True)
            for index, (a, b) in enumerate(zip(views["official"], views["ours"]), 1):
                with Image.open(a) as official, Image.open(b) as ours:
                    # Same-sized images, no independent object crop or scaling.
                    if official.size != ours.size:
                        raise ValueError(f"different common render dimensions: {cid}")
                    canvas = Image.new("RGBA", (official.width * 2, official.height), "white")
                    canvas.alpha_composite(official.convert("RGBA"), (0, 0))
                    canvas.alpha_composite(ours.convert("RGBA"), (official.width, 0))
                    destination = pair_dir / f"comparison_{index:04d}.png"
                    canvas.convert("RGB").save(destination)
                    pictures.append(dict(case_id=cid, view=index, path=str(destination),
                                         left="official", right="ours"))
    connectivity = _summarize_review_connectivity(
        case_ids=[case.get('case_id', case.get('id')) for case in cases],
        results_by_method=results_by_method,
        physical_records_by_method=physical_records_by_method)
    physical = summarize_physical_methods(physical_records, protocol_id=STANDING_PROTOCOL)
    for row in physical['table']:
        row['structural_connectivity_rate'] = connectivity['per_method'][row['method']]['success_rate']
    for method, sample in physical['samples'].items():
        updated = connectivity['per_method'][method]
        sample['connectivity'] = updated
        by_case = {row['case_id']:row for row in updated['cases']}
        for metric in sample['metrics']:
            metric['connectivity'] = by_case[metric['case_id']]
    physical['connectivity_reporting'] = {key:value for key,value in connectivity.items()
                                          if key != 'per_method'}
    (output/'physical_metrics.json').write_text(json.dumps(physical, ensure_ascii=False, indent=2)+'\n')
    summary = dict(status=read(root / "checkpoint.json", {}).get("status", "PREPARING"),
                   physical=physical,
                   denominators={"appearance": len(cases), "printing": len(cases),
                       "standing":sum(bool(c.get('standing_applicable',True))
                           and c.get('case_id',c.get('id')) not in ('dragon_007','Toys4K_dragon_007') for c in cases)},
                   rows=rows, pictures=pictures)
    (output / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    lines = ["# Physical method comparison", "",
        f"Batch status: {summary['status']}; physical summary: {physical['status']}.", "",
        "| Method | Standing success rate ↑ | Geometric contact connectivity ↑ | NSV ↓ | N |",
        "|---|---:|---:|---:|---:|"]
    lines += ["| " + " | ".join(shown(row[k]) for k in
        ('method','standing_success_rate','structural_connectivity_rate','nsv','N')) + " |" for row in physical['table']]
    lines += ["",
        "Standing uses the frozen applicable denominator, including terminal generation/simulation failures. Pending records mean the experiment is incomplete.",
        "几何接触连通率使用 geometric_facing_contact_connectivity_v1：δ=0.005D、相向法向点积<0、双向接近面积取min，面积阈值1.5e-5D²。",
        "本指标参考 SNAP3D 的距离与相向面积接触判据并作适配。节点为最终装配后的材料分量；两个分量同时满足最小距离与相向近接面积门槛时连边，整图连通即为通过。面积为表面采样估计值，D 为装配体包围盒对角线。该指标衡量几何接触连通，不代表机械锁紧或承重。按预定连通适用案例统计，不适用案例不进入分母。适用案例缺少新版记录时，比较表的连通列标为待重评；终态测量不可用计为未成功，并单独保留原因。",
        f"Connectivity summary: {connectivity['status']}; null rates are shown as unknown.",
        "Connectivity missing records (待重评): " + (", ".join(
            f"{row['case_id']} / {row['method']}" for row in connectivity['missing_records']) or "none") + ".",
        "Connectivity pending records (待完成): " + (", ".join(
            f"{row['case_id']} / {row['method']}" for row in connectivity['pending_records']) or "none") + ".",
        "NSV24 and N use the same all-method, same-protocol common measurable case list in physical_metrics.json; those subsets are not all-task success rates.",
        "Raw IVR remains a separate historical diagnostic. Legacy support without support_volume_min24_v1 is not measured NSV24. Missing measurements remain null. NSV uses the minimum ray S among the same 24 rotations for each final part, not a continuous global minimum or slicer consumption.",
        "Contact warnings do not change standing observations; topology failures still matter for final product acceptance.",
        "", "## Per-case evidence appendix", "",
        "Comparison images: left = official aDSL, right = ours; common native-material views, without independent crops or text overlays.", "",
        "Minimum geometric overhang area and minimum gap use separately selected print orientations. Neither is slicer support usage.",
        "Dapper scores are internal ours records and cannot rank methods with different reference quantities.",
        "Failures remain in the frozen denominators; applicability and missing reasons are retained in physical_metrics.json.", "",
        "| Case | Method | Generation | Native approved | Common appearance | Topology | Standing | Min area mm2 | G | N | Internal O | Repairs | Gen requests | Gen tokens | Final review requests | Final review tokens | Gen seconds |",
        "|---|---|---|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    keys = ("case_id", "arm", "generation", "native_approved", "common_appearance_approved", "topology", "standing",
            "min_overhang_area_mm2", "G", "N", "internal_dapper_score", "source_repairs", "generation_requests",
            "generation_tokens", "final_review_requests", "final_review_tokens", "generation_seconds")
    lines += ["| " + " | ".join(shown(row.get(k)) for k in keys) + " |" for row in rows]
    lines += ["", "Images and full per-job evidence:", ""]
    for case in cases:
        cid = case.get("case_id", case.get("id"))
        first = output / cid / "comparison_0001.png"
        if first.is_file():
            lines.append(f"- {cid}: [paired views]({cid}/comparison_0001.png); all eight views are in the same directory.")
        else:
            lines.append(f"- {cid}: paired common views are not yet available.")
    (output / "README.md").write_text("\n".join(lines) + "\n")
    return summary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run-root", required=True, type=Path)
    build(parser.parse_args().run_root)
