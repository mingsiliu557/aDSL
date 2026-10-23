"""Measurability selects paired datasets, never selects only physical successes."""
from copy import deepcopy
import json

import pytest

from benchmark.metrics import geometry, physical
from benchmark.scripts import curate_measurable_pairs as curation
from benchmark.scripts import evaluate_physical_metrics as evaluation


def record(case='one', *, connected=True, measurable=True, state='TERMINAL'):
    value = dict(protocol_id=geometry.CONNECTIVITY_PROTOCOL,status='PASS' if measurable else 'UNAVAILABLE',
        overall_pass=connected if measurable else None,part_count=1,
        material_component_count=1 if connected else 2,graph_component_count=(1 if connected else 2) if measurable else None,
        internal_connected=True,asset_sha256='asset',measurement_config_sha256='config',
        reason_codes=([] if connected else ['GEOMETRIC_GRAPH_DISCONNECTED']) if measurable else ['GLOBAL_MATERIAL_UNAVAILABLE'],
        evidence_ref=dict(path='evidence.json',json_pointer='',file_sha256='file',asset_sha256='asset'))
    return dict(case_id=case,state=state,asset_sha256='asset',part_count=1,connectivity=value)


@pytest.mark.parametrize('connected',[True,False])
def test_measured_failures_are_eligible_and_records_unchanged(connected):
    row=record(connected=connected)
    row['checker']={'status':'FAIL'}
    before=deepcopy(row)
    result=physical.connectivity_dataset_eligibility(row)
    assert result['eligible'] is True and result['status']=='ELIGIBLE'
    assert result['reason_codes']==[] and row==before


def test_unavailable_or_missing_evidence_is_excluded_not_unconnected():
    row=record(measurable=False)
    out=physical.connectivity_dataset_eligibility(row)
    assert out['status']=='EXCLUDED' and out['reason_codes']==['GLOBAL_MATERIAL_UNAVAILABLE']
    assert row['connectivity']['overall_pass'] is None
    missing=dict(case_id='missing',state='TERMINAL')
    assert physical.connectivity_dataset_eligibility(missing)['reason_codes']==['CONNECTIVITY_NOT_MEASURED']
    row=record();row['connectivity'].pop('evidence_ref')
    assert physical.connectivity_dataset_eligibility(row)['reason_codes']==['CONNECTIVITY_EVIDENCE_UNAVAILABLE']


def test_pending_counterpart_is_not_a_terminal_exclusion():
    out=physical.select_measurable_pairs({'a':[record()], 'b':[record(state='RUNNING')]})
    assert out['included_case_ids']==[] and out['excluded_cases']==[]
    assert out['status']=='PENDING' and out['pending_cases'][0]['case_id']=='one'


def test_unavailable_counterpart_excludes_whole_pair_but_keeps_measured_failure():
    rows={'a':[record('keep',connected=False),record('drop')],
          'b':[record('keep'),record('drop',measurable=False)]}
    before=deepcopy(rows)
    out=physical.select_measurable_pairs(rows)
    assert out['included_case_ids']==['keep']
    assert [r['case_id'] for r in out['excluded_cases']]==['drop']
    assert out['excluded_cases'][0]['methods']['a']['eligible'] is True
    assert rows==before


@pytest.mark.parametrize('change',['asset','evidence_asset','protocol','count','configuration','cases'])
def test_binding_or_protocol_conflicts_are_not_silently_pruned(change):
    a,b=record(),record()
    if change=='asset':b['connectivity']['asset_sha256']='wrong'
    elif change=='evidence_asset':b['connectivity']['evidence_ref']['asset_sha256']='wrong'
    elif change=='protocol':b['connectivity']['protocol_id']='old'
    elif change=='count':b['connectivity']['part_count']=2
    elif change=='configuration':b['connectivity']['measurement_config_sha256']='different'
    elif change=='cases':b['case_id']='different'
    with pytest.raises(ValueError):
        physical.select_measurable_pairs({'a':[a],'b':[b]})


def test_saved_pair_curation_does_not_measure_or_reclassify(tmp_path,monkeypatch):
    from test_connectivity_reassessment import frozen_inputs
    records,config,_=frozen_inputs(tmp_path)
    calls=[]
    def measure(parts,**kwargs):
        calls.append(None)
        out=record(connected=len(calls)!=1,measurable=len(calls)!=4)['connectivity']
        out.pop('evidence_ref')
        out['asset_sha256']=parts[0]['_asset_sha256']
        out['details']={}
        return out
    def forbidden(*args,**kwargs):
        raise AssertionError('curation must not measure or reclassify Standing')
    monkeypatch.setattr(evaluation,'measure_connectivity',measure)
    monkeypatch.setattr(evaluation,'measure_support24',forbidden)
    monkeypatch.setattr(evaluation,'summarize_physical_methods',forbidden)
    measured=tmp_path/'measured'
    source_summary=evaluation.evaluate(records,config,measured,connectivity_only=True)
    monkeypatch.setattr(evaluation,'measure_connectivity',forbidden)
    monkeypatch.setattr(physical,'classify_standing_record',forbidden)
    monkeypatch.setattr(physical,'summarize_standing',forbidden)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    output=tmp_path/'curated'
    result=curation.curate(measured/'measured_records.json',measured/'summary.json',output)
    assert len(calls)==4
    assert result['population']['case_ids']==['first']
    assert result['table'][0]['structural_connectivity_rate']==0.  # A measured failure stays.
    assert result['table'][1]['structural_connectivity_rate']==1.
    selection=json.loads((output/'selection.json').read_text())
    assert [r['case_id'] for r in selection['excluded_cases']]==['second']
    for method in ('baseline','ours'):
        assert result['samples'][method]['metrics']==source_summary['samples'][method]['metrics'][:1]
        assert result['samples'][method]['standing']['cases']==source_summary['samples'][method]['standing']['cases'][:1]
    curated=json.loads((output/'measured_records.json').read_text())
    for rows in curated['methods'].values():
        assert len(rows)==1
        for key in ('connectivity','support_evaluation'):
            ref=rows[0][key]['evidence_ref']
            assert geometry.file_hash(output/ref['path'])==ref['file_sha256']
    assert all(p.read_bytes()==data for p,data in before.items())
    with pytest.raises(FileExistsError):
        curation.curate(measured/'measured_records.json',measured/'summary.json',output)
