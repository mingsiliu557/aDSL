import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from agents.tool_context import ToolContext

from adsl.agents.checkers import CheckerRun
from adsl.agents.models import AnalysisContext, CheckerFinding, CheckerResult, CheckerSpec, MetricEvidence
from adsl.agents.service import ObjectWorkflow
from adsl.agents.tools.context import AgentToolContext
from adsl.agents.tools.files import READ_FILE_MAX_CHARS, read_file


def invoke(tmp_path, **arguments):
    context = AgentToolContext(workspace=tmp_path, source_path=tmp_path / 'source.py')
    wire = json.dumps(arguments)
    wrapper = ToolContext(context, tool_name='read_file', tool_call_id='test', tool_arguments=wire)
    result = asyncio.run(read_file.on_invoke_tool(wrapper, wire))
    return result, context


def test_small_file_old_arguments_and_eof(tmp_path):
    (tmp_path / 'source.py').write_text('scene = None\n')
    text, context = invoke(tmp_path, path='source.py')
    assert text == 'scene = None\n'
    assert context.events[0].tool == 'read_file'
    assert context.events[0].path == 'source.py'
    text, _ = invoke(tmp_path, path='source.py', offset=100)
    assert 'returned_chars=0' in text
    assert 'next_offset=none (EOF)' in text


@pytest.mark.parametrize('filename', ['report.json', 'source.py'])
def test_long_unicode_line_is_bounded_and_pages_without_loss(tmp_path, filename):
    original = '中文α🙂' * 7501
    (tmp_path / filename).write_text(original, encoding='utf-8')
    chunks = []
    offset = 0
    while offset < len(original):
        text, _ = invoke(tmp_path, path=filename, offset=offset, max_chars=10**9)
        header, chunk = text.split('\n', 1)
        assert len(chunk) <= READ_FILE_MAX_CHARS
        assert len(text) < READ_FILE_MAX_CHARS + 200
        chunks.append(chunk)
        offset += len(chunk)
        if offset < len(original):
            assert 'truncated=true' in header
            assert f'next_offset={offset}' in header
        else:
            assert 'truncated=false' in header
            assert 'next_offset=none (EOF)' in header
    assert ''.join(chunks) == original


def test_default_limit_and_explicit_smaller_page(tmp_path):
    (tmp_path / 'report.json').write_text('a' * (READ_FILE_MAX_CHARS + 1))
    text, _ = invoke(tmp_path, path='report.json')
    assert len(text.split('\n', 1)[1]) == READ_FILE_MAX_CHARS
    text, _ = invoke(tmp_path, path='report.json', offset=5, max_chars=7)
    assert text.split('\n', 1)[1] == 'a' * 7
    assert 'next_offset=12' in text


def test_json_pointer_returns_only_requested_field(tmp_path):
    payload = {'large_unrelated': 'DO_NOT_READ' * 10000, 'findings': [{'metric': {'value':60, 'threshold':50}}], 'a/b': {'~key': [3]}}
    (tmp_path / 'report.json').write_text(json.dumps(payload))
    text, _ = invoke(tmp_path, path='report.json', json_pointer='/findings/0/metric')
    assert json.loads(text) == {'value':60, 'threshold':50}
    assert 'DO_NOT_READ' not in text
    text, _ = invoke(tmp_path, path='report.json', json_pointer='/a~1b/~0key/0')
    assert json.loads(text) == 3
    text, _ = invoke(tmp_path, path='report.json', json_pointer='/large_unrelated')
    assert 'truncated=true' in text.split('\n', 1)[0]
    assert len(text) < READ_FILE_MAX_CHARS + 200


@pytest.mark.parametrize('arguments', [
    {'path':'source.py', 'offset':-1},
    {'path':'source.py', 'max_chars':0},
    {'path':'source.py', 'max_chars':-1},
    {'path':'../outside.txt'},
])
def test_invalid_read_arguments_and_workspace_escape(tmp_path, arguments):
    (tmp_path / 'source.py').write_text('x')
    with pytest.raises(ValueError):
        invoke(tmp_path, **arguments)


def test_candidate_coder_receives_compact_feedback(tmp_path, monkeypatch):
    import adsl.agents.service as service
    root = tmp_path / 'rounds/round_01'
    candidate = root / 'candidates/01'
    candidate.mkdir(parents=True)
    source = tmp_path / 'source.py'
    source.write_text('scene=None')
    candidate_source = candidate / 'source.py'
    candidate_source.write_text('scene=None')
    proposal = SimpleNamespace(proposal_id='repair-seat', finding_ids=['fea:stress:1'], model_dump=lambda: {'proposal_id':'repair-seat'})
    controller = SimpleNamespace(
        budget_error=lambda: None,
        normalize_proposal=lambda p: (p, []),
        prepare_candidate=lambda *args: (candidate, candidate_source, 'fingerprint'),
        record=lambda row: None,
    )
    monkeypatch.setattr(service, 'RepairController', lambda **kwargs: controller)
    finding = CheckerFinding(
        finding_id='fea:stress:1', rule_id='STRESS', category='physical_violation',
        metric=MetricEvidence(name='stress', value=60, threshold=50, comparator='le'),
        domain={'large_raw_report':'DO_NOT_INLINE' * 10000},
    )
    result = CheckerResult(checker='fea', status='FAIL', summary='stress high',
        metrics={'huge_mesh':'DO_NOT_INLINE' * 10000}, findings=[finding, finding.model_copy(update={'finding_id':'fea:other:2', 'message':'UNRELATED_EVIDENCE'})])
    run = CheckerRun(CheckerSpec(name='fea', command=['unused']), result, root / 'checkers/fea', ())
    other_run = CheckerRun(CheckerSpec(name='standing', command=['unused']),
        CheckerResult(checker='standing', status='FAIL', summary='UNRELATED_CHECKER', findings=[finding.model_copy(update={'finding_id':'standing:tilt:1'})]),
        root / 'checkers/standing', ())
    runtime = SimpleNamespace(run=AsyncMock(side_effect=RuntimeError('stop after capturing API input')))
    workflow = ObjectWorkflow()
    asyncio.run(workflow._attempt_engineering_candidates(
        runtime=runtime, request=SimpleNamespace(requirement='chair', repair_policy=SimpleNamespace(max_candidates_per_round=1)),
        workspace=tmp_path, source_path=source, round_root=root, round_number=1,
        plan=SimpleNamespace(model_dump=lambda: {}), repairer=None, image_critic=None, code_critic=None,
        baseline_execution=None, baseline_runs=[run, other_run],
        baseline_analysis_context=AnalysisContext(source_sha256='a', geometry_sha256='b', checker_specs_sha256='c'),
        source_index=object(), engineering_decision=SimpleNamespace(repair_proposals=[proposal]), code_decision=None,
    ))
    runtime.run.assert_awaited_once()
    wire = runtime.run.call_args.kwargs['input']
    assert 'DO_NOT_INLINE' not in wire
    assert 'UNRELATED_EVIDENCE' not in wire
    assert 'UNRELATED_CHECKER' not in wire
    payload = json.loads(wire)
    assert payload['assigned_source'] == 'rounds/round_01/candidates/01/source.py'
    evidence = payload['checker_evidence']
    assert isinstance(evidence, dict)
    assert evidence['typed_findings'][0]['metric']['threshold'] == 50
    assert len(evidence['typed_findings']) == 1
    assert len(evidence['checker_summary']) == 1
    assert evidence['typed_findings'][0]['result_pointer'] == '/findings/0'
    assert evidence['checker_summary'][0]['result_ref'] == 'rounds/round_01/checkers/fea/result.json'
    assert 'next_offset' in evidence['evidence_access']


def test_critic_gets_all_statuses_but_no_pass_or_nested_raw_evidence(tmp_path):
    from adsl.agents.service import _checker_evidence
    finding = CheckerFinding(finding_id='topology:gap:1', rule_id='GAP', category='physical_violation',
        domain={'gap_m': .01, 'raw': {'large': 'DO_NOT_INLINE'*10000}})
    runs = [CheckerRun(CheckerSpec(name=name, command=['unused']),
        CheckerResult(checker=name, status=status, summary=status, findings=[finding.model_copy(update={'finding_id':name+':1'})]),
        tmp_path / 'checkers' / name, ()) for name, status in [('topology','FAIL'),('standing','PASS')]]
    evidence = _checker_evidence(runs, workspace=tmp_path)
    assert len(evidence['checker_summary']) == 2
    assert len(evidence['typed_findings']) == 1
    assert evidence['typed_findings'][0]['key_values']['gap_m'] == .01
    assert 'DO_NOT_INLINE' not in json.dumps(evidence)
