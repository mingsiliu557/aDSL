import importlib.util
from pathlib import Path
import json
import numpy as np
import pytest
import trimesh
from snap3d.export import export_parts

spec=importlib.util.spec_from_file_location('cpu_batch',Path('scripts/cpu_batch.py'))
batch=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch)


def test_concurrency_obeys_26_gib_and_30_processes():
    for rss in [512*2**20,900*2**20,2*2**30,4*2**30,25*2**30]:
        plan=batch.concurrency_plan(rss)
        assert plan['total_simulation_workers']==plan['case_workers']*plan['cem_workers']
        assert plan['total_simulation_workers']<=30
        assert plan['total_simulation_workers']*rss<=26*2**30
        assert 1<=plan['cem_workers']<=8
    with pytest.raises(ValueError):batch.concurrency_plan(27*2**30)


def test_resume_identity_only_excludes_execution_worker_count():
    config={'cem':{'workers':8,'population':8},'preprocess':{'max_faces_per_case':400000}}
    changed={'cem':{'workers':4,'population':8},'preprocess':{'max_faces_per_case':400000}}
    assert batch.immutable_config(config)==batch.immutable_config(changed)
    changed['preprocess']['max_faces_per_case']=200000
    assert batch.immutable_config(config)!=batch.immutable_config(changed)


def test_completed_case_is_skipped_after_hash_validation(tmp_path,monkeypatch):
    path=tmp_path/'cases/demo';batch.write(path/'case_status.json',{'status':'COMPLETE','wall_seconds':12})
    export_parts({'body':trimesh.creation.box()},path/'assembly/export')
    def forbidden(*a,**k):raise AssertionError('completed case rerun')
    monkeypatch.setattr(batch.subprocess,'Popen',forbidden)
    assert batch.execute_case(tmp_path,{'case_id':'demo'},[0])['status']=='COMPLETE'
    (path/'assembly/export/body.stl').write_bytes(b'changed')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):batch.execute_case(tmp_path,{'case_id':'demo'},[0])


def test_timeout_kills_case_process_group_and_records_reason(tmp_path,monkeypatch):
    batch.write(tmp_path/'cases/demo/case_status.json',{'status':'RUNNING','wall_seconds':14399.})
    class Process:
        pid=12345
        def poll(self):return None
        def wait(self):return -9
    monkeypatch.setattr(batch.subprocess,'Popen',lambda *a,**k:Process())
    values=iter([10.,12.]);monkeypatch.setattr(batch.time,'monotonic',lambda:next(values))
    monkeypatch.setattr(batch.time,'sleep',lambda _:None)
    signals=[];monkeypatch.setattr(batch.os,'killpg',lambda pid,sig:signals.append((pid,sig)))
    result=batch.execute_case(tmp_path,{'case_id':'demo'},[0])
    assert result['status']=='TIMEOUT' and result['reason']=='CASE_WALL_TIME_LIMIT'
    assert [sig for pid,sig in signals]==[batch.signal.SIGTERM,batch.signal.SIGKILL]


def test_summary_preserves_100_denominator_and_marks_pending(tmp_path):
    batch.write(tmp_path/'inputs.json',{'cases':[{'case_id':str(i)} for i in range(100)]})
    result=batch.summarize(tmp_path)
    assert result['case_count']==100 and result['stable_fraction'] is None and result['unfinished']
    assert len(batch.read(tmp_path/'summary_rows.json'))==100


def test_summary_has_stage_metrics_and_unchanged_failure_causes(tmp_path):
    run=tmp_path/'cases/demo/assembly'
    batch.write(run/'simplify/manifest.json',{'metadata':{'total_faces_before':500000,'total_faces_after':400000,'fallback_parts':[],'within_budget':True}})
    batch.write(run/'pge/manifest.json',{'metadata':{'retained_cut_count':3,'rolled_back_cut_count':1,'residual_overlap_volume_mm3':.2}})
    batch.write(run/'cgr/contact.json',{'connectors':[{'status':'PLACED'},{'status':'SKIPPED'}],'pairs':[{'connector_status':'SKIPPED'}]})
    result=batch.case_summary(tmp_path,'demo',{'status':'FAILED','reason':'SIMULATION_ERROR'})
    assert result['simplify_total_faces_after']==400000
    assert result['pge_rolled_back_cut_count']==1 and result['connectors_skipped']==2
    assert result['final_stable'] is None and result['reason']=='SIMULATION_ERROR'


def test_low_memory_waits_only_before_dispatch(tmp_path,monkeypatch):
    batch.STOP_EVENT.clear();values=iter([3*2**30,5*2**30]);waits=[]
    monkeypatch.setattr(batch,'memory_availability',lambda:dict(available_bytes=next(values)))
    monkeypatch.setattr(batch.STOP_EVENT,'wait',lambda seconds:waits.append(seconds))
    assert batch.wait_for_memory(tmp_path,'demo') is True
    assert waits==[5.] and batch.read(tmp_path/'memory_dispatch.json')['paused'] is False


def test_nonmanifold_after_pge_is_classified_without_repair(tmp_path):
    batch.write(tmp_path/'current_stage.json',{'stage':'cgr'});batch.write(tmp_path/'pge/stage.json',{'status':'PASS'})
    error=ValueError('INPUT_GEOMETRY_INVALID');error.diagnostic={'code':'INPUT_GEOMETRY_INVALID','metrics':{'nonmanifold_vertices':6}}
    assert batch.classify_failure(error,tmp_path)=='NONMANIFOLD_AFTER_PGE'
    batch.write(tmp_path/'current_stage.json',{'stage':'repair'})
    assert batch.classify_failure(error,tmp_path)=='INPUT_GEOMETRY_INVALID'


def test_failure_summary_lists_simplification_for_nonmanifold_cases(tmp_path):
    batch.write(tmp_path/'inputs.json',{'cases':[{'case_id':'demo'}]})
    batch.write(tmp_path/'cases/demo/case_status.json',{'status':'FAILED','reason':'NONMANIFOLD_AFTER_PGE'})
    batch.write(tmp_path/'cases/demo/assembly/simplify/manifest.json',{'metadata':{
        'simplification_requested':True,'total_faces_before':500000,'total_faces_after':400000,
        'parts':[{'unchanged':False}],'fallback_parts':[]}})
    result=batch.summarize(tmp_path)
    assert result['nonmanifold_after_pge_count']==1
    assert result['nonmanifold_after_pge_cases'][0]['simplify_applied'] is True
    assert result['failures_by_reason']['NONMANIFOLD_AFTER_PGE']==['demo']


def test_worker_events_record_os_peak_rss_and_pid(tmp_path,monkeypatch):
    from snap3d import pipeline
    monkeypatch.setattr(pipeline,'paper_rollout',lambda *a,**k:dict(status='PASS',wall_seconds=1.,stable=True,global_score=.5))
    monkeypatch.setattr(pipeline,'relative_score',lambda *a,**k:.5)
    job=dict(generation=0,candidate_index=1,family=None,index=0,output=str(tmp_path),parts={},D=150.,
        config={'rollout':{}},fixed_ids=[],model_path=None,a='a',b='b',checks=[])
    pipeline._simulate_candidate(job)
    event=batch.read(tmp_path/'worker_0000.json')
    assert event['status']=='COMPLETE' and event['pid']>0 and event['peak_rss_bytes']>0
