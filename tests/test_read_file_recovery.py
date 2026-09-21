"""Real file tools; no API or geometry execution."""
import json

import pytest

from adsl.agents.tools.files import read_file, apply_patch
from adsl.agents.tools.context import AgentToolContext
from adsl.agents.overhang_edit import edit_outcome, file_hash
from test_patch_recovery import invoke


def test_missing_evidence_corrected_within_same_candidate(tmp_path):
    source = tmp_path / 'rounds/candidates/one/source.py'
    source.parent.mkdir(parents=True)
    source.write_text('value = 1\n')
    evidence = tmp_path / 'boundary.json'
    evidence.write_text('{"edges": 14}')
    ctx = AgentToolContext(tmp_path, source)
    before = file_hash(source)
    result = json.loads(invoke(read_file, ctx, 'bad', path=str(source.parent / evidence.name)))
    assert result['code'] == 'READ_NOT_FOUND' and not result['ok']
    assert result['resolved_path'] == str(source.parent / evidence.name)
    assert 'workspace' in result['path_basis']
    assert json.loads(invoke(read_file, ctx, 'correct', path=str(evidence)))['edges'] == 14
    current = invoke(read_file, ctx, 'source', path=str(source))
    invoke(apply_patch, ctx, 'patch', path=str(source), old_text=current, new_text='value = 2\n')
    assert [e.success for e in ctx.events] == [False, True, True, True]
    assert edit_outcome('done', ctx.events, before, file_hash(source))['status'] == 'CHANGED'


@pytest.mark.parametrize('args,code', [
    ({'offset': -1}, 'READ_INVALID_ARGUMENT'),
    ({'json_pointer': 'bad'}, 'READ_INVALID_ARGUMENT'),
    ({'json_pointer': '/bad~2escape'}, 'READ_INVALID_ARGUMENT'),
    ({'json_pointer': '/missing'}, 'READ_POINTER_NOT_FOUND'),
    ({'json_pointer': '/items/2'}, 'READ_POINTER_NOT_FOUND'),
    ({'json_pointer': '/items/0/missing'}, 'READ_POINTER_NOT_FOUND'),
])
def test_bad_read_arguments_are_short_recorded_failures(tmp_path, args, code):
    source = tmp_path / 'source.py'; source.write_text('{"items":[1]}')
    ctx = AgentToolContext(tmp_path, source)
    response = json.loads(invoke(read_file, ctx, 'bad', path='source.py', **args))
    assert response['code'] == code and not ctx.events[-1].success
    assert invoke(read_file, ctx, 'good', path='source.py', json_pointer='/items/0') == '1'


def test_severe_errors_and_tool_boundaries_are_not_hidden(tmp_path, monkeypatch):
    source = tmp_path / 'source.py'; source.write_text('{broken')
    ctx = AgentToolContext(tmp_path, source)
    with pytest.raises(json.JSONDecodeError):
        invoke(read_file, ctx, 'broken', path='source.py', json_pointer='/x')
    assert ctx.events[-1].code == 'READ_INVALID_JSON' and not ctx.events[-1].success
    with pytest.raises(ValueError, match='escapes workspace'):
        invoke(read_file, ctx, 'outside', path='../private.txt')
    assert ctx.events[-1].code == 'READ_OUTSIDE_WORKSPACE'
    other = tmp_path / 'report.txt'; other.write_text('report')
    with pytest.raises(ValueError, match='only the assigned source'):
        invoke(apply_patch, ctx, 'other', path='report.txt', old_text='report', new_text='wrong')
    from pathlib import Path
    def denied(*a, **kw): raise PermissionError('permission denied')
    monkeypatch.setattr(Path, 'open', denied)
    with pytest.raises(PermissionError):
        invoke(read_file, ctx, 'denied', path='source.py')
    assert ctx.events[-1].code == 'READ_PERMISSION_DENIED'


@pytest.mark.parametrize('review', ['generation', 'candidate'])
@pytest.mark.parametrize('read_kind', ['failed_source', 'other_report', 'source'])
def test_code_review_requires_successful_assigned_source_read(tmp_path, monkeypatch, review, read_kind):
    import asyncio
    from pathlib import Path
    from types import SimpleNamespace
    from agents.tool_context import ToolContext
    from adsl.agents.models import ObjectRequest, CodeCriticDecision, ImageCriticDecision, RepairProposal, RepairTarget
    from adsl.agents.service import ObjectWorkflow

    source = tmp_path/'source.py'; source.write_text('value = 1')
    report = tmp_path/'report.json'; report.write_text('{}')
    workflow = ObjectWorkflow.__new__(ObjectWorkflow)
    request = ObjectRequest('task', tmp_path, 'case')
    events = []
    async def run(**kw):
        if kw['role'].startswith('image'):
            return SimpleNamespace(final_output=ImageCriticDecision(approved=False, observations=[]))
        ctx = kw['context']
        path = str(report if read_kind == 'other_report' else source)
        wire = json.dumps({'path':path})
        wrapper = ToolContext(ctx,tool_name='read_file',tool_call_id='read',tool_arguments=wire)
        open_file = Path.open
        def missing(target, *a, **opts):
            if target == source: raise FileNotFoundError('mock unavailable source')
            return open_file(target, *a, **opts)
        with monkeypatch.context() as patch:
            if read_kind == 'failed_source': patch.setattr(Path, 'open', missing)
            await read_file.on_invoke_tool(wrapper, wire)
        events.extend(ctx.events)
        return SimpleNamespace(final_output=CodeCriticDecision(approved=True, observations=[]))
    runtime = SimpleNamespace(run=run)
    if review == 'generation':
        decision = asyncio.run(workflow._review_generation_code(runtime=runtime,request=request,
            plan=SimpleNamespace(model_dump=lambda:{}),execution=None,workspace=tmp_path,source_path=source,
            round_number=1,max_rounds=2,round_root=tmp_path,code_critic=None,image_decision=None,code_history=[]))
        approved = decision.approved
    else:
        proposal = RepairProposal(proposal_id='existing',finding_ids=['appearance'],hypothesis='edit',
                                  target=RepairTarget(),action='reshape')
        decision = asyncio.run(workflow._review_candidate_appearance(runtime=runtime,request=request,
            workspace=tmp_path,round_number=1,proposal_index=1,proposal=proposal,baseline_execution=None,
            candidate_execution=None,candidate_source=source,candidate_root=tmp_path,image_critic=None,code_critic=None))
        approved = decision[0]
    assert approved is (read_kind == 'source')
    assert events[0].success is (read_kind != 'failed_source')
