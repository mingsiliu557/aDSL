"""Pure record fixtures for the independent standing evaluation protocol."""
from copy import deepcopy
import json

import pytest

from benchmark.metrics.physical import (
    STANDING_PROTOCOL as PROTOCOL, classify_standing_record, summarize_standing,
    raw_ivr, physical_table_row, summarize_physical_methods)


def record(case_id='one', *, status='PASS', state='TERMINAL'):
    complete = status in {'PASS','FAIL'}
    return dict(case_id=case_id, applicable=True, state=state, protocol_id=PROTOCOL,
        generation_status='COMPLETED', source_sha256='source',asset_sha256='asset',
        checker=dict(status=status, assumptions={'standing_protocol':PROTOCOL},
            metrics={'standing_observation':dict(status=status if complete else 'UNAVAILABLE',
                observed_tipping=False if complete else None,
                observed_interface_exit=False if complete else None,
                peak_tilt_deg=.001 if complete else None, final_tilt_deg=.001 if complete else None,
                duration_s=5. if complete else None),
                'contact_diagnostic':{'status':'WARNING','affects_standing_verdict':False}}))


def test_warning_is_success_but_solver_error_is_not_tipping():
    good=record()
    error=record('error',status='ERROR')
    error['checker']['violations']=[{'code':'CHECKER_INFRASTRUCTURE_ERROR'}]
    before=deepcopy([good,error])
    summary=summarize_standing([good,error],protocol_id=PROTOCOL)
    assert summary['planned_total']==2 and summary['success_rate']==.5
    assert summary['terminal_failure_count']==1 and summary['observed_tipping_count']==0
    assert 'SIMULATION_ERROR' in summary['cases'][1]['reason_codes']
    assert summary['cases'][1]['observation']['observed_tipping'] is None
    assert [good,error]==before


def test_pending_and_generation_failure_keep_frozen_denominator():
    pending=record('later',state='PENDING');pending['checker']={}
    failed=record('generation');failed.update(checker={},generation_status='GENERATION_FAILED')
    rows=[record(),pending,failed]
    summary=summarize_standing(rows,protocol_id=PROTOCOL)
    assert summary['status']=='INCOMPLETE' and summary['success_rate'] is None
    assert summary['planned_total']==3 and summary['pending_count']==1
    assert summary['terminal_failure_count']==1
    assert 'GENERATION_FAILED' in summary['cases'][2]['reason_codes']


def test_exit_then_return_and_tipping_both_count():
    r=record(status='FAIL')
    r['checker']['metrics']['standing_observation'].update(
        observed_tipping=True,observed_interface_exit=True,peak_tilt_deg=30.,final_tilt_deg=0.)
    result=classify_standing_record(r,protocol_id=PROTOCOL)
    assert result['evaluation_outcome']=='FAIL'
    assert result['reason_codes']==['TIPPING','INTERFACE_EXIT']


@pytest.mark.parametrize('code,reason', [
    ('CHECKER_TIMEOUT','TIMEOUT'), ('DEPENDENCY_MESH_UNAVAILABLE','INVALID_INPUT'),
    ('COLLISION_PROXY_UNVERIFIED','UNSUPPORTED_INPUT'), ('SIMULATION_UNVERIFIED','SIMULATION_ERROR'),
])
def test_terminal_unavailable_reasons(code,reason):
    r=record(status='INDETERMINATE')
    r['checker']['summary']='ANALYSIS_UNAVAILABLE: '+code+': fixture'
    out=classify_standing_record(r,protocol_id=PROTOCOL)
    assert out['evaluation_outcome']=='FAIL' and reason in out['reason_codes']
    assert out['observation']['observed_tipping'] is None


def test_old_protocol_and_missing_cases_cannot_silently_mix():
    old=record();old['checker']['assumptions']['standing_protocol']='old_depth_gate'
    with pytest.raises(ValueError,match='protocol'):
        classify_standing_record(old,protocol_id=PROTOCOL)
    with pytest.raises(ValueError,match='frozen'):
        summarize_physical_methods({'a':[record()], 'b':[]},protocol_id=PROTOCOL)
    with pytest.raises(ValueError,match='duplicate'):
        summarize_standing([record(),record()],protocol_id=PROTOCOL)
    stale=record();stale['checker']['assumptions']['source_sha256']='other-source'
    with pytest.raises(ValueError,match='source binding'):
        classify_standing_record(stale,protocol_id=PROTOCOL)


def test_pending_observation_is_not_counted_as_measured_tipping():
    pending=record(state='RUNNING',status='FAIL')
    pending['checker']['metrics']['standing_observation']['observed_tipping']=True
    result=summarize_standing([pending],protocol_id=PROTOCOL)
    assert result['pending_count']==1 and result['observed_tipping_count']==0


def test_raw_ivr_uses_all_raw_pairs_and_no_interference_allowance():
    pair=dict(part_ids=['a','b'],raw_intersection_mm3=3.,undeclared_interference_mm3=0.)
    assert raw_ivr({'a':10.,'b':20.},[pair])['value']==.1
    assert raw_ivr({'a':10.},[])['reason']=='SINGLE_PART_NOT_APPLICABLE'
    assert raw_ivr({'a':10.,'b':20.},[])['value'] is None
    assert raw_ivr({'a':10.,'b':None},[pair])['value'] is None
    bounded=dict(pair,raw_intersection_mm3=-1e-17,volume_tolerance_mm3=1e-5)
    assert raw_ivr({'a':10.,'b':20.},[bounded])['value']==0.
    assert bounded['raw_intersection_mm3']==-1e-17


def test_main_metrics_preserve_null_and_common_sample_lists():
    a=record()
    a.update(part_count=2,part_volumes_mm3={'a':10.,'b':20.},
        pair_intersections=[dict(part_ids=['a','b'],raw_intersection_mm3=3.)],
        support=dict(status='PASS',total_support_volume_mm3=3.,total_volume_mm3=30.,nsv_total=.1),
        support_measurement_config_sha256='same-protocol')
    b=deepcopy(a);b['support']={'status':'INDETERMINATE','nsv_total':None}
    assert physical_table_row(b,protocol_id=PROTOCOL)['nsv'] is None
    out=summarize_physical_methods({'a':[a],'b':[b]},protocol_id=PROTOCOL)
    assert out['common_measurable_case_ids']['nsv']==[]
    assert out['table'][0]['nsv'] is None and out['table'][0]['raw_ivr']==.1
    assert set(out['table'][0])=={'method','standing_success_rate','raw_ivr','nsv','N'}
    assert out['samples']['b']['metrics'][0]['missing_reasons']['nsv']=='SUPPORT_UNAVAILABLE'
    json.dumps(out,allow_nan=False)


def test_nsv_different_config_is_not_comparable():
    a=record();a.update(part_count=1,support=dict(status='PASS',
        total_support_volume_mm3=1.,total_volume_mm3=10.,nsv_total=.1),
        support_measurement_config_sha256='a')
    b=deepcopy(a);b['support_measurement_config_sha256']='b'
    out=summarize_physical_methods({'a':[a],'b':[b]},protocol_id=PROTOCOL)
    assert out['nsv_configuration_mismatch_case_ids']==['one']
    assert out['table'][0]['nsv'] is None
