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
    kw=dict(workspace=tmp_path,source_path=source,source_version='original',role='coder',ablation='tracing')
    from adsl.agents import ablation_views
    helper=ablation_views.sanitized_evidence;measures=[]
    def checked(*args,**kwargs):
        before=set(tmp_path.rglob('*'));result=helper(*args,**kwargs)
        if not kwargs.get('write'):
            measures.append(True);assert set(tmp_path.rglob('*'))==before
        return result
    monkeypatch.setattr(ablation_views,'sanitized_evidence',checked)
    first=adapter.payload_for_agent(raw,**kw,tool_context=ctx)
    assert measures
    ref=first['feedback']['report_ref'];doc=json.loads((tmp_path/ref['path']).read_text())
    assert doc['scalar'] is None and doc['value_status']=='NONFINITE'
    assert doc['nested']['values']==[None,[None],{'x':None,'value_status':'KEPT'}]
    assert doc['nested']['value_status']=='NONFINITE'
    assert path.read_bytes()==original
    result=asyncio.run(tool(read_file,ctx,'evidence',path=ref['path'],json_pointer=ref['json_pointer']))
    assert 'ABLATION_READ_DENIED' not in result
    files=set(tmp_path.rglob('*'))
    assert_paths_current(first,ctx,source)
    other=AgentToolContext(tmp_path,source,ablation='tracing')
    assert adapter.payload_for_agent(raw,**kw,tool_context=other)==first
    assert_paths_current(first,other,source)
    assert set(tmp_path.rglob('*'))==files


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



def references(value):
    if isinstance(value,dict):
        if 'path' in value:yield value
        for item in value.values():yield from references(item)
    elif isinstance(value,list):
        for item in value:yield from references(item)


def assert_paths_current(payload,context,source):
    pending=list(references(payload));seen=set()
    while pending:
        ref=pending.pop()
        if not ref.get('path'):continue
        path=context.resolve(ref['path'])
        if path==source.resolve():continue
        assert path in context.sanitized_evidence_paths and path.is_file()
        if path not in seen:
            seen.add(path);pending.extend(references(json.loads(path.read_text())))
    assert all(path.is_file() for path in context.sanitized_evidence_paths)


def bound_evidence(tmp_path):
    source=tmp_path/'source.py';source.write_text('original')
    digest=hashlib.sha256(source.read_bytes()).hexdigest()
    inner=tmp_path/'inner.json';inner.write_text('{"metric":0.2}')
    binding={'source_sha256':digest,'source_version':'original','matches_current_source':True,'source_binding':'CURRENT'}
    path=tmp_path/'evidence.json';path.write_text(json.dumps({'metric':.2,'nested_ref':{'path':'inner.json',**binding},'source_ref':{'path':'source.py',**binding}}))
    raw={'feedback':{'report_ref':{'path':'evidence.json',**binding}}}
    return source,digest,raw


def project_fresh(tmp_path,source,payload):
    context=AgentToolContext(tmp_path,source,ablation='tracing')
    out=adapter.payload_for_agent(payload,workspace=tmp_path,source_path=source,
        source_version='original',role='coder',ablation='tracing',tool_context=context)
    assert_paths_current(out,context,source)
    return out,context


def test_tracing_reprojection_rebinds_changed_source(tmp_path):
    source,digest,raw=bound_evidence(tmp_path)
    first,_=project_fresh(tmp_path,source,raw)
    ref=first['feedback']['report_ref']
    assert ref['matches_current_source'] is True and ref['source_binding']=='CURRENT'
    source.write_text('changed')
    out,context=project_fresh(tmp_path,source,first)
    ref=out['feedback']['report_ref']
    assert ref['source_sha256']==digest and ref['source_version']=='original'
    assert ref['matches_current_source'] is False and ref['source_binding']=='STALE'
    for row in references(out):
        if row.get('source_sha256')==digest:assert row.get('matches_current_source') is not True
    for path in context.sanitized_evidence_paths:
        for row in references(json.loads(path.read_text())):
            if row.get('source_sha256')==digest:assert row.get('matches_current_source') is not True


def test_tracing_reprojection_hides_deleted_copy(tmp_path):
    source,digest,raw=bound_evidence(tmp_path)
    keep=tmp_path/'keep.json';keep.write_text('{"metric":0.3}')
    raw['feedback']['evidence_files']=[{'path':'keep.json','source_sha256':digest}]
    first,_=project_fresh(tmp_path,source,raw)
    removed=tmp_path/first['feedback']['report_ref']['path'];removed.unlink()
    out,context=project_fresh(tmp_path,source,first)
    ref=out['feedback']['report_ref']
    assert ref['path'] is None and ref['availability']=='UNAVAILABLE' and ref['reason']=='hidden_by_ablation'
    assert removed.resolve() not in context.sanitized_evidence_paths
    assert out['feedback']['evidence_files'][0]['path']
