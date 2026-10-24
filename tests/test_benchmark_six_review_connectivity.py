"""Offline wiring checks: saved admission measurements enter the review table."""
from copy import deepcopy
import ast
import json
from pathlib import Path

import pytest

from benchmark.metrics.physical import STANDING_PROTOCOL, summarize_physical_methods
from experiments.benchmark_six.build_review import build, _summarize_review_connectivity
from test_benchmark_six_evaluation import evaluation, _stub_final_connectivity_flow
from test_physical_metrics import measured


METHODS = ('official', 'ours')
NOTE = ('几何接触连通率使用 geometric_facing_contact_connectivity_v1：'
        'δ=0.005D、相向法向点积<0、双向接近面积取min，面积阈值1.5e-5D²。')


def result(case_id='one', method='official', *, outcome=True, state='TERMINAL',
           applicable=True, standing_applicable=True, null_hash=False):
    physical = measured(case_id)
    physical.pop('connectivity')  # Existing report inputs have no new measurement.
    physical.update(state=state, applicable=standing_applicable,
                    connectivity_applicable=applicable)
    asset = None if null_hash else 'new-composite-asset'
    connectivity = dict(protocol_id='geometric_facing_contact_connectivity_v1',
        status='UNAVAILABLE' if outcome is None else 'PASS', overall_pass=outcome,
        internal_connected=None if outcome is None else outcome,
        material_component_count=None if outcome is None else 2,
        graph_component_count=None if outcome is None else 1 if outcome else 2,
        part_count=1, asset_sha256=asset,
        measurement_config_sha256=None if null_hash else 'connectivity-config',
        reason_codes=['GEOMETRY_UNAVAILABLE'] if outcome is None else
            [] if outcome else ['GEOMETRIC_GRAPH_DISCONNECTED'],
        evidence_ref=dict(path='connectivity_details.json', json_pointer='',
                          file_sha256='evidence-hash', asset_sha256=asset))
    out = dict(case_id=case_id, arm=method, selected_source_sha256='source',
        physical_evaluation_record=physical, connectivity=connectivity,
        connectivity_asset_sha256=asset, status='FAIL')
    # Use the real evaluator assembly boundary, including its admission schema.
    evaluation.attach_connectivity_admission(out, state=state)
    assert 'connectivity_applicable' not in out['admission_record']
    return out


def inputs(rows):
    ids = list(next(iter(rows.values())))
    physical = {method:{cid:r['physical_evaluation_record'] for cid,r in records.items()}
                for method,records in rows.items()}
    return dict(case_ids=ids, results_by_method=rows,
                physical_records_by_method=physical)


def save(root, rows, *, standing_na=()):
    ids = list(next(iter(rows.values())))
    evaluation.write_json(root/'config/cases.json', {'cases':[
        dict(case_id=cid, standing_applicable=cid not in standing_na) for cid in ids]})
    for method, records in rows.items():
        for cid, value in records.items():
            folder = root/'jobs'/cid/method
            evaluation.write_json(folder/'job.json', {'generation':{'status':'COMPLETED'}})
            evaluation.write_json(folder/'evaluation/result.json', value)
    return inputs(rows)


def normal_rows():
    return {method:{cid:result(cid, method, outcome=outcome, null_hash=outcome is None)
                    for cid,outcome in [('connected', True), ('disconnected', False),
                                        ('unavailable', None)]} for method in METHODS}


def without_connectivity(physical):
    out = deepcopy(physical)
    out.pop('connectivity_reporting', None)
    for row in out['table']:
        row.pop('structural_connectivity_rate')
    for sample in out['samples'].values():
        sample.pop('connectivity')
        for metric in sample['metrics']:
            metric.pop('connectivity')
    return out


def test_actual_admission_assembly_reports_one_third_and_preserves_other_columns(tmp_path):
    rows = normal_rows()
    for method in METHODS:
        rows[method]['excluded'] = result('excluded', method, applicable=False)
        rows[method]['excluded'].pop('admission_record')
        # Standing applicability must not determine connectivity applicability.
        rows[method]['connected']['physical_evaluation_record']['applicable'] = False
    args = save(tmp_path, rows, standing_na=['connected'])
    original = deepcopy(args)
    baseline = summarize_physical_methods({method:list(records.values()) for method,records in
        args['physical_records_by_method'].items()}, protocol_id=STANDING_PROTOCOL)
    source_bytes = {path:path.read_bytes() for path in tmp_path.rglob('*') if path.is_file()}
    summary = build(tmp_path)
    physical = summary['physical']
    assert without_connectivity(physical) == without_connectivity(baseline)
    assert args == original
    assert all(path.read_bytes() == content for path,content in source_bytes.items())
    assert baseline['table'][0]['structural_connectivity_rate'] == 0
    assert physical['connectivity_reporting']['status'] == 'COMPLETE'
    assert physical['connectivity_reporting']['measurement_config_sha256'] == 'connectivity-config'
    for row in physical['table']:
        assert row['structural_connectivity_rate'] == pytest.approx(1/3)
    for sample in physical['samples'].values():
        connectivity = sample['connectivity']
        assert connectivity['planned_total'] == 3
        assert connectivity['success_count'] == 1
        assert connectivity['terminal_failure_count'] == 2
        assert connectivity['graph_disconnected_count'] == 1
        assert connectivity['unavailable_count'] == 1
        assert connectivity['missing_count'] == connectivity['pending_count'] == 0
        excluded = connectivity['cases'][-1]
        assert excluded['reason_codes'] == ['NOT_APPLICABLE']
        assert excluded['evaluation_outcome'] is None
        assert [m['connectivity'] for m in sample['metrics']] == connectivity['cases']
    assert json.loads((tmp_path/'review/physical_metrics.json').read_text()) == physical
    assert json.loads((tmp_path/'review/summary.json').read_text())['physical'] == physical
    markdown = (tmp_path/'review/README.md').read_text()
    independent = (Path(__file__).resolve().parents[1]/
                   'benchmark/scripts/evaluate_physical_metrics.py').read_text()
    assert NOTE in markdown
    assert any(isinstance(node, ast.Constant) and isinstance(node.value, str)
               and NOTE in node.value for node in ast.walk(ast.parse(independent)))
    assert 'Connectivity summary: COMPLETE' in markdown
    assert 'Standing 等原样复用冻结汇总' not in markdown


def test_real_final_evaluator_stub_flows_into_report(tmp_path, monkeypatch):
    raw = {}
    for method in METHODS:
        cid, folder, calls, _, _ = _stub_final_connectivity_flow(tmp_path, monkeypatch, method)
        monkeypatch.setattr(evaluation, 'render_final', lambda *a:
                            dict(status='PASS', native_views=[], neutral_views=[]))
        raw[method] = evaluation.evaluate(tmp_path, cid, method)
        assert [call[0] for call in calls] == ['load', 'measure']
        assert raw[method]['admission_record']['asset_sha256'] != raw[method][
            'physical_evaluation_record']['asset_sha256']
    before = {p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    physical = build(tmp_path)['physical']
    assert physical['connectivity_reporting']['status'] == 'COMPLETE'
    for sample in physical['samples'].values():
        assert sample['connectivity']['graph_disconnected_count'] == 1
        assert sample['connectivity']['success_rate'] == 0
        assert sample['standing']['success_rate'] == 1
    assert all(p.read_bytes() == content for p,content in before.items())


def change_record(value, field):
    if field == 'case_id':
        value['case_id'] = 'wrong'
    elif field == 'arm':
        value['arm'] = 'wrong'
    elif field == 'admission_record.case_id':
        value['admission_record']['case_id'] = 'wrong'
    elif field == 'state':
        value['admission_record']['state'] = 'INVALID'
    elif field == 'source_sha256':
        value['admission_record']['source_sha256'] = 'wrong'
    elif field == 'asset_sha256':
        value['admission_record']['asset_sha256'] = 'wrong'
    elif field == 'connectivity_asset_sha256':
        value['connectivity_asset_sha256'] = 'wrong'
    elif field == 'top-level connectivity':
        value['connectivity'] = dict(value['connectivity'], overall_pass=False)
    elif field == 'connectivity_applicable':
        value['physical_evaluation_record']['connectivity_applicable'] = False
    else:
        nested = value['admission_record']['connectivity']
        if field == 'evidence_ref.asset_sha256':
            nested['evidence_ref']['asset_sha256'] = 'wrong'
        elif field == 'evidence_ref.source_sha256':
            nested['evidence_ref']['source_sha256'] = 'wrong'
        else:
            nested[field] = {'protocol_id':'wrong', 'measurement_config_sha256':'different',
                             'overall_pass':None, 'part_count':3}[field]


@pytest.mark.parametrize('field', [
    'case_id', 'arm', 'admission_record.case_id', 'state', 'source_sha256',
    'asset_sha256', 'connectivity_asset_sha256', 'top-level connectivity',
    'connectivity_applicable', 'protocol_id', 'measurement_config_sha256',
    'evidence_ref.asset_sha256', 'evidence_ref.source_sha256', 'overall_pass', 'part_count'])
def test_conflicts_are_hard_errors_with_case_and_method(field):
    rows = {method:{'one':result('one', method)} for method in METHODS}
    change_record(rows['ours']['one'], field)
    with pytest.raises(ValueError) as error:
        _summarize_review_connectivity(**inputs(rows))
    assert 'one ours:' in str(error.value)
    expected = 'asset_sha256' if field == 'connectivity_asset_sha256' else field
    assert expected in str(error.value) or field in {'overall_pass', 'protocol_id'}


def test_missing_does_not_hide_later_conflicts():
    rows = normal_rows()
    rows['official']['connected'].pop('admission_record')
    rows['ours']['unavailable']['admission_record']['connectivity']['protocol_id'] = 'old'
    with pytest.raises(ValueError, match='unavailable ours:.*protocol'):
        _summarize_review_connectivity(**inputs(rows))


@pytest.mark.parametrize('missing', ['admission_record', 'connectivity'])
def test_missing_record_is_separate_from_failures_and_nulls_every_method(tmp_path, missing):
    rows = {method:{'one':result('one', method)} for method in METHODS}
    if missing == 'admission_record':
        rows['official']['one'].pop(missing)
    else:
        rows['official']['one']['admission_record'].pop(missing)
    save(tmp_path, rows)
    # Independent historical reassessment output is deliberately a separate path.
    evaluation.write_json(tmp_path/'independent_reassessment/summary.json',
                          {'structural_connectivity_rate':1.0})
    physical = build(tmp_path)['physical']
    report = physical['connectivity_reporting']
    assert report['status'] == 'REASSESSMENT_REQUIRED'
    assert report['missing_records'] == [{'case_id':'one', 'method':'official'}]
    assert report['pending_records'] == []
    for method,sample in physical['samples'].items():
        assert sample['connectivity']['success_rate'] is None
        assert sample['connectivity']['terminal_failure_count'] == 0
        assert sample['connectivity']['missing_count'] == (method == 'official')
    row = physical['samples']['official']['connectivity']['cases'][0]
    assert row['evaluation_outcome'] is None and row['status'] == 'REASSESSMENT_REQUIRED'
    assert row['reason_codes'] == ['REASSESSMENT_REQUIRED']
    assert all(r['structural_connectivity_rate'] is None for r in physical['table'])
    markdown = (tmp_path/'review/README.md').read_text()
    assert '| official | 1.000 | unknown |' in markdown
    assert 'Connectivity summary: REASSESSMENT_REQUIRED' in markdown
    assert 'Connectivity missing records (待重评): one / official.' in markdown


def test_existing_and_missing_result_lifecycle_counts_are_separate():
    rows = {method:{cid:result(cid, method, state=state) for cid,state in
                    [('pending', 'PENDING'), ('running', 'RUNNING'),
                     ('no_result', 'RUNNING'), ('terminal_missing', 'TERMINAL')]}
            for method in METHODS}
    args = inputs(rows)
    for method in METHODS:
        rows[method]['no_result'] = rows[method]['terminal_missing'] = None
    before = deepcopy(args)
    report = _summarize_review_connectivity(**args)
    assert args == before
    assert report['status'] == 'REASSESSMENT_REQUIRED'
    assert len(report['missing_records']) == 2 and len(report['pending_records']) == 6
    for summary in report['per_method'].values():
        assert summary['planned_total'] == 4
        assert summary['pending_count'] == 3 and summary['missing_count'] == 1
        assert summary['success_count'] == summary['terminal_failure_count'] == 0
        assert summary['unavailable_count'] == summary['graph_disconnected_count'] == 0
        assert summary['success_rate'] is None
        assert [r['evaluation_outcome'] for r in summary['cases']] == ['PENDING']*3 + [None]
    args['case_ids'] = ['no_result']
    pending = _summarize_review_connectivity(**args)
    assert pending['status'] == 'INCOMPLETE' and pending['missing_records'] == []


def test_no_applicable_cases_has_no_missing_failure_or_success_rate():
    rows = {method:{'one':result('one', method, applicable=False)} for method in METHODS}
    args = inputs(rows)
    for method in METHODS:
        rows[method]['one'] = None
    report = _summarize_review_connectivity(**args)
    assert report['status'] == 'COMPLETE'
    assert report['missing_records'] == report['pending_records'] == []
    for sample in report['per_method'].values():
        assert sample['planned_total'] == sample['missing_count'] == 0
        assert sample['terminal_failure_count'] == sample['pending_count'] == 0
        assert sample['success_rate'] is None


def test_not_applicable_measurement_still_validates_protocol():
    rows = {method:{'one':result('one', method, applicable=False)} for method in METHODS}
    rows['ours']['one']['admission_record']['connectivity']['protocol_id'] = 'old'
    with pytest.raises(ValueError, match='one ours:.*protocol'):
        _summarize_review_connectivity(**inputs(rows))


@pytest.mark.parametrize('field,value', [
    ('connectivity_applicable', 'false'),
    ('measurement_config_sha256', 'different'),
    ('overall_pass', False),
])
def test_unavailable_and_frozen_boolean_conflicts_are_rejected(field, value):
    rows = {method:{'one':result('one', method, outcome=None)} for method in METHODS}
    changed = rows['ours']['one']
    if field == 'connectivity_applicable':
        changed['physical_evaluation_record'][field] = value
    else:
        changed['admission_record']['connectivity'][field] = value
    with pytest.raises(ValueError, match='one ours:.*' + field):
        _summarize_review_connectivity(**inputs(rows))


def test_build_displays_pending_and_terminal_missing_result_files(tmp_path):
    evaluation.write_json(tmp_path/'config/cases.json', {'cases':[
        {'case_id':'running'}, {'case_id':'failed'}]})
    for method in METHODS:
        for cid,status in [('running', 'RUNNING'), ('failed', 'GENERATION_FAILED')]:
            evaluation.write_json(tmp_path/'jobs'/cid/method/'job.json',
                                  {'generation':{'status':status}})
    physical = build(tmp_path)['physical']
    reporting = physical['connectivity_reporting']
    assert reporting['status'] == 'REASSESSMENT_REQUIRED'
    assert reporting['missing_records'] == [
        {'case_id':'failed', 'method':method} for method in METHODS]
    assert reporting['pending_records'] == [
        {'case_id':'running', 'method':method} for method in METHODS]
    for sample in physical['samples'].values():
        assert sample['connectivity']['pending_count'] == 1
        assert sample['connectivity']['missing_count'] == 1
        assert sample['connectivity']['terminal_failure_count'] == 0
        assert sample['standing']['terminal_failure_count'] == 1
    markdown = (tmp_path/'review/README.md').read_text()
    assert 'Connectivity summary: REASSESSMENT_REQUIRED' in markdown
    assert 'failed / official' in markdown and 'running / ours' in markdown
