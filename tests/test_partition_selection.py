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
        return {'status':'CHANGED' if mode in ('changed_shape','reference_repair') and len(calls)>0 else 'MATCH'}
    monkeypatch.setattr(scoring,'reference_shape_comparison',shape)
    async def code(**kw):
        return GradedCodeCriticDecision(approved=len(calls)>0 or mode not in ('required_repair','reference_repair'),observations=[],issues=[])
    monkeypatch.setattr(w,'_review_generation_code',code)
    checks=[]
    def run(specs,*,source,root,**kw):
        round_number=len(checks);checks.append(source)
        results=[]
        for spec in specs:
            out=root/'checkers'/spec.name;out.mkdir(parents=True,exist_ok=True)
            status='PASS';metrics={};artifacts={};findings=[]
            if spec.name=='assembly_overhang':
                n=2 if not round_number else 1 if mode in ('improved','topology_fail','standing_unknown','changed_shape') else 3 if mode in ('worse','required_repair') else 2
                ref=scoring.load_reference(kw.get('partition_reference') or reference)
                objective=scoring.score_partition([dict(status='PASS',gap_voxels=0) for _ in range(n)],ref,part_count=n)
                if mode=='unknown' and round_number: objective['score']=None;status='INDETERMINATE'
                metrics={'partition_objective':objective,'items':[]}
                stls=[]
                for i in range(n):
                    stl=out/f'piece{i}.stl';stl.write_text(source.read_text())
                    stls.append(dict(part_id=f'piece{i}',stl=str(stl),stl_sha256=flow.file_hash(stl)))
                layout=out/'print_layout.json';write_json(layout,dict(status=status,source_sha256=flow.file_hash(source),
                    reference_sha256=ref['reference_sha256'],evaluation_config_sha256=objective['evaluation_config_sha256'],parts=stls))
                artifacts={'print_layout':str(layout),'partition_reference':str(reference)}
                findings=[physics.finding('assembly_overhang','PRINT_PARTITION_OPPORTUNITY','Consider a local regroup',
                    category='optimization_opportunity',repairability='design_variable',required=False)]
            if round_number==1 and mode=='topology_fail' and spec.name=='assembly_topology':status='FAIL'
            if round_number and mode=='standing_unknown' and spec.name=='assembly_standing':status='INDETERMINATE'
            result=CheckerResult(checker=spec.name,status=status,summary='mock gate',metrics=metrics,artifacts=artifacts,
                findings=findings,assumptions={'source_sha256':flow.file_hash(source)})
            write_json(out/'report.json',result.model_dump())
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
    result,book=run_flow(state)
    assert result.approved and book['retained']=='attempt_0001'
    old=book['versions']['original']['reviews'];new=book['versions']['attempt_0001']['reviews']
    assert new['edit_purpose']=='required_repair' and new['reference_shape_changed']
    assert old['partition_reference']!=new['partition_reference']==book['partition_reference']
    assert old['assembly_overhang']['metrics']['partition_objective']['reference_sha256']!=new['assembly_overhang']['metrics']['partition_objective']['reference_sha256']
    assert len(state[-1])==1 and len(checks)==3


def test_repairing_optimization_candidate_keeps_original_score_baseline(tmp_path,monkeypatch):
    state,checks=setup(tmp_path,monkeypatch,'topology_fail')
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
    assert result.approved and book['retained']=='attempt_0002'
