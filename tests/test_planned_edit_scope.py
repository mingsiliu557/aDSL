import asyncio
import json
from types import SimpleNamespace
import pytest
from agents.tool_context import ToolContext
from adsl.agents import service
from adsl.agents.models import PlannedEngineeringCriticDecision, RepairProposal, RepairTarget
from adsl.agents.overhang_edit import inferred_proposal, inferred_scope_unchanged, opportunities, experiment_protection
from adsl.agents.repair_policy import validate_patch_scope
from adsl.agents.source_index import SourceIndex
from adsl.agents.tools.context import AgentToolContext
from adsl.agents.tools.files import apply_patch
from test_overhang_candidate_isolation import fixture, run_case
from test_overhang_local_edit import measured


def test_joint_round_limited_edits_keep_attempt_accounting(tmp_path):
    from adsl.agents.overhang_edit import reserve_attempt, budget_remaining
    from adsl.agents.models import RepairPolicy
    options = {'mode': 'planned_checks', 'max_candidates': None}
    for n in range(6):
        assert reserve_attempt(tmp_path, options, 'engineering', attempt_id=str(n))
    assert budget_remaining(tmp_path, options) == 'round_limited'
    assert not reserve_attempt(tmp_path, options, 'engineering', attempt_id='0')
    assert len((tmp_path/'edit_attempts.jsonl').read_text().splitlines()) == 6
    assert budget_remaining(tmp_path, {'max_candidates': 2}) == 0
    assert RepairPolicy(max_total_candidates=None).max_total_candidates is None
    assert RepairPolicy().max_total_candidates == 5


def test_joint_scope_annotation_preserved_and_symbol_validated(tmp_path):
    source=tmp_path/'source.py'
    source.write_text('class ChairLegs:\n    def __init__(self):\n        pass\n')
    note='ChairLegs.__init__: only the grain placement; leave structural legs unchanged'
    proposal=RepairProposal(proposal_id='p',finding_ids=['overhang:optimization:0'],
        hypothesis='move grain locally',evidence=['source and surface coordinates'],action='relayout',
        target=RepairTarget(allowed_scopes=[note]))
    normalized,errors=inferred_proposal(proposal,source,{'mode':'planned_checks'},opportunities(measured()))
    assert not errors
    assert normalized.parameter_bounds['_localization']['targets'][0]['symbol']=='ChairLegs.__init__'
    assert normalized.parameter_bounds['_localization']['scope_notes']==[note]
    assert note in normalized.evidence[-1]
    proposal.target.allowed_scopes=['Missing.__init__: only grain']
    assert inferred_proposal(proposal,source,{'mode':'planned_checks'},opportunities(measured()))[0] is None
    proposal.target.allowed_scopes=[note]
    assert inferred_proposal(proposal,source,{'protection':{'allowed_classes':['ChairLegs']}},opportunities(measured()))[0] is None


def test_joint_invalid_index_is_unavailable_not_workflow_crash(tmp_path):
    path=tmp_path/'source_index.json';path.write_text('{"features":[{"bounds":[[null,null,null],[null,null,null]]}]}')
    before=path.read_bytes()
    assert service._load_joint_source_index(path,tmp_path) is None
    assert path.read_bytes()==before
    assert json.loads((tmp_path/'source_index_unavailable.json').read_text())['status']=='UNAVAILABLE'


def test_joint_inference_allows_parent_and_helper_without_index(tmp_path):
    a=tmp_path/'source.py';b=tmp_path/'candidate.py'
    a.write_text('class Wheel:\n    pass\nclass Assembly:\n    z = 0\ndef place():\n    return 0\nCHECKER_THRESHOLD=1\n')
    b.write_text(a.read_text().replace('z = 0','z = 1').replace('return 0','return 1'))
    proposal=RepairProposal(proposal_id='p',finding_ids=['overhang:optimization:0'],
        hypothesis='parent controls wheel placement; helper computes its offset',evidence=['read source; location inferred'],
        action='relayout',target=RepairTarget(allowed_scopes=['Assembly','place']))
    options={'mode':'planned_checks','protection':{}}
    proposal,errors=inferred_proposal(proposal,a,options,opportunities(measured()))
    assert not errors and not proposal.target.source_ids
    index=SourceIndex(source_path=str(a),source_sha256='x',index_sha256='',root_feature_id='')
    result=validate_patch_scope(a,b,proposal=proposal,source_index=index,planned_checks=True)
    assert result.valid and result.changed_symbols==['class:Assembly','function:place']
    assert inferred_scope_unchanged(a,b,proposal.parameter_bounds['_localization'])
    b.write_text(b.read_text().replace('CHECKER_THRESHOLD=1','CHECKER_THRESHOLD=2'))
    assert not validate_patch_scope(a,b,proposal=proposal,source_index=index,planned_checks=True).valid
    assert not inferred_scope_unchanged(a,b,proposal.parameter_bounds['_localization'])


def test_patch_tool_still_cannot_write_checker_configuration(tmp_path):
    source=tmp_path/'source.py';source.write_text('x=1')
    config=tmp_path/'checker.json';config.write_text('{"threshold":1}')
    wire=json.dumps({'path':'checker.json','old_text':'1','new_text':'2'})
    ctx=ToolContext(AgentToolContext(tmp_path,source),tool_name='apply_patch',tool_call_id='scope_test',tool_arguments=wire)
    with pytest.raises(ValueError,match='assigned source'):
        asyncio.run(apply_patch.on_invoke_tool(ctx,wire))
    assert config.read_text()=='{"threshold":1}'


@pytest.mark.parametrize('category,expected_calls',[('insufficient_localization',2),('scope_limited',2),('no_change_needed',1),('no_reasonable_plan',1)])
def test_joint_empty_proposal_replans_at_most_once_and_retains(tmp_path,monkeypatch,category,expected_calls):
    f=fixture(tmp_path,monkeypatch)
    f.options.update(mode='planned_checks',protection_policy='explicit_task_constraints',protection={})
    f.original.source_index_path.unlink()
    old=f.kwargs['runtime'].run; calls=[]
    async def model(**kw):
        if kw['role'].startswith('engineering'):
            data=json.loads(kw['input']);calls.append(data)
            assert 'current_complete_source' in data
            # Joint mode already sent the complete source. No redundant read tool.
            return SimpleNamespace(final_output=PlannedEngineeringCriticDecision(approved=False,
                observations=['cannot justify a safe change'],repair_proposals=[],stop_category=category))
        return await old(**kw)
    f.kwargs['runtime'].run=model
    result,book=run_case(f)
    assert len(calls)==expected_calls
    assert not book['attempts'] and book['retained']=='original' and result.glb_path.exists()
    assert book['stop_category']==category
    assert bool(book.get('supplemental_planning_used'))==(expected_calls==2)


def test_joint_missing_index_reaches_actual_candidate(tmp_path,monkeypatch):
    f=fixture(tmp_path,monkeypatch,actions=(90,),budget=1)
    f.options.update(mode='planned_checks',protection_policy='explicit_task_constraints',protection={})
    f.original.source_index_path.unlink()
    result,book=run_case(f)
    assert book['retained']=='attempt_0001'
    assert book['attempts']['attempt_0001']['scope_validation']['changed_symbols']==['class:Frame']


def test_explicit_surfaces_not_silently_removed():
    seen=[]
    def exact(a,b,options):seen.append(options);return {'status':'FAIL'}
    options={'mode':'planned_checks','protection_policy':'explicit_task_constraints',
        'scale_mm_per_source_unit':10,'protection':{'surfaces':[{'label':'explicit interface'}]}}
    assert experiment_protection(None,None,options,exact_check=exact)['status']=='FAIL'
    assert 'scale_mm_per_source_unit' not in seen[0] # no implicit bounds
    assert experiment_protection(None,None,{'mode':'planned_checks'},exact_check=exact)['status']=='UNCONFIRMED'
