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
