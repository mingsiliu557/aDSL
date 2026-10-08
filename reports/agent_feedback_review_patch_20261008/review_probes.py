"""Additional offline review probes. No model, geometry or physical execution."""
from types import SimpleNamespace
import json

import pytest


class RequestFailure(Exception):
    def __init__(self, status, code=None, message='request rejected'):
        super().__init__(message)
        self.status_code = status
        self.body = {'error': {'code': code, 'message': message}}


@pytest.mark.parametrize('shared', [False, True])
def test_critic_request_failure_blocks_engineer_after_checker_regression(tmp_path, monkeypatch, shared):
    from test_assembly_topology import setup_flow
    from test_fixed_assembly import run_flow
    from adsl.agents.models import GradedImageCriticDecision, EngineeringCriticDecision
    state = setup_flow(tmp_path, monkeypatch, ['PASS', 'FAIL'])
    workflow, request, runtime, source, calls = state
    old_code_review = workflow._review_generation_code
    error = RequestFailure(429, 'insufficient_quota') if shared else RequestFailure(400, 'context_too_large')
    followups = []

    async def image(**kw):
        # Round 1 remains a normal appearance rejection with a valid checker.
        return GradedImageCriticDecision(approved=False, observations=[], issues=[{
            'severity': 'HIGH', 'target': None,
            'problem': 'Visible discrepancy', 'suggested_fix': 'repair visual discrepancy'}])

    async def code(**kw):
        if kw['round_number'] == 1:
            return await old_code_review(**kw)
        raise error

    async def engineering(**kw):
        followups.append(kw['role'])
        return SimpleNamespace(final_output=EngineeringCriticDecision(
            approved=False, observations=[], repair_proposals=[]))

    monkeypatch.setattr(workflow, '_review_generation_image', image)
    monkeypatch.setattr(workflow, '_review_generation_code', code)
    runtime.run = engineering
    if shared:
        with pytest.raises(RequestFailure) as caught:
            run_flow(state)
        assert caught.value is error
        book = json.loads((tmp_path/'assembly_versions.json').read_text())
    else:
        _, book = run_flow(state)
        assert book['stop_reason'] == 'agent_input_too_large'
    print({'followups_after_critic_failure': followups,
           'candidate_reason': book['versions']['attempt_0001']['reviews']['reason'],
           'stop_reason': book['stop_reason']})
    assert followups == []


def test_execution_programming_error_is_propagated_after_ledger(tmp_path, monkeypatch):
    from test_fixed_assembly import mock_flow, run_flow
    from adsl.agents import fixed_assembly as flow
    state = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    error = TypeError('deterministic executor programming defect')
    def execute(*args, **kwargs):
        raise error
    monkeypatch.setattr(flow, 'execute_asset_source', execute)
    with pytest.raises(TypeError) as caught:
        run_flow(state)
    assert caught.value is error
    book = json.loads((tmp_path/'assembly_versions.json').read_text())
    assert 'original' in book['versions']


def test_request_classifier_shared_fault_agrees_with_batch_pause():
    from experiments.benchmark_six import run
    # The new classifier recognizes a provider's message-only quota response.
    # The batch pause predicate currently still has a second independent list.
    error = RequestFailure(429, None, 'You exceeded your current quota, please check your plan and billing details')
    classified = run.request_error_helpers().classify_model_request_error(error)
    print({'classification': classified, 'batch_shared_fault': run.shared_fault(error)})
    assert classified['kind'] == 'shared_fault'
    assert run.shared_fault(error)
