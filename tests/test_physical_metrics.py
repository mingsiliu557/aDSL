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


def measured(case_id='one', *, count=1, internal=True, materials=None, groups=1):
    r=record(case_id);r['part_count']=count
    reasons=[] if groups==1 else ['GEOMETRIC_GRAPH_DISCONNECTED']
    r['connectivity']=dict(protocol_id='geometric_facing_contact_connectivity_v1',status='PASS',
        overall_pass=groups==1,internal_connected=internal,
        material_component_count=count if materials is None else materials,graph_component_count=groups,
        part_count=count,reason_codes=reasons,asset_sha256='asset',measurement_config_sha256='connectivity-config')
    r['support_evaluation']=dict(protocol_id='support_volume_min24_v1',status='PASS',
        total_support_volume_mm3=3.,total_volume_mm3=30.,nsv_total=.1,part_count=count,
        orientation_objective='support_volume_mm3',orientation_set='axis_aligned_24',
        asset_sha256='asset',measurement_config_sha256='support-config',reason_codes=[])
    return r


def test_main_metrics_preserve_null_and_shared_nsv_n_sample_lists():
    a=measured();b=deepcopy(a)
    b['support_evaluation'].update(status='UNAVAILABLE',nsv_total=None)
    out=summarize_physical_methods({'a':[a],'b':[b]},protocol_id=PROTOCOL)
    assert out['common_measurable_case_ids']=={'nsv':[],'N':[]}
    assert out['table'][0]['nsv'] is out['table'][0]['N'] is None
    assert set(out['table'][0])=={'method','standing_success_rate','structural_connectivity_rate','nsv','N'}
    assert out['samples']['b']['metrics'][0]['missing_reasons']['nsv']=='SUPPORT24_UNAVAILABLE'
    json.dumps(out,allow_nan=False)


def test_legacy_support_is_not_min24():
    r=record();r.update(support=dict(status='PASS',nsv_total=.1,total_volume_mm3=30,total_support_volume_mm3=3),part_count=1)
    result=physical_table_row(r,protocol_id=PROTOCOL)
    assert result['nsv'] is None
    assert result['missing_reasons']['nsv']=='SUPPORT24_NOT_MEASURED'


@pytest.mark.parametrize('key',['connectivity','support_evaluation'])
@pytest.mark.parametrize('field,value',[('protocol_id','old'),('asset_sha256','other')])
def test_measurement_protocol_and_asset_mismatch_rejected(key,field,value):
    r=measured();r[key][field]=value
    with pytest.raises(ValueError,match='mismatch'):
        physical_table_row(r,protocol_id=PROTOCOL)


@pytest.mark.parametrize('key',['connectivity','support_evaluation'])
def test_config_mismatch_rejected(key):
    a=measured();b=deepcopy(a);b[key]['measurement_config_sha256']='other'
    with pytest.raises(ValueError,match='configuration'):
        summarize_physical_methods({'a':[a],'b':[b]},protocol_id=PROTOCOL)


def test_single_baseline_multi_ours_ivr_na_does_not_affect_main_table():
    a=measured(count=1);b=measured(count=2)
    a.update(part_volumes_mm3={'whole':100},pair_intersections=[])
    b.update(part_volumes_mm3={'a':80,'b':20},pair_intersections=[dict(part_ids=['a','b'],raw_intersection_mm3=.2)])
    before=deepcopy([a,b]);out=summarize_physical_methods({'adsl':[a],'ours':[b]},protocol_id=PROTOCOL)
    assert [r['N'] for r in out['table']]==[1,2]
    assert [r['nsv'] for r in out['table']]==[.1,.1]
    assert all(r['structural_connectivity_rate']==1 for r in out['table'])
    assert physical_table_row(a,protocol_id=PROTOCOL)['raw_ivr'] is None
    assert physical_table_row(b,protocol_id=PROTOCOL)['raw_ivr']==.002
    assert [a,b]==before


def test_graph_failure_and_internal_multicomponent_diagnostics_are_separate():
    a=measured('disconnected',internal=False,materials=2,groups=2);a['applicable']=False
    b=measured('nearby_materials',internal=False,materials=2)
    c=measured('unknown');c['connectivity'].update(status='UNAVAILABLE',overall_pass=None,
        internal_connected=None,graph_component_count=None,reason_codes=['DISTANCE_UNAVAILABLE'])
    d=measured('good')
    out=summarize_physical_methods({'ours':[a,b,c,d]},protocol_id=PROTOCOL)
    con=out['samples']['ours']['connectivity']
    assert con['planned_total']==4 and con['success_rate']==.5
    assert con['graph_disconnected_count']==1 and con['internal_multicomponent_case_count']==2
    assert con['unavailable_count']==1 and con['terminal_failure_count']==2
    assert out['samples']['ours']['standing']['planned_total']==3
    assert con['cases'][1]['evaluation_outcome']=='PASS'
    assert con['cases'][1]['reason_codes']==[]
    assert con['cases'][3]['reason_codes']==[]
    assert con['cases'][3]['graph_component_count']==1
    assert con['cases'][3]['internal_connected'] is True


def test_pending_invalidates_rate_and_ns_samples_even_if_stale_measurements_present():
    r=measured();r['state']='PENDING'
    out=summarize_physical_methods({'ours':[r]},protocol_id=PROTOCOL)
    assert out['status']=='INCOMPLETE'
    assert out['table'][0]['structural_connectivity_rate'] is None
    assert out['common_measurable_case_ids']['N']==[]


def test_online_failures_do_not_remove_measured_geometry():
    r=measured();r['checker']['status']='FAIL'
    r['checker']['metrics']['standing_observation'].update(status='FAIL',observed_tipping=True)
    out=summarize_physical_methods({'ours':[r]},protocol_id=PROTOCOL)
    assert out['table'][0]['standing_success_rate']==0
    assert out['table'][0]['structural_connectivity_rate']==1
    assert out['table'][0]['nsv']==.1


def test_invalid_success_reason_or_count_is_rejected():
    r=measured();r['connectivity']['reason_codes']=['GEOMETRIC_GRAPH_DISCONNECTED']
    with pytest.raises(ValueError,match='reason'):
        physical_table_row(r,protocol_id=PROTOCOL)
    r=measured();r['support_evaluation']['part_count']=2
    with pytest.raises(ValueError,match='count'):
        physical_table_row(r,protocol_id=PROTOCOL)


@pytest.mark.parametrize('field,value', [
    ('material_component_count',0), ('material_component_count',True),
    ('material_component_count',1.5), ('material_component_count',None),
    ('graph_component_count',0), ('graph_component_count',True),
    ('graph_component_count',2), ('graph_component_count',None),
    ('overall_pass',False),
])
def test_invalid_graph_success_evidence_is_rejected(field,value):
    r=measured();r['connectivity'][field]=value
    with pytest.raises(ValueError,match='success evidence'):
        physical_table_row(r,protocol_id=PROTOCOL)


def test_material_count_does_not_replace_print_part_count_or_nsv_sample():
    whole=measured(internal=False,materials=2)
    split=measured(count=2,materials=2)
    before=deepcopy([whole,split])
    out=summarize_physical_methods({'whole':[whole],'split':[split]},protocol_id=PROTOCOL)
    assert [r['N'] for r in out['table']]==[1,2]
    assert all(r['structural_connectivity_rate']==1 for r in out['table'])
    assert out['common_measurable_case_ids']=={'nsv':['one'],'N':['one']}
    assert all(r['standing_success_rate']==1 and r['nsv']==.1 for r in out['table'])
    assert [whole,split]==before
    whole['connectivity']['part_count']=2
    with pytest.raises(ValueError,match='print-part count mismatch'):
        physical_table_row(whole,protocol_id=PROTOCOL)


@pytest.mark.parametrize('old_protocol',[
    'print_plan_structural_connectivity_v1','geometric_proximity_connectivity_v1'])
def test_old_connectivity_protocol_cannot_be_relabelled_by_summary(old_protocol):
    r=measured()
    r['connectivity']['protocol_id']=old_protocol
    with pytest.raises(ValueError,match='protocol mismatch'):
        summarize_physical_methods({'old':[r]},protocol_id=PROTOCOL)
