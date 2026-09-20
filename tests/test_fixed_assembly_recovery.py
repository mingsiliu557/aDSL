"""Bounded assembly recovery; mock execution/reviews, no geometry or API calls."""
from dataclasses import replace
import json
from pathlib import Path
import subprocess

import pytest

from adsl.agents import fixed_assembly as flow
from test_fixed_assembly import mock_flow, run_flow


@pytest.mark.parametrize('failure', ['no_manifest', 'broken_manifest', 'empty_error', 'timeout'])
def test_failed_execution_has_readable_feedback_and_can_repair(tmp_path, monkeypatch, failure):
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    state = (state[0], replace(state[1], checker_specs=(), max_rounds=2), *state[2:])
    execute = flow.execute_asset_source
    attempts = []

    def fail_initial(source, out, **kwargs):
        attempts.append(source)
        if len(attempts) == 1:
            if failure == 'broken_manifest':
                manifest = out/'assembly'/'assembly_manifest.json'
                manifest.parent.mkdir(parents=True)
                manifest.write_text('{unfinished')
            if failure == 'timeout':
                raise subprocess.TimeoutExpired(['mock-geometry'], 120)
            raise flow.AssetExecutionError('' if failure == 'empty_error' else 'mock execution failed')
        return execute(source, out, **kwargs)

    monkeypatch.setattr(flow, 'execute_asset_source', fail_initial)
    repair = state[0]._repair

    async def read_feedback_before_repair(**kwargs):
        feedback = kwargs['payload']['feedback']
        path = Path(feedback['report_path'])
        assert path.name == 'execution_error.json'
        diagnostic = json.loads(path.read_text())
        assert diagnostic['type'] in {'AssetExecutionError', 'TimeoutExpired'}
        if failure == 'broken_manifest':
            assert diagnostic['assembly_report_error']
        assert feedback['geometry_status'] == 'ERROR'
        assert feedback['failures'][-1]['code'] == 'EXECUTION_UNAVAILABLE'
        assert Path(feedback['failures'][-1]['report_path']).is_file()
        return await repair(**kwargs)

    monkeypatch.setattr(state[0], '_repair', read_feedback_before_repair)
    result, book = run_flow(state)
    assert result.approved and book['retained'] == 'attempt_0001'
    assert len(state[-1]) == 1 and len(attempts) == 2
    assert (tmp_path/'source.py').read_text() == (tmp_path/'assembly/part.stl').read_text() == 'candidate1'
    assert (tmp_path/'original/source.py').read_text() == 'original'
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines()) == 1


def test_failed_repair_keeps_original_assets_and_completed_resume_does_not_retry(tmp_path, monkeypatch):
    state = mock_flow(tmp_path, monkeypatch, [('PASS', False)])
    state = (state[0], replace(state[1], max_rounds=2), *state[2:])
    execute = flow.execute_asset_source
    attempts = []

    def fail_candidate(source, out, **kwargs):
        attempts.append(source)
        if len(attempts) > 1:
            raise flow.AssetExecutionError('mock candidate failed')
        return execute(source, out, **kwargs)

    monkeypatch.setattr(flow, 'execute_asset_source', fail_candidate)
    result, book = run_flow(state)
    assert not result.approved and book['retained'] == 'original'
    assert book['versions']['attempt_0001']['reviews']['geometry']['status'] == 'ERROR'
    assert Path(book['feedback']['report_path']).is_file()
    assert (tmp_path/'source.py').read_text() == (tmp_path/'scene.glb').read_text() == 'original'
    assert (tmp_path/'assembly/part.stl').read_text() == 'original'
    run_flow(state)
    assert len(state[-1]) == 1 and len(attempts) == 2
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines()) == 1


def test_review_error_stops_and_preserves_readable_flow_diagnostic(tmp_path, monkeypatch):
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])

    async def broken_review(**kwargs):
        raise RuntimeError('mock API error')

    monkeypatch.setattr(state[0], '_review_generation_image', broken_review)
    result, book = run_flow(state)
    assert not result.approved and book['stop_reason'] == 'FLOW_ERROR'
    assert state[-1] == []
    assert json.loads(Path(book['feedback']['report_path']).read_text())['error'] == 'mock API error'
    assert (tmp_path/'source.py').read_text() == (tmp_path/'scene.glb').read_text() == 'original'


def test_existing_geometry_failure_is_not_relabelled_as_unavailable(tmp_path, monkeypatch):
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    state = (state[0], replace(state[1], checker_specs=(), max_rounds=2), *state[2:])
    execute = flow.execute_asset_source
    attempts = []
    findings = [{'code': 'DISCONNECTED_PRINT_PART', 'part_id': 'leg', 'components': 2}]

    def reject_initial_geometry(source, out, **kwargs):
        attempts.append(source)
        if len(attempts) == 1:
            manifest = out/'assembly'/'assembly_manifest.json'
            manifest.parent.mkdir(parents=True)
            manifest.write_text(json.dumps({'status': 'FAIL', 'failures': findings}))
            raise flow.AssetExecutionError('no assembly render produced after geometric rejection')
        return execute(source, out, **kwargs)

    monkeypatch.setattr(flow, 'execute_asset_source', reject_initial_geometry)
    result, book = run_flow(state)
    assert result.approved
    original = book['versions']['original']['reviews']
    assert original['geometry']['status'] == 'FAIL'
    assert original['geometry']['failures'] == findings
    assert original['appearance_approved'] is None
    feedback = state[-1][0]['payload']['feedback']
    assert feedback['geometry_status'] == 'FAIL' and feedback['failures'] == findings
    diagnostic = json.loads(Path(feedback['report_path']).read_text())
    assert Path(diagnostic['assembly_report_path']).is_file()
    assert diagnostic['type'] == 'AssetExecutionError'
