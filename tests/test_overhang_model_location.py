import json
from types import SimpleNamespace

import pytest

from adsl.agents import service
from adsl.agents.models import EngineeringCriticDecision, RepairProposal, RepairTarget, SourceCandidate
from adsl.agents.overhang_edit import inferred_proposal, inferred_scope_unchanged, opportunities
from test_overhang_candidate_isolation import fixture, run_case
from test_overhang_local_edit import measured


def test_missing_index_reads_full_source_and_images_then_edits(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, actions=(90,), budget=1)
    f.original.source_index_path.unlink()
    images = []
    monkeypatch.setattr(service, 'user_input', lambda text, paths: (images.append(tuple(paths)) or text))
    _, book = run_case(f)
    payload = json.loads(next(x for role, x in f.calls if role.startswith('engineering')))
    assert payload['localization_mode'] == 'model_inferred'
    assert payload['current_complete_source'] == (f.workspace / 'original_source.py').read_text()
    assert any(images)
    attempt = book['attempts']['attempt_0001']
    assert attempt['localization']['method'] == 'model_inferred'
    assert attempt['localization']['tool_confirmed'] is False
    assert attempt['localization']['targets'][0]['symbol'] == 'Frame'
    assert attempt['accepted']


@pytest.mark.parametrize('target', ['Missing', 'Protected', 'Frame.missing'])
def test_illegal_inferred_target_rejected(tmp_path, target):
    src = tmp_path / 'source.py'
    src.write_text('class Frame:\n    def build(self):\n        return 1\nclass Protected:\n    pass\n')
    proposal = RepairProposal(proposal_id='p', finding_ids=['overhang:optimization:0'],
        hypothesis='inferred', evidence=['uncertain'], target=RepairTarget(allowed_scopes=[target]), action='reshape')
    normalized, errors = inferred_proposal(proposal, src, {'protection': {'allowed_classes': ['Frame']}}, opportunities(measured()))
    assert normalized is None and errors


def test_method_scope_does_not_authorize_sibling(tmp_path):
    a, b = tmp_path / 'a.py', tmp_path / 'b.py'
    a.write_text('class Frame:\n    def build(self):\n        return 1\n    def other(self):\n        return 2\n')
    b.write_text(a.read_text().replace('return 2', 'return 3'))
    assert not inferred_scope_unchanged(a, b, {'targets': [{'symbol': 'Frame.build'}]})


def test_inference_cannot_bypass_geometry_protection(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, actions=(90,), budget=1)
    f.original.source_index_path.unlink()
    monkeypatch.setattr(service, 'protection_check', lambda *a: {'status': 'FAIL', 'errors': ['protected top changed']})
    _, book = run_case(f)
    assert book['retained'] == 'original' and not book['attempts']['attempt_0001']['accepted']


def test_missing_index_may_decline_without_candidate(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch)
    f.original.source_index_path.unlink()
    runtime = f.kwargs['runtime']
    old = runtime.run
    async def run(**kw):
        if kw['role'].startswith('engineering'):
            assert json.loads(kw['input'])['localization_mode'] == 'model_inferred'
            kw['context'].record('read_file', kw['context'].source_path)
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=True,
                observations=['cannot infer safely'], repair_proposals=[]))
        return await old(**kw)
    runtime.run = run
    result, book = run_case(f)
    assert not book['attempts'] and book['retained'] == 'original' and result.glb_path.exists()
    assert book['stop_reason'] == 'no_actionable_proposal'


def test_reliable_index_keeps_controller_path(tmp_path, monkeypatch):
    f = fixture(tmp_path, monkeypatch, actions=(90,), budget=1)
    old_opportunities = service.opportunities
    def indexed(result):
        return [x.model_copy(update={'source_candidates': [SourceCandidate(
            feature_id='existing', source_ids=['existing-source'], method='geometric', ambiguous=False)]})
            for x in old_opportunities(result)]
    monkeypatch.setattr(service, 'opportunities', indexed)
    calls = []
    monkeypatch.setattr(service.RepairController, 'normalize_proposal', lambda self, p: (calls.append(p) or (p, [])))
    monkeypatch.setattr(service, 'inferred_proposal', lambda *a: pytest.fail('reliable index must not fall back'))
    _, book = run_case(f)
    payload = json.loads(next(x for role, x in f.calls if role.startswith('engineering')))
    assert payload['localization_mode'] == 'index_assisted' and 'current_complete_source' not in payload
    assert calls and book['attempts']['attempt_0001']['localization']['method'] == 'index_assisted'
