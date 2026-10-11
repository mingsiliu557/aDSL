"""Generated-source error contracts; synthetic Python only, no mesh/model calls."""
import json
import subprocess
import sys
from dataclasses import replace
from pathlib import Path

import pytest

from adsl.agents.agent_feedback import payload_for_agent
from adsl.agents.feedback_schema import execution_failure_summary, sha256_file
from adsl.agents.utils.execution import AssetExecutionError, execute_asset_source


@pytest.mark.parametrize('body,kind,stage', [
    ('raise ValueError("positions must be finite and strictly increasing")', 'ValueError', 'source_execution'),
    ('class Legs: pass\nLegs().children', 'AttributeError', 'source_execution'),
    ('scene = 1', 'TypeError', 'source_contract'),
])
def test_real_child_source_error_arrives_as_bounded_diagnostic(tmp_path, body, kind, stage):
    source = tmp_path/'source.py'; source.write_text(body)
    with pytest.raises(AssetExecutionError) as caught:
        execute_asset_source(source,tmp_path/'out',render=False,export_urdf=False)
    diagnostic = caught.value.diagnostic
    assert diagnostic['exception_type'] == kind and diagnostic['stage'] == stage
    assert diagnostic['source_sha256'] == sha256_file(source)
    assert diagnostic['source_locations'] if stage == 'source_execution' else not diagnostic['source_locations']
    raw = json.loads((tmp_path/'out/source_execution_error.json').read_text())
    assert 'Traceback' in raw['traceback']
    current = execution_failure_summary(raw,source_path=source,source_version='original')
    current['evidence_ref'] = dict(path=str(tmp_path/'out/source_execution_error.json'),json_pointer='',
        source_sha256=sha256_file(source),source_version='original')
    payload = {'feedback':{'execution_failure':current, 'failures':[current]},
               'source_sha256':sha256_file(source)}
    projected = payload_for_agent(payload,workspace=tmp_path,source_path=source,source_version='original',role='coder')
    assert projected['evaluation_feedback']['unique_failure_count'] == 1
    group = projected['evaluation_feedback']['failures'][0]
    assert group['exception_type'] == kind and group['message'] == current['message']
    assert group['repair_target'] == 'generated_source'
    assert 'Traceback' not in json.dumps(projected)
    assert payload_for_agent(projected,workspace=tmp_path,source_path=source,source_version='original',role='coder') == projected
    source.write_text('different')
    assert execution_failure_summary(raw,source_path=source) is None


def test_import_dependency_is_not_classified_as_source_repair(tmp_path):
    source=tmp_path/'source.py';source.write_text('import module_that_does_not_exist')
    with pytest.raises(AssetExecutionError) as caught:
        execute_asset_source(source,tmp_path/'out',render=False,export_urdf=False)
    assert caught.value.diagnostic is None


def test_source_error_skips_critics_and_reaches_existing_coder_loop(tmp_path, monkeypatch):
    from test_fixed_assembly import mock_flow, run_flow
    from adsl.agents import fixed_assembly as flow
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=2),*state[2:])
    original_execute=flow.execute_asset_source
    calls=[]
    def execute(source,out,**kwargs):
        calls.append(source)
        if len(calls)==1:
            raise AssetExecutionError('full stderr',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
                stage='source_execution', exception_type='AttributeError',message="no attribute 'children'",
                source_sha256=sha256_file(source),source_locations=[dict(path=str(source),line=1,function='<module>')]))
        return original_execute(source,out,**kwargs)
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    review=state[0]._review_generation_code
    async def code(**kwargs):
        assert kwargs['round_number'] == 2
        return await review(**kwargs)
    monkeypatch.setattr(state[0],'_review_generation_code',code)
    result,book=run_flow(state)
    assert result.approved and len(state[-1]) == 1 and len(calls)==2
    first=book['versions']['original']['reviews']
    assert first['image_review_status'] == first['code_review_status'] == 'SKIPPED_SOURCE_EXECUTION_FAILED'
    feedback=state[-1][0]['payload']['feedback']
    assert feedback['execution_failure']['exception_type'] == 'AttributeError'
    assert feedback['execution_failure']['source_version'] == 'original'
    assert 'API/runtime error first' in feedback['primary_objective']
    assert not first['geometry']['failures'][0].get('geometry_repair_allowed',False)


def test_exact_failed_source_repeat_consumes_edit_but_not_execution(tmp_path,monkeypatch):
    from test_fixed_assembly import mock_flow,run_flow
    from adsl.agents import fixed_assembly as flow
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=4),*state[2:])
    original_execute=flow.execute_asset_source
    executed=[]
    def execute(source,out,**kwargs):
        executed.append(source.read_text())
        if source.read_text() in {'original','B'}:
            raise AssetExecutionError('bad api',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
                stage='source_execution',exception_type='ValueError',message='bad api',
                source_sha256=sha256_file(source),source_locations=[]))
        return original_execute(source,out,**kwargs)
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    edits=[]
    async def repair(**kwargs):
        edits.append(kwargs)
        kwargs['source_path'].write_text(['B','original','C'][len(edits)-1])
        return {'status':'CHANGED'}
    monkeypatch.setattr(state[0],'_repair',repair)
    result,book=run_flow(state)
    assert result.approved and executed==['original','B','C'] and len(edits)==3
    repeated=book['versions']['attempt_0002']['reviews']['duplicate_source']
    assert repeated['duplicate_of_version']=='original' and repeated['executed'] is False
    assert Path(edits[-1]['payload']['assembly_context']['report_ref']['path']).is_file()
    assert edits[-1]['payload']['source_version']=='attempt_0001'
    assert edits[-1]['payload']['feedback']['duplicate_source']==repeated
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines())==3
    run_flow(state)
    assert len(edits)==3 and executed==['original','B','C']


def test_repeat_guard_requires_deterministic_failure_and_identical_bindings(tmp_path):
    from adsl.agents.fixed_assembly import _previous_failed_source
    from adsl.agents.overhang_edit import version_record
    source=tmp_path/'source.py';source.write_text('A')
    binding=dict(source_sha256=sha256_file(source),execution_config_sha256='config',relevant_input_hashes={'image':'1'})
    def book(failure):
        return {'versions':{'original':version_record('original',source,None,reviews=dict(
            execution_binding=binding,geometry={'source_sha256':sha256_file(source),'failures':[failure]}))}}
    transient=book({'code':'EXECUTION_UNAVAILABLE','stage':'external_executor'})
    assert _previous_failed_source(transient,binding) is None
    physical=book({'code':'DISCONNECTED_PRINT_PART'})
    assert _previous_failed_source(physical,binding)['id']=='original'
    assert _previous_failed_source(physical,{**binding,'execution_config_sha256':'other'}) is None
    assert _previous_failed_source(physical,{**binding,'relevant_input_hashes':{'image':'2'}}) is None


def test_wrong_hash_and_library_frames_never_bind_source_failure(tmp_path):
    source=tmp_path/'source.py';source.write_text('source')
    raw=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',stage='source_execution',
        exception_type='ValueError',message='failed',source_sha256='old',source_locations=[])
    assert execution_failure_summary(raw,source_path=source) is None
    raw['source_sha256']=sha256_file(source)
    raw['source_locations']=[dict(path='/library/loft.py',line=8,function='loft')]
    assert execution_failure_summary(raw,source_path=source) is None
    raw['source_locations']=[];raw['stage']='export'
    assert execution_failure_summary(raw,source_path=source) is None


def test_source_failure_does_not_start_configured_physical_checks(tmp_path,monkeypatch):
    from test_fixed_assembly import mock_flow,run_flow
    from adsl.agents import fixed_assembly as flow,assembly_physics,assembly_topology
    state=mock_flow(tmp_path,monkeypatch,[])
    specs=(assembly_topology.checker_spec(),assembly_physics.checker_spec('assembly_overhang'),
           assembly_physics.checker_spec('assembly_standing'))
    state=(state[0],replace(state[1],checker_specs=specs,max_rounds=1),*state[2:])
    def execute(source,out,**kw):
        raise AssetExecutionError('bad source',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
            stage='source_execution',exception_type='ValueError',message='bad source',
            source_sha256=sha256_file(source),source_locations=[]))
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    monkeypatch.setattr(assembly_physics,'run_assembly_checks',lambda *a,**k:pytest.fail('Physical process cannot run'))
    result,book=run_flow(state)
    assert not result.approved and state[-1]==[]
    reviews=book['versions']['original']['reviews']
    for spec in specs:
        assert reviews[spec.name]['status']=='INDETERMINATE'
        assert reviews[spec.name]['assumptions']['reason']=='SOURCE_EXECUTION_FAILED'
        assert reviews[spec.name]['metrics']=={}


def test_source_error_reaches_real_coder_adapter_and_file_tools(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from test_fixed_assembly import mock_flow,run_flow
    from test_assembly_feedback_recovery import payload,tool
    from adsl.agents import fixed_assembly as flow
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.tools.files import read_file,apply_patch
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=2),*state[2:])
    monkeypatch.setattr(state[0],'_repair',ObjectWorkflow._repair.__get__(state[0]))
    original_execute=flow.execute_asset_source
    executions=[];captured=[]
    def execute(source,out,**kw):
        executions.append(source.read_text())
        if len(executions)==1:
            raise AssetExecutionError('full stderr',diagnostic=dict(code='GENERATED_SOURCE_EXECUTION_FAILED',
                stage='source_execution',exception_type='AttributeError',message="no attribute 'children'",
                source_sha256=sha256_file(source),source_locations=[dict(path=str(source),line=1,function='<module>')]))
        return original_execute(source,out,**kw)
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    async def runtime(**kw):
        data=payload(kw);captured.append(data)
        assert kw['role'].startswith('coder:assembly:')
        group,=data['evaluation_feedback']['failures']
        assert group['exception_type']=='AttributeError' and group['repair_target']=='generated_source'
        assert group['message']=="no attribute 'children'"
        assert group['physical_verdict']=='NOT_EVALUATED' and not group.get('geometry_repair_allowed')
        evidence=next(r for r in group['evidence_refs'] if r['availability']=='AVAILABLE')
        detail=json.loads(await tool(read_file,kw['context'],'error_detail',path=evidence['path'],json_pointer=evidence['json_pointer']))
        assert detail['exception_type']=='AttributeError' and detail['source_sha256']==sha256_file(kw['context'].source_path)
        text=await tool(read_file,kw['context'],'source',path=data['assigned_source'])
        await tool(apply_patch,kw['context'],'patch',path=data['assigned_source'],old_text=text,new_text='repaired')
        return SimpleNamespace(final_output='Fixed generated-source API usage; recheck required.')
    state[2].run=runtime
    result,book=run_flow(state)
    assert result.approved and len(captured)==1 and executions==['original','repaired']
    assert book['retained']=='attempt_0001'
    assert book['versions']['original']['reviews']['code_review_status']=='SKIPPED_SOURCE_EXECUTION_FAILED'
    write_path=tmp_path/'captured_source_error_coder.json'
    write_path.write_text(json.dumps(captured[0],allow_nan=False,indent=2))
    run_flow(state)
    assert len(captured)==1 and executions==['original','repaired']
