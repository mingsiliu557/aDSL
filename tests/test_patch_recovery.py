import asyncio
import json
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from adsl.agents.tools.context import AgentToolContext, patch_failure
from adsl.agents.tools.files import apply_patch, read_file
from adsl.agents.overhang_edit import edit_outcome, file_hash, reserve_attempt, budget_remaining
from adsl.agents.service import ObjectWorkflow


def invoke(tool, ctx, call_id, **params):
    wire = json.dumps(params)
    wrapper = ToolContext(ctx, tool_name=tool.name, tool_call_id=call_id, tool_arguments=wire)
    return asyncio.run(tool.on_invoke_tool(wrapper, wire))


def setup(tmp_path, text='value = 1\n'):
    source = tmp_path / 'source.py'
    source.write_text(text)
    return source, AgentToolContext(tmp_path, source, patch_scope_id='candidate_1')


@pytest.mark.parametrize('old,count', [('missing', 0), ('a', 2)])
def test_match_errors_leave_file_untouched_and_log_arguments(tmp_path, old, count):
    source, ctx = setup(tmp_path, 'a a\n')
    before, inode = file_hash(source), source.stat().st_ino
    result = json.loads(invoke(apply_patch, ctx, 'call1', path='source.py', old_text=old, new_text='SECRET_NEW_TEXT'))
    assert result['code'] == 'PATCH_MATCH_COUNT' and result['match_count'] == count
    assert result['source_unchanged'] and result['corrections_remaining'] == 2
    assert file_hash(source) == before and source.stat().st_ino == inode
    assert 'SECRET_NEW_TEXT' not in json.dumps(result)
    row = json.loads((tmp_path / result['report_path']).read_text())
    assert row['old_text'] == old and row['new_text'] == 'SECRET_NEW_TEXT'
    assert row['call_id'] == 'call1' and row['candidate_id'] == 'candidate_1'
    assert row['before_sha256'] == row['after_sha256'] == before
    assert ctx.events[-1].success is False


def test_reread_and_correct_same_candidate_no_extra_candidate_charge(tmp_path):
    source, ctx = setup(tmp_path)
    options = {'max_candidates': 2}
    assert reserve_attempt(tmp_path, options, 'initial', attempt_id='candidate_1')
    before = file_hash(source)
    invoke(apply_patch, ctx, 'bad', path='source.py', old_text='value=1', new_text='value=2')
    current = invoke(read_file, ctx, 'read', path='source.py', offset=0, max_chars=100, json_pointer=None)
    assert current == 'value = 1\n'
    invoke(apply_patch, ctx, 'correct', path='source.py', old_text=current, new_text='value = 2\n')
    assert source.read_text() == 'value = 2\n'
    assert patch_failure(ctx.events) is None
    assert edit_outcome('done', ctx.events, before, file_hash(source))['status'] == 'CHANGED'
    ObjectWorkflow._require_tool_event(ctx, 'apply_patch', 'coder')
    assert budget_remaining(tmp_path, options) == 1
    rows = [json.loads(s) for s in next((tmp_path / 'patch_diagnostics').glob('*.jsonl')).read_text().splitlines()]
    assert [r['call_id'] for r in rows] == ['bad', 'correct']
    assert [r['ok'] for r in rows] == [False, True]


def test_three_failures_exhaust_and_new_context_cannot_reset(tmp_path):
    source, ctx = setup(tmp_path)
    before = file_hash(source)
    for i in range(3):
        result = json.loads(invoke(apply_patch, ctx, str(i), path='source.py', old_text='wrong', new_text='changed'))
        assert result['corrections_remaining'] == max(0, 2-i)
    assert patch_failure(ctx.events) == 'PATCH_RETRY_EXHAUSTED'
    assert edit_outcome('NO_CHANGE: done', ctx.events, before, before)['status'] == 'TOOL_ERROR'
    with pytest.raises(RuntimeError, match='PATCH_RETRY_EXHAUSTED'):
        ObjectWorkflow._require_tool_event(ctx, 'apply_patch', 'coder')
    ctx = AgentToolContext(tmp_path, source, patch_scope_id='candidate_1')
    result = json.loads(invoke(apply_patch, ctx, 'four', path='source.py', old_text='value = 1', new_text='value = 2'))
    assert result['code'] == 'PATCH_RETRY_EXHAUSTED' and file_hash(source) == before


def test_third_submission_may_succeed_but_no_later_patch(tmp_path):
    source, ctx = setup(tmp_path)
    before = file_hash(source)
    for i in range(2):
        invoke(apply_patch, ctx, f'bad{i}', path='source.py', old_text='wrong', new_text='changed')
    invoke(apply_patch, ctx, 'good', path='source.py', old_text='value = 1', new_text='value = 2')
    assert edit_outcome('done', ctx.events, before, file_hash(source))['status'] == 'CHANGED'
    result = json.loads(invoke(apply_patch, ctx, 'extra', path='source.py', old_text='value = 2', new_text='value = 3'))
    assert result['retry_exhausted']
    assert source.read_text() == 'value = 2\n'
    assert edit_outcome('done', ctx.events, before, file_hash(source))['status'] == 'TOOL_ERROR'


def test_failed_patch_followed_by_dummy_patch_is_not_recovery(tmp_path):
    source, ctx = setup(tmp_path)
    invoke(apply_patch, ctx, 'bad', path='source.py', old_text='wrong', new_text='changed')
    invoke(apply_patch, ctx, 'noop', path='source.py', old_text='value = 1', new_text='value = 1')
    assert edit_outcome('done', ctx.events, file_hash(source), file_hash(source))['status'] == 'TOOL_ERROR'


def test_atomic_replace_failure_preserves_old_source(tmp_path, monkeypatch):
    import adsl.agents.tools.files as files
    source, ctx = setup(tmp_path)
    before = file_hash(source)
    def denied(*args):
        raise PermissionError('mock replace denied')
    monkeypatch.setattr(files.os, 'replace', denied)
    with pytest.raises(PermissionError):
        invoke(apply_patch, ctx, 'write', path='source.py', old_text='value = 1', new_text='value = 2')
    assert file_hash(source) == before
    assert ctx.events[-1].code == 'PATCH_WRITE_ERROR'
    assert not list(tmp_path.glob('.source.py.patch-*'))
    with pytest.raises(ValueError, match='escapes workspace'):
        invoke(apply_patch, ctx, 'outside', path='../other.py', old_text='value', new_text='wrong')


def test_exhausted_patch_retains_original_assets_in_workflow(tmp_path, monkeypatch):
    from test_overhang_candidate_isolation import fixture, run_case
    f = fixture(tmp_path, monkeypatch, initial=True, actions=(), budget=1)
    original_run = f.kwargs['runtime'].run
    model_turns = []
    async def run(**kw):
        if not kw['role'].startswith('coder'):
            return await original_run(**kw)
        ctx = kw['context']
        for i in range(3):
            wire = json.dumps({'path': ctx.source_path.relative_to(ctx.workspace).as_posix(),
                               'old_text': 'missing exact block', 'new_text': 'replacement'})
            wrapper = ToolContext(ctx, tool_name='apply_patch', tool_call_id=f'call{i}', tool_arguments=wire)
            response = await apply_patch.on_invoke_tool(wrapper, wire)
            model_turns.append(json.loads(response))
        return SimpleNamespace(final_output='stopping after patch errors')
    f.kwargs['runtime'].run = run
    result, book = run_case(f)
    assert len(model_turns) == 3 and len(book['attempts']) == 1
    assert book['retained'] == 'original' and result.glb_path.read_text() == 'GLB:0'
    assert book['attempts']['attempt_0001']['status'] == 'TOOL_ERROR'
