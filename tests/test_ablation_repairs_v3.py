"""Three v3 regressions with scripted execution, no model or native checker."""
from copy import deepcopy
from dataclasses import replace
import asyncio
import hashlib
import json
from pathlib import Path
import numpy as np
import pytest
from adsl.agents import fixed_assembly as flow, agent_feedback as adapter
from adsl.agents.ablation_views import sanitized_evidence
from adsl.agents.tools.context import AgentToolContext
from test_fixed_assembly import mock_flow,run_flow


def sequence(tmp_path,monkeypatch,change,*,initial_error=False,interrupt=False):
    values=[('PASS',False)]*(2 if initial_error else 3)+[('PASS',True)]
    state=mock_flow(tmp_path,monkeypatch,values)
    req=replace(state[1],max_rounds=4,checker_specs=(),fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'})
    state=(state[0],req,*state[2:])
    execute=flow.execute_asset_source
    calls=[]
    def exported(source,out,**kw):
        n=len(calls)+1;calls.append(n)
        if initial_error and n==1:
            raise flow.AssetExecutionError('source blocked',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
                stage='source_execution',exception_type='ValueError',message='stub failure',
                source_sha256=flow.file_hash(source),source_locations=[]))
        result=execute(source,out,**kw)
        path=out/'assembly/assembly_manifest.json';data=json.loads(path.read_text())
        data['part_declarations']=[{'id':'body','components':['crossbar','stem']}]
        data['parts']=[{'id':'body','print_transform_mm':np.eye(4).tolist()}]
        if n in ([3] if initial_error else [2,3]):
            if change=='group':data['part_declarations'][0]['components']=['crossbar']
            else:data['parts'][0]['print_transform_mm'][0][0]=-1
        path.write_text(json.dumps(data));return result
    monkeypatch.setattr(flow,'execute_asset_source',exported)
    writes=flow.write_json; snapshots=[];interrupted=[]
    def save(path,data):
        writes(path,data)
        if Path(path).name=='assembly_versions.json':
            snapshots.append(deepcopy(data))
            if interrupt and not interrupted and data.get('working')=='attempt_0001' and data.get('next_round')==3:
                interrupted.append(True);raise KeyboardInterrupt('persisted rejection')
    monkeypatch.setattr(flow,'write_json',save)
    if interrupt:
        with pytest.raises(KeyboardInterrupt):run_flow(state)
    result,book=run_flow(state)
    return result,book,state[-1],snapshots


@pytest.mark.parametrize('change',['group','orientation'])
@pytest.mark.parametrize('interrupt',[False,True])
def test_fixed_baseline_rejects_cross_round_bypass_and_resume(tmp_path,monkeypatch,change,interrupt):
    result,book,calls,_=sequence(tmp_path,monkeypatch,change,interrupt=interrupt)
    assert result.approved and book['retained']=='attempt_0003'
    assert book['actions_structure_baseline']=='original'
    field='grouping_changed' if change=='group' else 'orientation_changed'
    for version in ('attempt_0001','attempt_0002'):
        row=book['versions'][version]['reviews']['structured_action_check']
        assert row['baseline_version']=='original' and row[field]==['body'] and row['conclusion']=='REJECT'
    assert not book['versions']['attempt_0003']['reviews'].get('structured_action_check')
    for call in calls[1:]:
        feedback=call['payload']['feedback']
        assert feedback['structured_action_violation'][field]==['body']
        assert 'original' in feedback['primary_objective'] and 'body' in feedback['primary_objective']
        assert 'Implement the authorized print-group declarations' not in feedback['primary_objective']


def test_first_successful_export_establishes_baseline_after_source_failure(tmp_path,monkeypatch):
    result,book,calls,snapshots=sequence(tmp_path,monkeypatch,'group',initial_error=True)
    assert result.approved and book['actions_structure_baseline']=='attempt_0001'
    assert not any(row.get('actions_structure_baseline') for row in snapshots if row.get('working')=='original')
    assert 'structured_action_check' not in book['versions']['attempt_0001']['reviews']
    assert book['versions']['attempt_0002']['reviews']['structured_action_check']['grouping_changed']==['body']


def test_structure_diff_connections_and_invalid_records():
    base={'part_declarations':[{'id':'body','components':['a','b']}],
          'parts':[{'id':'body','print_transform_mm':np.eye(4).tolist()}], 'connections':[]}
    candidate=deepcopy(base);candidate['connections']=[{'id':'new'}]
    assert flow._actions_structure_diff(base,candidate)==dict(grouping_changed=[],orientation_changed=[],invalid=False)
    candidate['parts'][0]['print_transform_mm']=[[float('nan')]]
    assert flow._actions_structure_diff(base,candidate)['invalid']
    assert flow._actions_structure_diff(base,{})['invalid']


def evidence(tmp_path):
    source=tmp_path/'source.py';source.write_text('stub')
    raw={'scalar':float('nan'),'nested':{'values':[float('inf'),[float('-inf')],{'x':float('nan'),'value_status':'KEPT'}]},
         'findings':[{'finding_id':'f','source_candidates':[{'source_ids':['line:7']}],'metric':.2}]}
    path=tmp_path/'evidence.json';path.write_text(json.dumps(raw))
    payload={'feedback':{'report_ref':{'path':'evidence.json','json_pointer':'/nested/values'}}}
    return source,path,payload


def test_nonfinite_nested_evidence_and_memory_measurement(tmp_path,monkeypatch):
    from adsl.agents.tools.files import read_file
    from test_assembly_feedback_recovery import tool
    source,path,raw=evidence(tmp_path);original=path.read_bytes()
    paths=set(tmp_path.rglob('*'))
    projected=sanitized_evidence(raw,workspace=tmp_path,source_path=source)
    assert set(tmp_path.rglob('*'))==paths
    ctx=AgentToolContext(tmp_path,source,ablation='tracing')
    kw=dict(workspace=tmp_path,source_path=source,source_version='original',role='coder',ablation='tracing',tool_context=ctx)
    from adsl.agents import ablation_views
    helper=ablation_views.sanitized_evidence;measures=[]
    def checked(*args,**kwargs):
        before=set(tmp_path.rglob('*'));result=helper(*args,**kwargs)
        if not kwargs.get('write'):
            measures.append(True);assert set(tmp_path.rglob('*'))==before
        return result
    monkeypatch.setattr(ablation_views,'sanitized_evidence',checked)
    first=adapter.payload_for_agent(raw,**kw)
    assert measures
    ref=first['feedback']['report_ref'];doc=json.loads((tmp_path/ref['path']).read_text())
    assert doc['scalar'] is None and doc['value_status']=='NONFINITE'
    assert doc['nested']['values']==[None,[None],{'x':None,'value_status':'KEPT'}]
    assert doc['nested']['value_status']=='NONFINITE'
    assert path.read_bytes()==original
    result=asyncio.run(tool(read_file,ctx,'evidence',path=ref['path'],json_pointer=ref['json_pointer']))
    assert 'ABLATION_READ_DENIED' not in result
    files=set(tmp_path.rglob('*'))
    assert adapter.payload_for_agent(raw,**kw)==first
    assert adapter.payload_for_agent(first,**kw)==first
    assert set(tmp_path.rglob('*'))==files


def test_explicit_marker_is_required_and_invalidated_by_new_feedback(tmp_path,monkeypatch):
    source,path,raw=evidence(tmp_path)
    kw=dict(workspace=tmp_path,source_path=source,source_version='original',role='coder',ablation='tracing')
    first=adapter.payload_for_agent(raw,**kw)
    assert first['ablation_view']==dict(variant='tracing',sha256=hashlib.sha256(adapter._encoded({k:v for k,v in first.items() if k!='ablation_view'})).hexdigest())
    constructor=adapter._Projection.__init__;calls=[]
    def init(self,*a,**kw):calls.append(1);constructor(self,*a,**kw)
    monkeypatch.setattr(adapter._Projection,'__init__',init)
    assert adapter.payload_for_agent(first,**kw)==first and not calls
    modified=deepcopy(first);modified['feedback']['repair_history']=[{'action':'reshape','reason':'new '+str(i)} for i in range(20)]
    after=adapter.payload_for_agent(modified,**kw)
    assert len(calls)==1 and len(after['feedback']['repair_history'])<=3
    assert after['ablation_view']!=first['ablation_view']
    unmarked=deepcopy(first);unmarked.pop('ablation_view')
    adapter.payload_for_agent(unmarked,**kw)
    assert len(calls)==2 and 'ablation_view' not in raw and 'ablation_view' not in raw['feedback']


def test_support_restore_keeps_structure_baseline(tmp_path,monkeypatch):
    from test_support_workflow import d_fixture
    state,*_=d_fixture(tmp_path,monkeypatch,[12,12])
    req=replace(state[1],fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'})
    _,book=run_flow((state[0],req,*state[2:]))
    assert book['retained']=='original' and book['actions_structure_baseline']=='original'
    feedback=flow._restore_support_baseline(book,baseline_id='original',stop_reason='stub')
    assert book['actions_structure_baseline']=='original'
    baseline=book['versions']['original']['reviews']['geometry'];candidate=deepcopy(baseline)
    candidate['parts'][0]['print_transform_mm'][0][0]=-1
    assert flow._actions_structure_diff(baseline,candidate)['orientation_changed']==['body']



def test_source_failure_instruction_precedes_structure_restore(tmp_path,monkeypatch):
    state=mock_flow(tmp_path,monkeypatch,[('PASS',False),('PASS',True)])
    req=replace(state[1],max_rounds=3,checker_specs=(),fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'})
    state=(state[0],req,*state[2:]);executor=flow.execute_asset_source;executed=[]
    def execute(src,out,**kw):
        executed.append(1)
        if len(executed)==2:
            raise flow.AssetExecutionError('broken source',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
                stage='source_execution',exception_type='ValueError',message='stub failure',
                source_sha256=flow.file_hash(src),source_locations=[]))
        result=executor(src,out,**kw)
        path=out/'assembly/assembly_manifest.json';data=json.loads(path.read_text())
        data.update(part_declarations=[{'id':'body','components':['crossbar','stem']}],
                    parts=[{'id':'body','print_transform_mm':np.eye(4).tolist()}])
        path.write_text(json.dumps(data));return result
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    _,book=run_flow(state)
    assert book['actions_structure_baseline']=='original'
    objective=state[-1][1]['payload']['feedback']['primary_objective']
    assert objective.startswith('Fix the bound generated-source API/runtime error first.')
    assert 'Restore print-part grouping' in objective and 'original' in objective
    assert 'Implement the authorized print-group declarations' not in objective
