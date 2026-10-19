"""NSV controller/feedback contracts; stubs only. Written, not run in this change."""
from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
from adsl.agents import fixed_assembly as flow, assembly_physics as physics, assembly_topology as topology
from adsl.agents.agent_feedback import payload_for_agent, AgentFeedbackError
from adsl.agents.prompts import object_prompt, fixed_assembly_for_agent
from adsl.agents.models import CheckerResult, RepairProposal, RepairTarget, GradedCodeCriticDecision
from adsl.agents.checkers import CheckerRun
from adsl.agents.overhang_edit import version_record
from adsl.agents.utils.io import write_json
from test_fixed_assembly import mock_flow, run_flow
from test_support_volume import row, measurement


def support_fixture(tmp_path,monkeypatch,values,*,patch_status='CHANGED',appearance=None,partition=False):
    values=list(values)
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)]*len(values),patch_status=patch_status)
    w,request,runtime,source,calls=f
    config=deepcopy(request.fixed_assembly)
    config['physics']={'overhang':dict(support_metric='nsv',shape_editable=True,
        layer_height_mm=.2,overhang_threshold_from_horizontal_deg=45)}
    if partition:
        from adsl.agents.partition_score import OBJECTIVE
        config['physics']['overhang']['partition_objective']=dict(OBJECTIVE)
    request=replace(request,max_rounds=len(values),fixed_assembly=config,
        checker_specs=(physics.checker_spec('assembly_overhang'),))
    execute=flow.execute_asset_source
    def run_source(src,out,**kw):
        execution=execute(src,out,**kw)
        path=out/'assembly/assembly_manifest.json';manifest=json.loads(path.read_text())
        manifest.update(root_id='body',connections=[],part_declarations=[
            dict(id='body',components=['crossbar','stem'],assembly_transform=np.eye(4).tolist())],
            parts=[dict(id='body',print_transform_mm=np.eye(4).tolist())])
        write_json(path,manifest)
        return execution
    monkeypatch.setattr(flow,'execute_asset_source',run_source)
    checks=[]
    def run_checks(specs,*,source,root,**kw):
        index=min(len(checks),len(values)-1);checks.append(str(source))
        support=measurement(values[index])['support']
        result=CheckerResult(checker='assembly_overhang',status='PASS',summary='stub support',
            metrics=dict(support=support,support_measurement_config_sha256='fixed-ray-contract',items=[]),
            findings=[physics.finding('assembly_overhang','SUPPORT_SHAPE_OPPORTUNITY','local shape hypothesis',
                category='optimization_opportunity',repairability='design_variable',required=False)],
            assumptions={'source_sha256':flow.file_hash(source)})
        folder=root/'checkers/assembly_overhang';write_json(folder/'result.json',result.model_dump())
        return [CheckerRun(specs[0],result,folder,())]
    monkeypatch.setattr(physics,'run_assembly_checks',run_checks)
    async def advise(*args,**kw):
        return RepairProposal(proposal_id='local',finding_ids=['support'],hypothesis='local relief',
                              target=RepairTarget(),action='reshape')
    monkeypatch.setattr(topology,'engineer',advise)
    async def code(**kw):
        return GradedCodeCriticDecision(approved=True,observations=[],issues=[])
    monkeypatch.setattr(w,'_review_generation_code',code)
    original_repair=w._repair
    captured=[]
    async def repair(**kw):
        p=kw['payload']
        captured.append(payload_for_agent(p,workspace=tmp_path,source_path=kw['source_path'],
            source_version=p['source_version'],role='coder'))
        return await original_repair(**kw)
    monkeypatch.setattr(w,'_repair',repair)
    return (w,request,runtime,source,calls),checks,captured


def test_mode_a_optional_gain(tmp_path,monkeypatch):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,8])
    result,book=run_flow(f)
    assert result.approved and book['retained']=='attempt_0001'
    review=book['versions']['attempt_0001']['reviews']
    assert review['support_postcheck']['conclusion']=='ACCEPT'
    assert review['support_postcheck']['tolerance_mm3']==pytest.approx(1.2)
    assert inputs[0]['support_baseline_version']=='original'
    assert inputs[0]['support_evidence']['support']['total_support_volume_mm3']==12
    assert len(checks)==2


def test_no_support_gain_restores_all_pointers(tmp_path,monkeypatch):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,12])
    result,book=run_flow(f)
    assert result.approved and book['retained']==book['working']==book['qualified']=='original'
    assert book['partition_reference'] is None
    assert 'support_baseline_version' not in book['feedback']
    assert book['versions']['attempt_0001']['reviews']['support_postcheck']['rule']=='no_support_gain'
    assert (tmp_path/'source.py').read_text()=='original'


@pytest.mark.parametrize('outcome',['NO_CHANGE','TOOL_ERROR'])
def test_optional_edit_stops_without_losing_baseline(tmp_path,monkeypatch,outcome):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,8],patch_status=outcome)
    result,book=run_flow(f)
    assert result.approved and book['retained']==book['working']=='original'
    assert len(checks)==1 and len(inputs)==1
    assert book['stop_reason']==outcome


def test_support_checkpoint_copies_and_restores_none_reference(tmp_path):
    source=tmp_path/'source.py';source.write_text('baseline')
    record=version_record('original',source,None,reviews={})
    book=dict(versions={'original':record},working='original',retained='original',qualified=None,
              partition_reference=None,active_partition_intent=None)
    feedback={'source_version':'original','engineering_proposal':{'action':'reshape'},'nested':{'a':1}}
    flow._save_support_checkpoint(book,baseline_id='original',baseline_feedback=feedback)
    feedback['nested']['a']=9
    book.update(working='bad',retained='bad',qualified='bad',partition_reference='new-reference')
    restored=flow._restore_support_baseline(book,baseline_id='original',stop_reason='support_unavailable')
    assert book['working']==book['retained']=='original' and book['qualified'] is None
    assert book['partition_reference'] is None and restored['nested']['a']==1
    assert 'engineering_proposal' not in restored


def test_mismatched_backend_is_not_comparable():
    baseline={**measurement(12),'measurement_config_sha256':'embree','tolerance_mm3':1}
    candidate={**measurement(1),'measurement_config_sha256':'triangle'}
    assert flow._compare_support(baseline,candidate)['rule']=='support_unavailable'


@pytest.mark.parametrize('role',['coder','engineering_critic','code_critic_review'])
def test_nsv_prompt_keeps_api_and_removes_conflicting_objective(role):
    legacy=object_prompt(role,articulation=False,fixed_assembly=True)
    assert legacy==object_prompt(role,articulation=False,fixed_assembly=True,support_metric='area',shape_editable=False)
    prompt=object_prompt(role,articulation=False,fixed_assembly=True,support_metric='nsv',shape_editable=True)
    assert 'Dapper' not in prompt and 'N**alpha' not in prompt
    assert 'add_part' in prompt and 'TabSlot' in prompt and 'delta_S < -tau' in prompt
    assert 'Authorized optional shape actions' in prompt
    disabled=object_prompt(role,articulation=False,fixed_assembly=True,support_metric='nsv',shape_editable=False)
    assert 'Optional shape changes for support reduction are disabled' in disabled


def test_model_evidence_is_bounded_idempotent_and_score_free(tmp_path):
    evidence=dict(source_version='original',support=measurement(12)['support'],part_count=1,tolerance_mm3=1.2,
        items=[dict(part_id='body',support=row(),print_transform_mm=np.eye(4).tolist(),
                    largest_regions=[{'area_mm2':1,'global_face_ids':list(range(20000))}],evidence_ref={})])
    payload={'support_evidence':evidence,'feedback':{'partition_guidance':{'score':999},
        'support_evidence':evidence},'assigned_source':'source.py'}
    original=deepcopy(payload)
    result=payload_for_agent(payload,workspace=tmp_path,source_path=None,source_version=None,role='coder')
    assert 'global_face_ids' not in json.dumps(result) and 'partition_guidance' not in json.dumps(result)
    assert payload==original
    assert payload_for_agent(result,workspace=tmp_path,source_path=None,source_version=None,role='coder')==result


def test_support_evidence_counts_toward_budget(tmp_path):
    evidence=dict(support=measurement(12)['support'],part_count=4000,items=[dict(part_id=str(i),support=row()) for i in range(4000)])
    with pytest.raises(AgentFeedbackError,match='AGENT_FEEDBACK_TOO_LARGE'):
        payload_for_agent({'support_evidence':evidence},workspace=tmp_path,source_path=None,source_version=None,role='coder')


def test_initial_config_projection_does_not_mutate_frozen():
    config={'physics':{'overhang':{'support_metric':'nsv','partition_objective':{'alpha':.1}}}}
    original=deepcopy(config);view=fixed_assembly_for_agent(config)
    assert config==original and 'alpha' not in json.dumps(view)
    area={};assert fixed_assembly_for_agent(area) is area


def d_fixture(tmp_path,monkeypatch,values,*,bad_middle=False,refresh_value=None):
    from adsl.agents import partition_score as scoring
    f,checks,captured=support_fixture(tmp_path,monkeypatch,values,partition=True)
    calls=f[-1]
    refs=[]
    def create(manifest,folder,config):
        folder.mkdir(parents=True,exist_ok=True)
        digest=json.loads(Path(manifest).read_text())['source_sha256']
        path=folder/f'{digest}.json'
        path.write_text(json.dumps({'source_sha256':digest}))
        refs.append(str(path))
        return path
    def compare_shape(manifest,reference,**kw):
        old=json.loads(Path(reference).read_text())['source_sha256']
        new=json.loads(Path(manifest).read_text())['source_sha256']
        return dict(status='MATCH' if old==new else 'CHANGED',
            semantic_ownership_preserved=True,root_frame_preserved=True,
            candidate_bounds_mm=[[0,0,0],[1,1,1]])
    monkeypatch.setattr(scoring,'create_reference',create)
    monkeypatch.setattr(scoring,'load_reference',lambda *a,**kw:{})
    monkeypatch.setattr(scoring,'reference_shape_comparison',compare_shape)
    monkeypatch.setattr(scoring,'compare_partition_scores',lambda *a,**kw:{'conclusion':'UNCHANGED'})
    published=[]
    def publish(result,workspace,digest,version):
        published.append((deepcopy(result),digest,version))
        return result.get('artifacts',{}).get('print_layout'),[]
    monkeypatch.setattr(scoring,'publish_print_layout',publish)
    def checks_d(specs,*,source,root,partition_reference=None,**kw):
        index=len(calls);checks.append(str(root))
        s=values[min(index,len(values)-1)]
        if refresh_value is not None and 'partition_reference_refresh' in str(root):s=refresh_value
        support=measurement(s)['support']
        result=CheckerResult(checker='assembly_overhang',status='PASS',summary='stub support',
            metrics=dict(support=support,support_measurement_config_sha256='fixed-ray-contract',items=[],
                partition_objective={'print_part_count':1,'reference_sha256':str(partition_reference),'score':1}),
            findings=[physics.finding('assembly_overhang','SUPPORT_SHAPE_OPPORTUNITY','local hypothesis',
                category='optimization_opportunity',repairability='design_variable',required=False)],
            artifacts={'print_layout':str(root/'layout.json')},
            assumptions={'source_sha256':flow.file_hash(source)})
        write_json(root/'layout.json',dict(source_sha256=flow.file_hash(source),support=s))
        folder=root/'checkers/assembly_overhang';write_json(folder/'result.json',result.model_dump())
        return [CheckerRun(specs[0],result,folder,())]
    monkeypatch.setattr(physics,'run_assembly_checks',checks_d)
    if bad_middle:
        execute=flow.execute_asset_source
        def broken(source,out,**kw):
            execution=execute(source,out,**kw)
            if len(calls)==1:
                path=out/'assembly/assembly_manifest.json';raw=json.loads(path.read_text())
                raw['part_declarations'][0]['components']=['missing_semantic_component']
                write_json(path,raw)
            return execution
        monkeypatch.setattr(flow,'execute_asset_source',broken)
    return f,checks,captured,refs,published


def test_d_shape_only_continues_without_fabricated_intent(tmp_path,monkeypatch):
    f,checks,inputs,refs,published=d_fixture(tmp_path,monkeypatch,[12,8,7],bad_middle=True)
    result,book=run_flow(f)
    assert result.approved and book['retained']=='attempt_0002'
    first=book['versions']['attempt_0001']['reviews']
    assert first['grouping_realization']['status']=='MISMATCH' and not first['accepted']
    assert first['partition_intent'] is None
    assert len(inputs)==2
    assert all(p['support_baseline_version']=='original' and p['partition_intent'] is None for p in inputs)
    assert inputs[1]['source_version']=='attempt_0001'
    assert book['versions']['attempt_0002']['reviews']['support_postcheck']['conclusion']=='ACCEPT'
    assert published[-1][2]=='attempt_0002'


def test_d_refreshed_measurement_not_old_pose_decides_rejection(tmp_path,monkeypatch):
    f,checks,inputs,refs,published=d_fixture(tmp_path,monkeypatch,[12,1],refresh_value=14)
    result,book=run_flow(f)
    candidate=book['versions']['attempt_0001']['reviews']
    assert candidate['support_reference_refresh']['status']=='PASS'
    assert candidate['support_evidence']['support']['total_support_volume_mm3']==14
    assert candidate['support_postcheck']['conclusion']=='REJECT'
    assert book['working']==book['retained']==book['qualified']=='original'
    assert book['partition_reference']==refs[0]
    assert published[-1][2]=='original'
    assert json.loads(Path(published[-1][0]['artifacts']['print_layout']).read_text())['support']==12


def test_shape_only_origin_requires_recorded_parent_chain(tmp_path):
    source=tmp_path/'source.py';source.write_text('baseline')
    candidate=tmp_path/'candidate.py';candidate.write_text('candidate')
    base=version_record('original',source,None)
    review=dict(parent_version='original',support_baseline_version='original',
                repair_proposal=dict(action='reshape',grouping_change=None))
    record=version_record('attempt_0001',candidate,None,reviews=review)
    book={'versions':{'original':base,'attempt_0001':record}}
    assert flow._shape_only_origin(book,'attempt_0001','original')
    record['reviews']['repair_proposal']['grouping_change']={'operation':'split'}
    assert not flow._shape_only_origin(book,'attempt_0001','original')


def test_root_and_ownership_checks():
    baseline={'root_id':'body','parts':[dict(id='body',components=['a','b'],assembly_transform=np.eye(4).tolist())]}
    assert flow._shape_only_declarations(baseline,deepcopy(baseline))['status']=='MATCH'
    changed=deepcopy(baseline);changed['parts'][0]['components']=['a']
    assert flow._shape_only_declarations(baseline,changed)['status']=='MISMATCH'
    changed=deepcopy(baseline);changed['parts'][0]['assembly_transform'][2][3]=1
    assert flow._shape_only_declarations(baseline,changed)['status']=='MISMATCH'
    assert flow._shape_only_declarations(baseline,None)['status']=='UNAVAILABLE'


def test_a_required_repair_can_increase_support_without_becoming_optional(tmp_path,monkeypatch):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,15])
    execute=flow.execute_asset_source
    def initially_unapproved(source,out,**kw):
        execution=execute(source,out,**kw)
        mock_flow.appearance=bool(f[-1])
        return execution
    monkeypatch.setattr(flow,'execute_asset_source',initially_unapproved)
    async def code(**kw):
        return GradedCodeCriticDecision(approved=bool(f[-1]),observations=[],issues=[],required_changes=[] if f[-1] else ['fix appearance'])
    monkeypatch.setattr(f[0],'_review_generation_code',code)
    result,book=run_flow(f)
    assert result.approved and book['retained']=='attempt_0001'
    assert book['versions']['attempt_0001']['reviews']['support_postcheck'] is None


def test_a_initial_non_upright_stops_without_new_edit(tmp_path,monkeypatch):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,8])
    execute=flow.execute_asset_source
    def tilted(source,out,**kw):
        execution=execute(source,out,**kw)
        path=out/'assembly/assembly_manifest.json';raw=json.loads(path.read_text())
        raw['parts'][0]['print_transform_mm']=[[0,-1,0,0],[1,0,0,0],[0,0,1,0],[0,0,0,1]]
        write_json(path,raw)
        return execution
    monkeypatch.setattr(flow,'execute_asset_source',tilted)
    result,book=run_flow(f)
    assert not result.approved and not inputs
    assert book['versions']['original']['reviews']['mode_a_pose_check']['rule']=='print_pose_changed'
