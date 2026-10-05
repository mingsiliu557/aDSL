"""Version and selection contracts. Models/physics mocked; score arithmetic real."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

from adsl.agents import fixed_assembly as flow, assembly_physics as physics, assembly_topology as topology
from adsl.agents import partition_score as scoring
from adsl.agents.checkers import CheckerRun
from adsl.agents.models import (RepairPolicy, RepairProposal, RepairTarget, PrintGroupingChange,
    CheckerResult, GradedCodeCriticDecision)
from adsl.agents.utils.io import write_json
from test_fixed_assembly import mock_flow, run_flow
from test_partition_score import reference_fixture


def setup(tmp_path,monkeypatch,mode):
    state=mock_flow(tmp_path,monkeypatch,[('PASS',mode not in ('required_repair','reference_repair')),('PASS',True)])
    w,req,runtime,source,calls=state
    specs=(topology.checker_spec(),physics.checker_spec('assembly_overhang'),physics.checker_spec('assembly_standing'))
    req=replace(req,max_rounds=2,checker_specs=specs,repair_policy=RepairPolicy(print_partition_editable=True),
        fixed_assembly={**req.fixed_assembly,'physics':{'overhang':{'partition_objective':scoring.OBJECTIVE}}})
    reference_dir=tmp_path/'reference_input';reference_dir.mkdir()
    manifest=reference_fixture(reference_dir)
    reference=scoring.create_reference(manifest,tmp_path/'references')
    ref2=reference
    if mode=='reference_repair':
        raw=json.loads(manifest.read_text());raw['source_sha256']='new_source'
        for row in raw['partition_reference_inputs']:row['source_sha256']='new_source'
        write_json(manifest,raw);ref2=scoring.create_reference(manifest,tmp_path/'references')
    monkeypatch.setattr(scoring,'create_reference',lambda *a,**kw:ref2 if calls else reference)
    def shape(*a):
        return {'status':'CHANGED' if len(calls)>0 and (mode=='changed_shape' or (mode=='reference_repair' and str(a[1])==str(reference))) else 'MATCH'}
    monkeypatch.setattr(scoring,'reference_shape_comparison',shape)
    async def code(**kw):
        return GradedCodeCriticDecision(approved=len(calls)>0 or mode not in ('required_repair','reference_repair'),observations=[],issues=[])
    monkeypatch.setattr(w,'_review_generation_code',code)
    checks=[]
    def run(specs,*,source,root,**kw):
        round_number=len(checks);checks.append(source)
        results=[]
        for spec in specs:
            out=root/'checkers'/spec.name;out.mkdir(parents=True,exist_ok=False)
            status='PASS';metrics={};artifacts={};findings=[]
            if spec.name=='assembly_overhang':
                n=2 if not round_number else 1 if mode in ('improved','topology_fail','standing_unknown','changed_shape') else 3 if mode in ('worse','required_repair') else 2
                ref=scoring.load_reference(kw.get('partition_reference') or reference)
                objective=scoring.score_partition([dict(status='PASS',gap_voxels=0) for _ in range(n)],ref,part_count=n)
                if mode=='unknown' and round_number: objective['score']=None;status='INDETERMINATE'
                metrics={'partition_objective':objective,'partition_guidance':scoring.partition_score_guidance(objective),'items':[]}
                stls=[]
                for i in range(n):
                    stl=out/f'piece{i}.stl';stl.write_text(source.read_text())
                    stls.append(dict(part_id=f'piece{i}',stl=str(stl),stl_sha256=flow.file_hash(stl)))
                layout=out/'print_layout.json';write_json(layout,dict(status=status,source_sha256=flow.file_hash(source),
                    reference_sha256=ref['reference_sha256'],evaluation_config_sha256=objective['evaluation_config_sha256'],parts=stls))
                artifacts={'print_layout':str(layout),'partition_reference':str(kw.get('partition_reference') or reference)}
                findings=[physics.finding('assembly_overhang','PRINT_PARTITION_OPPORTUNITY','Consider a local regroup',
                    category='optimization_opportunity',repairability='design_variable',required=False)]
            if round_number==1 and mode=='topology_fail' and spec.name=='assembly_topology':
                status='FAIL'
                findings=[physics.finding(spec.name,'UNDECLARED_PART_INTERFERENCE','Measured overlap in candidate',
                    category='geometry_failure',repairability='geometry',required=True)]
            if round_number and mode=='standing_unknown' and spec.name=='assembly_standing':status='INDETERMINATE'
            result=CheckerResult(checker=spec.name,status=status,summary='mock gate',metrics=metrics,artifacts=artifacts,
                findings=findings,assumptions={'source_sha256':flow.file_hash(source)})
            write_json(out/'report.json',result.model_dump())
            write_json(out/'result.json',result.model_dump())
            results.append(CheckerRun(spec,result,out,()))
        return results
    monkeypatch.setattr(physics,'run_assembly_checks',run)
    async def advise(*args,**kw):
        return RepairProposal(proposal_id='group',finding_ids=['assembly_overhang:PRINT_PARTITION_OPPORTUNITY:'],
            hypothesis='local merge',target=RepairTarget(),action='regroup_print_parts',
            grouping_change=PrintGroupingChange(operation='merge',source_part_ids=['crossbar','stem'],
                target_print_parts=[{'id':'crossbar','components':['crossbar','stem']}],connection_changes=['remove joint']))
    monkeypatch.setattr(topology,'engineer',advise)
    return (w,req,runtime,source,calls),checks


@pytest.mark.parametrize('mode,selected',[('improved','attempt_0001'),('worse','original'),('equal','original'),
    ('unknown','original'),('topology_fail','original'),('standing_unknown','original'),
    ('required_repair','attempt_0001'),('changed_shape','original')])
def test_score_hard_gates_purpose_and_selected_print_bundle(tmp_path,monkeypatch,mode,selected):
    state,checks=setup(tmp_path,monkeypatch,mode)
    result,book=run_flow(state)
    assert result.approved and book['retained']==selected
    assert len(checks)==2
    reviews=book['versions']['attempt_0001']['reviews']
    assert reviews['edit_purpose']==('required_repair' if mode=='required_repair' else 'partition_optimization')
    final=json.loads((tmp_path/'assembly_result.json').read_text())
    assert final['version_id']==selected
    for part in final['print_parts']:
        assert Path(part['stl']).read_text()==state[3].read_text()
        assert str(Path(part['stl']).parent).endswith(selected)
    if mode in ('worse','equal','changed_shape'):
        assert book['working']=='original' and book['feedback']['source_version']=='original'
    before=(tmp_path/'repair_history.jsonl').read_bytes()
    result2,book2=run_flow(state)
    assert result2.approved and book2['retained']==selected and len(checks)==2
    assert (tmp_path/'repair_history.jsonl').read_bytes()==before


def test_reference_tampering_rejected_on_resume(tmp_path,monkeypatch):
    state,_=setup(tmp_path,monkeypatch,'improved');_,book=run_flow(state)
    path=Path(book['partition_reference']);(path.parent/'occupied.npz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='reference geometry/occupancy changed'):run_flow(state)


def test_accepted_body_repair_references_and_rescores_without_model_edit(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'reference_repair')
    measured=[];run=physics.run_assembly_checks
    def capture(*a,**kw):
        results=run(*a,**kw)
        overhang=next(r for r in results if r.spec.name=='assembly_overhang')
        measured.append((overhang.output_dir,{p:p.read_bytes() for p in overhang.output_dir.iterdir()}))
        return results
    monkeypatch.setattr(physics,'run_assembly_checks',capture)
    result,book=run_flow(state)
    assert result.approved and book['retained']=='attempt_0001'
    candidate=book['versions']['attempt_0001']
    old=book['versions']['original']['reviews'];new=candidate['reviews']
    assert new['edit_purpose']=='required_repair' and new['reference_shape_changed']
    assert old['partition_reference']!=new['partition_reference']==book['partition_reference']
    old_sha=old['assembly_overhang']['metrics']['partition_objective']['reference_sha256']
    new_sha=new['assembly_overhang']['metrics']['partition_objective']['reference_sha256']
    assert old_sha!=new_sha
    assert len(state[-1])==1 and len(checks)==3
    previous_dir,previous_files=measured[1];refreshed_dir,refreshed_files=measured[2]
    assert refreshed_dir==previous_dir.parent.parent/'partition_reference_refresh/checkers/assembly_overhang'
    assert json.loads((previous_dir/'report.json').read_text())['metrics']['partition_objective']['reference_sha256']==old_sha
    assert json.loads((refreshed_dir/'report.json').read_text())==new['assembly_overhang']
    for path,content in {**previous_files,**refreshed_files}.items():
        assert path.read_bytes()==content
        assert candidate['files'][str(path)]==flow.file_hash(path)
    selected=next(r for r in candidate['checkers'] if r['spec']['name']=='assembly_overhang')
    assert selected['output_dir']==str(refreshed_dir)
    final=json.loads((tmp_path/'assembly_result.json').read_text())
    layout=json.loads(Path(final['print_layout']).read_text())
    assert layout['reference_sha256']==new_sha
    assert layout['source_sha256']==final['source_sha256']==flow.file_hash(state[3])
    for part in final['print_parts']:
        assert Path(part['stl']).read_text()==state[3].read_text()
    assert run_flow(state)[0].approved and len(checks)==3 and len(state[-1])==1
    (refreshed_dir/'report.json').write_text('{}')
    with pytest.raises(ValueError,match='version asset hash changed'):
        run_flow(state)


def test_reference_creation_failure_preserves_previous_measurement(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'reference_repair')
    create=scoring.create_reference;run=physics.run_assembly_checks;measured=[]
    def failing_reference(*a,**kw):
        if state[-1]:raise ValueError('reference body unavailable')
        return create(*a,**kw)
    def capture(*a,**kw):
        results=run(*a,**kw)
        overhang=next(r for r in results if r.spec.name=='assembly_overhang')
        measured.append((overhang.output_dir,{p:p.read_bytes() for p in overhang.output_dir.iterdir()}))
        return results
    monkeypatch.setattr(scoring,'create_reference',failing_reference)
    monkeypatch.setattr(physics,'run_assembly_checks',capture)
    result,book=run_flow(state)
    candidate=book['versions']['attempt_0001'];reviews=candidate['reviews']
    assert result.approved and book['retained']=='attempt_0001'  # Overhang stays advisory.
    assert reviews['partition_reference_error']=='reference body unavailable'
    assert reviews['assembly_overhang']['status']=='INDETERMINATE'
    assert reviews['assembly_overhang']['metrics']['partition_objective']['score'] is None
    assert not reviews['partition_ready'] and book['partition_reference'] is None
    previous_dir,previous_files=measured[1]
    selected=next(r for r in candidate['checkers'] if r['spec']['name']=='assembly_overhang')
    unavailable_dir=Path(selected['output_dir'])
    assert unavailable_dir==previous_dir.parent.parent/'partition_reference_refresh/checkers/assembly_overhang'
    assert json.loads((previous_dir/'report.json').read_text())['status']=='PASS'
    for path,content in previous_files.items():
        assert path.read_bytes()==content
        assert candidate['files'][str(path)]==flow.file_hash(path)
    for name in ('result.json','report.json'):
        path=unavailable_dir/name
        assert json.loads(path.read_text())==reviews['assembly_overhang']
        assert candidate['files'][str(path)]==flow.file_hash(path)
    final=json.loads((tmp_path/'assembly_result.json').read_text())
    assert final['print_layout'] is None and not final['print_parts']
    assert len(state[-1])==1 and len(checks)==2
    assert run_flow(state)[0].approved and len(checks)==2


@pytest.mark.parametrize('surface',[False,True])
def test_repairing_optimization_candidate_keeps_original_score_baseline(tmp_path,monkeypatch,surface):
    state,checks=setup(tmp_path,monkeypatch,'topology_fail')
    if surface:visual_aspects(state,monkeypatch,['surface'])
    w,req,rt,source,calls=state
    req=replace(req,max_rounds=3)
    execute=flow.execute_asset_source
    def third(source,out,**kw):
        if len(calls)==2:
            # Reuse the same mock export shape for the third checked source.
            from adsl.agents.utils.execution import ExecutionResult
            folder=out/'assembly';folder.mkdir(parents=True)
            write_json(folder/'assembly_manifest.json',{'status':'PASS','failures':[],'source_sha256':flow.file_hash(source)})
            render=out/'render';render.mkdir();glb=render/'scene.glb';glb.write_text(source.read_text())
            png=render/'view.png';png.write_text(source.read_text())
            return ExecutionResult(out,glb,None,(png,),'','')
        return execute(source,out,**kw)
    monkeypatch.setattr(flow,'execute_asset_source',third)
    # Last score equals the previous candidate, but is still compared to original.
    result,book=run_flow((w,req,rt,source,calls))
    assert len(calls)==2 and len(checks)==3
    last=book['versions']['attempt_0002']['reviews']
    assert last['edit_purpose']=='partition_optimization'
    assert last['comparison_baseline_version']=='original'
    assert last['partition_change_vs_baseline']['conclusion']=='IMPROVED'
    assert result.approved==(not surface) and book['retained']=='attempt_0002'
    assert last['partition_adopted']


def test_no_grouping_proposal_stops_without_source_edit(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'improved')
    async def no_proposal(*a,**kw):return None
    monkeypatch.setattr(topology,'engineer',no_proposal)
    result,book=run_flow(state)
    assert result.approved and len(checks)==1 and not state[-1]
    assert book['retained']=='original' and book['stop_reason']=='no_reasonable_partition_proposal'


def visual_aspects(state,monkeypatch,aspects):
    w,req,rt,source,calls=state
    async def code(**kw):
        aspect=aspects[min(len(calls),len(aspects)-1)]
        return GradedCodeCriticDecision(approved=aspect is None,observations=[],
            required_changes=['Unresolved explicit appearance requirement'] if aspect else [],
            issues=[dict(severity='HIGH',aspect=aspect,target='body',problem='Current appearance requirement unresolved',
                         suggested_fix='Use supported controls or report limitation')] if aspect else [])
    monkeypatch.setattr(w,'_review_generation_code',code)


@pytest.mark.parametrize('mode,selected',[('improved','attempt_0001'),('worse','original'),('equal','original'),('changed_shape','original')])
def test_surface_only_partition_selection_and_bound_outputs(tmp_path,monkeypatch,mode,selected):
    state,checks=setup(tmp_path,monkeypatch,mode)
    visual_aspects(state,monkeypatch,['surface','surface'])
    result,book=run_flow(state)
    original=book['versions']['original']['reviews'];candidate=book['versions']['attempt_0001']['reviews']
    assert original['partition_ready'] and not original['accepted']
    assert candidate['edit_purpose']=='partition_optimization'
    assert candidate['comparison_baseline_version']=='original'
    assert candidate['partition_adopted']==(mode=='improved')
    assert not result.approved and book['qualified'] is None and book['retained']==selected
    payload=state[-1][0]['payload']
    assert payload['edit_purpose']=='partition_optimization'
    assert payload['partition_guidance']==original['assembly_overhang']['metrics']['partition_guidance']
    assert payload['source_version']=='original' and 'deferred' in payload['assignment']
    final=json.loads((tmp_path/'assembly_result.json').read_text())
    assert final['source_sha256']==flow.file_hash(state[3])==final['reviews']['checker_source_sha256']
    assert final['version_id']==selected and final['print_parts']
    assert Path(final['print_layout']).parent.name==selected
    for row in final['print_parts']:assert Path(row['stl']).read_text()==state[3].read_text()
    assert final['reviews']['accepted'] is False
    if mode!='improved':
        assert book['working']=='original' and book['feedback']['source_version']=='original'
        assert book['partition_stop_reference']==original['assembly_overhang']['metrics']['partition_objective']['reference_sha256']
    assert not run_flow(state)[0].approved and len(checks)==2


@pytest.mark.parametrize('missing_render',[False,True])
def test_geometry_issue_or_missing_display_is_not_partition_ready(tmp_path,monkeypatch,missing_render):
    state,checks=setup(tmp_path,monkeypatch,'improved')
    visual_aspects(state,monkeypatch,['surface' if missing_render else 'geometry'])
    w,req,rt,source,calls=state;req=replace(req,max_rounds=1)
    if missing_render:
        execute=flow.execute_asset_source
        def no_views(*a,**kw):return replace(execute(*a,**kw),render_paths=())
        monkeypatch.setattr(flow,'execute_asset_source',no_views)
    result,book=run_flow((w,req,rt,source,calls))
    assert not result.approved and not book['versions']['original']['reviews']['partition_ready']
    assert book['qualified'] is None


def test_geometry_visual_helper_requires_explicit_classified_highs():
    for issues in ([],[dict(severity='LOW',aspect='surface')],[dict(severity='HIGH')],
                   [dict(severity='HIGH',aspect='surface'),dict(severity='HIGH',aspect='geometry')]):
        assert not flow._geometry_visual_ready(dict(appearance_approved=False,code_critic={'issues':issues}),True)
    assert not flow._geometry_visual_ready(dict(appearance_approved=None,code_critic={'issues':[dict(severity='HIGH',aspect='surface')]}),True)


def test_required_physical_regroup_may_lower_score_with_surface_pending(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'required_repair')
    visual_aspects(state,monkeypatch,['surface','surface'])
    run=physics.run_assembly_checks
    def fail_initial(*a,**kw):
        results=run(*a,**kw)
        if len(checks)==1:
            target=results[0].result;target.status='FAIL'
            target.findings=[physics.finding('assembly_topology','UNDECLARED_PART_INTERFERENCE','Measured body overlap',
                category='geometry_failure',repairability='geometry',required=True)]
        return results
    monkeypatch.setattr(physics,'run_assembly_checks',fail_initial)
    result,book=run_flow(state)
    original=book['versions']['original']['reviews'];candidate=book['versions']['attempt_0001']['reviews']
    assert not original['partition_ready'] and candidate['partition_ready']
    assert candidate['edit_purpose']=='required_repair' and not candidate['partition_adopted']
    assert scoring.compare_partition_scores(original['assembly_overhang'],candidate['assembly_overhang'])['conclusion']=='WORSE'
    assert not result.approved and book['retained']=='attempt_0001' and book['qualified'] is None
    assert state[-1][0]['payload']['grouping_change']['operation']=='merge'


def test_required_body_repair_with_surface_pending_refreshes_reference(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'reference_repair')
    visual_aspects(state,monkeypatch,['geometry','surface'])
    result,book=run_flow(state)
    old=book['versions']['original']['reviews'];new=book['versions']['attempt_0001']['reviews']
    assert new['reference_shape_changed'] and new['partition_ready']
    assert new['partition_reference']!=old['partition_reference']
    assert new['body_reference_comparison']['status']=='MATCH'
    assert len(checks)==3 and len(state[-1])==1
    assert book['retained']=='attempt_0001' and book['qualified'] is None and not result.approved


@pytest.mark.parametrize('unavailable',[False,True])
def test_optional_no_proposal_or_unavailable_never_blindly_regroups(tmp_path,monkeypatch,unavailable):
    state,checks=setup(tmp_path,monkeypatch,'improved')
    visual_aspects(state,monkeypatch,['surface'])
    w,req,rt,source,calls=state;engineering=[]
    async def no_proposal(*a,**kw):
        engineering.append(True)
        if unavailable:raise ValueError('malformed model response')
        return None
    async def surface_no_change(**kw):
        calls.append(kw)
        assert kw['payload']['edit_purpose']=='required_repair'
        assert kw['payload']['source_version']=='original'
        return dict(status='NO_CHANGE',reason='surface API unavailable')
    monkeypatch.setattr(topology,'engineer',no_proposal);monkeypatch.setattr(w,'_repair',surface_no_change)
    result,book=run_flow(state)
    assert len(engineering)==1 and len(calls)==1 and len(checks)==1
    assert ('partition_stop_reference' in book)==(not unavailable)
    assert book['feedback']['engineering']['status']==('UNAVAILABLE' if unavailable else 'NO_PROPOSAL')
    assert book['retained']=='original' and not result.approved
    run_flow(state)
    assert len(engineering)==len(calls)==len(checks)==1


def test_existing_full_approval_not_replaced_by_surface_regression(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'improved')
    visual_aspects(state,monkeypatch,[None,'surface'])
    result,book=run_flow(state)
    assert result.approved and book['retained']==book['qualified']==book['working']=='original'
    candidate=book['versions']['attempt_0001']['reviews']
    assert candidate['partition_adopted'] and not candidate['accepted']


def test_losing_surface_candidate_restores_feedback_before_surface_repair(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'worse')
    visual_aspects(state,monkeypatch,['surface'])
    w,req,rt,source,calls=state;req=replace(req,max_rounds=3)
    repair=w._repair
    async def bounded(**kw):
        if calls:
            calls.append(kw)
            assert kw['payload']['source_version']=='original'
            assert kw['payload']['feedback']['source_sha256']==flow.file_hash(tmp_path/'original/source.py')
            assert kw['payload']['edit_purpose']=='required_repair'
            assert kw['source_path'].read_text()=='original'
            return dict(status='NO_CHANGE',reason='surface API unavailable')
        return await repair(**kw)
    monkeypatch.setattr(w,'_repair',bounded)
    result,book=run_flow((w,req,rt,source,calls))
    assert len(calls)==2 and len(checks)==2 and not result.approved
    assert book['working']==book['retained']=='original'


def test_same_reference_not_reopened_after_surface_repair(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'equal');visual_aspects(state,monkeypatch,['surface'])
    w,req,rt,source,calls=state;req=replace(req,max_rounds=3);advice=[]
    async def no_proposal(*a,**kw):advice.append(True);return None
    repair=w._repair
    async def bounded(**kw):
        assert kw['payload']['edit_purpose']=='required_repair'
        if calls:
            calls.append(kw);return dict(status='NO_CHANGE',reason='unsupported remaining surface finish')
        return await repair(**kw)
    monkeypatch.setattr(topology,'engineer',no_proposal);monkeypatch.setattr(w,'_repair',bounded)
    result,book=run_flow((w,req,rt,source,calls))
    assert len(advice)==1 and len(checks)==2 and len(calls)==2
    assert book['versions']['attempt_0001']['reviews']['partition_ready']
    assert not result.approved
