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
        assert 'Support optimization uses' in kw['code_critic']['instructions']
        assert 'Dapper' not in kw['code_critic']['instructions']
        return GradedCodeCriticDecision(approved=True,observations=[],issues=[])
    monkeypatch.setattr(w,'_review_generation_code',code)
    original_repair=w._repair
    captured=[]
    async def repair(**kw):
        assert 'Support optimization uses' in kw['repairer']['instructions']
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


def test_default_area_frozen_hash_and_feedback_keys_remain_legacy(tmp_path,monkeypatch):
    import hashlib
    from test_fixed_assembly import plan_data
    from adsl.agents.models import FixedAssemblyPlan
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    f=(f[0],replace(f[1],checker_specs=()),*f[2:])
    frozen={**f[1].fixed_assembly,'assembly_plan':FixedAssemblyPlan.model_validate(plan_data()).model_dump()}
    expected=hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()
    result,book=run_flow(f)
    assert result.approved and book['config_sha256']==expected
    assert 'support_checkpoints' not in book and 'support_evidence' not in book['feedback']
    assert 'support_round' not in book['versions']['original']['reviews']


def test_d_rejected_candidate_resume_does_not_reserve_again(tmp_path,monkeypatch):
    f,checks,inputs,refs,published=d_fixture(tmp_path,monkeypatch,[12,12])
    result,book=run_flow(f)
    counts=(len(f[-1]),len(checks),len(inputs))
    result,book=run_flow(f)
    assert (len(f[-1]),len(checks),len(inputs))==counts
    assert book['working']==book['retained']=='original'


def test_a_internal_visual_repair_still_compares_original_support(tmp_path,monkeypatch):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,5,12])
    execute=flow.execute_asset_source
    def appearance(source,out,**kw):
        result=execute(source,out,**kw)
        mock_flow.appearance=len(f[-1])!=1
        return result
    monkeypatch.setattr(flow,'execute_asset_source',appearance)
    async def code(**kw):
        from adsl.agents.models import GradedCodeCriticDecision
        approved=len(f[-1])!=1
        return GradedCodeCriticDecision(approved=approved,observations=[],
            required_changes=[] if approved else ['Restore silhouette'],
            issues=[] if approved else [dict(severity='HIGH',aspect='geometry',target=None,
                problem='Silhouette changed',suggested_fix='Restore silhouette')])
    monkeypatch.setattr(f[0],'_review_generation_code',code)
    result,book=run_flow(f)
    assert len(inputs)==2 and all(p['support_baseline_version']=='original' for p in inputs)
    assert inputs[1]['source_version']=='attempt_0001'
    assert book['versions']['attempt_0002']['reviews']['support_postcheck']['rule']=='no_support_gain'
    assert book['retained']==book['qualified']==book['working']=='original'


def test_measurement_unavailable_restores_baseline(tmp_path,monkeypatch):
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,8])
    run=physics.run_assembly_checks
    def missing(specs,**kw):
        rows=run(specs,**kw)
        if f[-1]:
            rows[0].result.status='INDETERMINATE'
            rows[0].result.metrics['support']={'status':'INDETERMINATE','reason':'backend_missing'}
            rows[0].result.findings=[]
            write_json(rows[0].output_dir/'result.json',rows[0].result.model_dump())
        return rows
    monkeypatch.setattr(physics,'run_assembly_checks',missing)
    result,book=run_flow(f)
    assert book['retained']==book['working']=='original'
    assert book['versions']['attempt_0001']['reviews']['support_postcheck']['rule']=='support_unavailable'


def test_d_budget_exhaustion_restores_original_reference(tmp_path,monkeypatch):
    f,checks,inputs,refs,published=d_fixture(tmp_path,monkeypatch,[12,8],bad_middle=True)
    result,book=run_flow(f)
    assert not book['versions']['attempt_0001']['reviews']['accepted']
    assert book['retained']==book['working']=='original'
    assert book['partition_reference']==refs[0] and not book.get('active_partition_intent')


@pytest.mark.parametrize('shape_editable',[False,True])
def test_d_authorized_grouping_keeps_real_intent(tmp_path,monkeypatch,shape_editable):
    from adsl.agents.models import PrintGroupingChange
    f,checks,inputs,refs,published=d_fixture(tmp_path,monkeypatch,[12,8])
    config=deepcopy(f[1].fixed_assembly)
    config['physics']['overhang']['shape_editable']=shape_editable
    request=replace(f[1],fixed_assembly=config,
        repair_policy=f[1].repair_policy.model_copy(update={'print_partition_editable':True}))
    f=(f[0],request,*f[2:])
    execute=flow.execute_asset_source
    def grouped(source,out,**kw):
        result=execute(source,out,**kw)
        if f[-1]:
            path=out/'assembly/assembly_manifest.json';m=json.loads(path.read_text())
            m['part_declarations']=[dict(id='body',components=['crossbar'],assembly_transform=np.eye(4).tolist()),
                                   dict(id='stem',components=['stem'],assembly_transform=np.eye(4).tolist())]
            m['parts'].append(dict(id='stem',print_transform_mm=np.eye(4).tolist()))
            write_json(path,m)
        return result
    monkeypatch.setattr(flow,'execute_asset_source',grouped)
    original_run=physics.run_assembly_checks
    def measurements(specs,**kw):
        results=original_run(specs,**kw)
        for r in results:
            r.result.metrics['partition_objective']['print_part_count']=2 if f[-1] else 1
            r.result.findings=[physics.finding('assembly_overhang','PRINT_PARTITION_OPPORTUNITY','grouping',
                category='optimization_opportunity',repairability='design_variable',required=False)]
            write_json(r.output_dir/'result.json',r.result.model_dump())
        return results
    monkeypatch.setattr(physics,'run_assembly_checks',measurements)
    # This stub isolates regroup routing, not real shape or interface validity.
    from adsl.agents import partition_score as scoring
    monkeypatch.setattr(scoring,'reference_shape_comparison',lambda *a,**kw:dict(
        status='MATCH',semantic_ownership_preserved=True,root_frame_preserved=True))
    async def split(*a,**kw):
        return RepairProposal(proposal_id='split',finding_ids=['support'],hypothesis='split stem',
            target=RepairTarget(),action='regroup_print_parts',grouping_change=PrintGroupingChange(
                operation='split',source_part_ids=['body'],target_print_parts=[
                    dict(id='body',components=['crossbar']),dict(id='stem',components=['stem'])],
                connection_changes=['create body/stem connection']))
    monkeypatch.setattr(topology,'engineer',split)
    result,book=run_flow(f)
    candidate=book['versions']['attempt_0001']['reviews']
    assert candidate['grouping_realization']['status']=='MATCH'
    assert candidate['support_postcheck']['rule']=='split_support_gain'
    assert inputs[0]['partition_intent']['grouping_change']['operation']=='split'
    assert book['retained']=='attempt_0001' and book.get('active_partition_intent') is None


def test_engineering_actual_input_uses_support_not_dapper(tmp_path,monkeypatch):
    import asyncio
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.models import ObjectRequest, FixedAssemblyPlan
    from test_fixed_assembly import CONFIG, plan_data
    source=tmp_path/'source.py';source.write_text('scene = None\n')
    config=deepcopy(CONFIG)
    config['physics']={'overhang':dict(support_metric='nsv',shape_editable=True,
        layer_height_mm=.2,overhang_threshold_from_horizontal_deg=45,
        partition_objective={'alpha':.2})}
    request=ObjectRequest('fixture',tmp_path,'fixture',fixed_assembly=config)
    captured=[]
    class Runtime:
        def agent(self,**kw):return kw
        async def run(self,**kw):
            captured.append(kw)
            return SimpleNamespace(final_output={'approved':False,'observations':['No justified local change'],
                'repair_proposals':[],'unresolved_findings':[]})
    evidence=dict(source_version='original',source_sha256=flow.file_hash(source),
        support=measurement(12)['support'],part_count=1,tolerance_mm3=1.2,items=[])
    spec=physics.checker_spec('assembly_overhang')
    result=CheckerResult(checker=spec.name,status='PASS',summary='measured support',
        metrics={'partition_objective':{'score':999,'alpha':.2,'gap_voxels':5},'items':[]})
    run=CheckerRun(spec,result,tmp_path/'checkers',())
    feedback=dict(source_version='original',support_evidence=evidence,repair_history=[],
                  partition_guidance={'score':999},next_edit_purpose='partition_optimization')
    workflow=ObjectWorkflow.__new__(ObjectWorkflow)
    answer=asyncio.run(topology.engineer(workflow,Runtime(),request,
        FixedAssemblyPlan.model_validate(plan_data()),source,None,tmp_path/'engineering',run,
        {'current_assembly':None},feedback,1))
    assert answer is None and len(captured)==1
    assert 'Dapper' not in captured[0]['agent']['instructions']
    saved=json.loads((tmp_path/'engineering/engineering_input.json').read_text())
    assert saved['support_evidence']['part_count']==1
    assert 'partition_measurement' not in saved and 'partition_guidance' not in saved
    assert feedback['engineering']['status']=='NO_PROPOSAL'


def test_initial_and_resumed_coder_receive_nsv_policy(tmp_path,monkeypatch):
    import asyncio
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.models import ObjectRequest, FixedAssemblyPlan
    from test_fixed_assembly import CONFIG, plan_data
    config=deepcopy(CONFIG)
    config['physics']={'overhang':dict(support_metric='nsv',shape_editable=True,
        layer_height_mm=.2,overhang_threshold_from_horizontal_deg=45)}
    workspace=tmp_path/'new'
    request=ObjectRequest('fixture',workspace,'test',fixed_assembly=config)
    captures=[]
    class Runtime:
        usage=SimpleNamespace(update_manifest=lambda **kw:None)
        def agent(self,**kw):return kw
        async def run(self,**kw):
            if kw['role']=='planner':
                assert 'Support optimization uses' in kw['agent']['instructions']
                return SimpleNamespace(final_output=FixedAssemblyPlan.model_validate(plan_data()))
            captures.append(kw)
            context=kw['context'];context.source_path.write_text('scene = None\n')
            context.record('write_file',context.source_path)
            return SimpleNamespace(final_output='done')
    w=ObjectWorkflow.__new__(ObjectWorkflow)
    monkeypatch.setattr(w,'_runtime',lambda *a,**kw:Runtime())
    async def stop(**kw):return 'fixture stopped before executor'
    monkeypatch.setattr(w,'_iterate',stop)
    assert asyncio.run(w.generate(request))=='fixture stopped before executor'
    (workspace/'source.py').write_text('')
    write_json(workspace/'checkpoint.json',{'mode':'generate','stage':'planned'})
    assert asyncio.run(w.resume(request))=='fixture stopped before executor'
    assert len(captures)==2
    for capture in captures:
        prompt=capture['agent']['instructions']
        assert 'Support optimization uses' in prompt and 'Dapper' not in prompt
    assert config==request.fixed_assembly
