"""Real measurement and existing typed Engineering adapter; model calls mocked."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

from adsl.agents import assembly_physics as physics, assembly_topology as topology
from adsl.agents.assembly_overhang import analyze
from adsl.agents.checkers import CheckerRun
from adsl.agents.models import (CheckerResult, RepairPolicy, RepairProposal, RepairTarget,
    PrintGroupingChange, EngineeringCriticDecision, FixedAssemblyPlan)
from adsl.agents.partition_score import OBJECTIVE, create_reference
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.utils.io import write_json
from test_assembly_physics import fixture, OVERHANG
from test_fixed_assembly import mock_flow, plan_data
from test_assembly_feedback_recovery import tool, payload
from adsl.agents.tools.files import read_file


def measured_fixture(root):
    args,manifest=fixture(root)
    _,_,meshes=physics.load_parts(args)
    inputs=[]
    for name,mesh in meshes.items():
        file=root/f'{name}.body.npz';np.savez(file,vertices=mesh.vertices,faces=mesh.faces)
        inputs.append(dict(part_id=name,status='PASS',frame='part_local_mm',npz=file.name,
            sha256=sha256_file(file),source_sha256=sha256_file(args.source)))
    manifest['partition_reference_inputs']=inputs;write_json(args.manifest,manifest)
    reference=create_reference(args.manifest,root/'references')
    result=analyze(args,{'overhang':dict(OVERHANG,partition_objective=OBJECTIVE,partition_editable=True),
        'partition_reference_path':str(reference)})
    return args,manifest,result


def test_selected_check_order_and_topology_fail_does_not_gate(tmp_path,monkeypatch):
    from adsl.agents.utils.execution import ExecutionResult
    calls=[]
    def run(spec,**kw):
        calls.append(spec.name)
        return CheckerRun(spec,CheckerResult(checker=spec.name,
            status='FAIL' if spec.name=='assembly_topology' else 'PASS',summary='fixture'),tmp_path,())
    monkeypatch.setattr(topology,'run_assembly_topology',run)
    monkeypatch.setattr(physics,'run_checker',run)
    specs=[physics.checker_spec('assembly_standing'),physics.checker_spec('assembly_overhang'),topology.checker_spec()]
    runs=physics.run_assembly_checks(specs,execution=ExecutionResult(tmp_path,tmp_path/'scene.glb',None,(),'', ''),
        source=tmp_path/'source.py',root=tmp_path,physics={})
    assert calls==['assembly_topology','assembly_overhang','assembly_standing']
    assert [r.result.status for r in runs]==['FAIL','PASS','PASS']


@pytest.mark.parametrize('permission,unknown,accepted',[(True,False,True),(False,False,False),(True,True,False)])
def test_measured_partition_reaches_engineering(tmp_path,monkeypatch,permission,unknown,accepted):
    args,manifest,result=measured_fixture(tmp_path/'geometry')
    assert result.status=='PASS' and result.metrics['partition_objective']['score'] is not None
    assert any(f.rule_id=='PRINT_PARTITION_OPPORTUNITY' for f in result.findings)
    state=mock_flow(tmp_path/'workflow',monkeypatch,[('PASS',True)])
    workflow,request,runtime,_,_=state
    # Evidence is physically measured; only the language model is mocked.
    request=replace(request,workspace=tmp_path,repair_policy=RepairPolicy(print_partition_editable=permission),
        fixed_assembly={**request.fixed_assembly,'physics':{'overhang':{'partition_objective':OBJECTIVE}}})
    feedback=dict(source_version='original',checker_summary=[],typed_findings=[],evidence_files=[],
        next_edit_purpose='partition_optimization',primary_objective='Evaluate one grouping hypothesis',
        comparison_baseline_version='original',partition_guidance=result.metrics['partition_guidance'])
    context={'current_assembly':{'root_id':'base','parts':manifest['part_declarations'],
                                 'connections':manifest['connections']}}
    async def model(**kw):
        data=payload(kw)
        if permission:
            assert 'alternative score is not required' in data['assignment']
            assert 'do not invent the candidate score' in data['assignment']
        assert data['partition_measurement'][0]['partition_objective']['reference_sha256']
        assert data['partition_measurement'][0]['partition_guidance']==result.metrics['partition_guidance']
        assert all(x['gap_voxels'] is not None for x in data['partition_measurement'][0]['items'])
        assert data['assembly_context']['current_assembly']['connections']==manifest['connections']
        assert data['next_edit_purpose']=='partition_optimization'
        assert data['comparison_baseline_version']==data['source_version']=='original'
        assert data['primary_objective']==feedback['primary_objective']
        assert data['partition_guidance']==result.metrics['partition_guidance']
        await tool(read_file,kw['context'],'read_source',path=str(args.source))
        proposal=RepairProposal(proposal_id='merge',finding_ids=[result.findings[0].finding_id],
            hypothesis='One fewer piece may improve the measured objective',target=RepairTarget(),
            action='regroup_print_parts',grouping_change=PrintGroupingChange(operation='merge',
                source_part_ids=['missing' if unknown else 'base','stem'],
                target_print_parts=[{'id':'base','components':['base','stem']}],
                connection_changes=['remove internal joint']))
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,observations=[],repair_proposals=[proposal]))
    runtime.run=model
    run=CheckerRun(physics.checker_spec('assembly_overhang'),result,args.output,())
    proposal=asyncio.run(topology.engineer(workflow,runtime,request,FixedAssemblyPlan.model_validate(plan_data()),
        args.source,None,tmp_path,run,context,feedback,1))
    assert (proposal is not None)==accepted
    assert (tmp_path/'engineering_input.json').is_file()


def test_zero_gap_still_has_grouping_opportunity_and_missing_piece_unknown(tmp_path):
    args,manifest=fixture(tmp_path,pair=False)
    _,_,meshes=physics.load_parts(args)
    file=tmp_path/'base.body.npz';np.savez(file,vertices=meshes['base'].vertices,faces=meshes['base'].faces)
    manifest['partition_reference_inputs']=[dict(part_id='base',status='PASS',frame='part_local_mm',npz=file.name,
        sha256=sha256_file(file),source_sha256=sha256_file(args.source))]
    write_json(args.manifest,manifest)
    reference=create_reference(args.manifest,tmp_path/'references')
    cfg={'overhang':dict(OVERHANG,partition_objective=OBJECTIVE,partition_editable=True),
         'partition_reference_path':str(reference)}
    result=analyze(args,cfg)
    assert result.metrics['partition_objective']['gap_voxels']==0 and result.findings
    manifest['part_declarations'].append(dict(id='missing',components=['missing']))
    write_json(args.manifest,manifest)
    result=analyze(args,cfg)
    assert result.status=='INDETERMINATE'
    assert result.metrics['partition_objective']['score'] is None
    assert result.metrics['partition_objective']['print_part_count']==2


def test_split_ownership_root_and_merge_connectivity_guards():
    assembly={'root_id':'frame','parts':[dict(id='frame',components=['left','right','front']),
        dict(id='top',components=['top'])], 'connections':[]}
    split=PrintGroupingChange(operation='split',source_part_ids=['frame'],target_print_parts=[
        {'id':'frame','components':['left','right']},{'id':'front','components':['front']}],connection_changes=['new brace mates'])
    split.validate_current_assembly(assembly)
    for targets in ([{'id':'frame','components':['left','right']}],
                    [{'id':'renamed','components':['left','right']},{'id':'front','components':['front']}],
                    [{'id':'frame','components':['left','right']},{'id':'top','components':['front']}],
                    [{'id':'frame','components':['left','right']},{'id':'front','components':['right','front']}]):
        bad=split.model_copy(update={'target_print_parts':[type(split.target_print_parts[0])(**x) for x in targets]})
        with pytest.raises(ValueError):bad.validate_current_assembly(assembly)
    merge=PrintGroupingChange(operation='merge',source_part_ids=['frame','top'],target_print_parts=[
        {'id':'frame','components':['left','right','front','top']}])
    with pytest.raises(ValueError,match='must be connected'):merge.validate_current_assembly(assembly)


def test_semantic_instance_grouping_and_initial_action_guard():
    from adsl.agents.assembly_topology import validate_partition_proposal, InvalidPartitionProposal
    members=['torso','front_left_leg','front_right_leg','rear_left_leg','rear_right_leg']
    assembly={'root_id':'body','parts':[{'id':'body','components':members}], 'connections':[]}
    change=PrintGroupingChange(operation='split',source_part_ids=['body'],target_print_parts=[
        {'id':'body','components':[m for m in members if m!='front_left_leg']},
        {'id':'leg','components':['front_left_leg']}],connection_changes=['add one paired leg interface'])
    proposal=RepairProposal(proposal_id='leg',finding_ids=['f'],hypothesis='local split',target=RepairTarget(),
        action='regroup_print_parts',grouping_change=change)
    validate_partition_proposal(proposal,purpose='partition_optimization',assembly=assembly,editable=True)
    wrong=proposal.model_copy(update={'action':'reshape','grouping_change':None})
    with pytest.raises(InvalidPartitionProposal):
        validate_partition_proposal(wrong,purpose='partition_optimization',assembly=assembly,editable=True)
    validate_partition_proposal(wrong,purpose='partition_optimization',continuing=True)
    validate_partition_proposal(wrong,purpose='required_repair')
    old={**assembly,'parts':[{'id':'body','components':['torso','four_legs']}]}
    with pytest.raises(InvalidPartitionProposal,match='semantic ownership'):
        validate_partition_proposal(proposal,purpose='partition_optimization',assembly=old,editable=True)
    for bad in [members[:-1],members+[members[-1]]]:
        broken=change.model_copy(update={'target_print_parts':[{'id':'body','components':bad}]})
        # Existing DTO validation, including duplicate/missing semantics, remains active.
        with pytest.raises((ValueError,AttributeError)):
            broken.validate_current_assembly(assembly)


def test_engineering_receives_api_reference_without_planner_assignment():
    from adsl.agents.prompts import object_prompt
    prompt=object_prompt('engineering_critic',articulation=False,fixed_assembly=True)
    assert 'TabSlot' in prompt and 'add_part' in prompt and 'connect' in prompt
    assert 'Optional fixed manufacturing assembly v1' not in prompt
    coder=object_prompt('coder',articulation=False,fixed_assembly=True)
    assert 'front_left_leg' in coder and 'There is no documented .children' in coder


@pytest.mark.parametrize('area_available',[True,False])
def test_recommended_regions_reuse_measurement_in_print_mm(tmp_path,monkeypatch,area_available):
    from adsl.agents import assembly_overhang as overhang,partition_score as score
    source=tmp_path/'source.py';source.write_text('stub source')
    args=SimpleNamespace(source=source,manifest=tmp_path/'manifest.json',output=tmp_path)
    args.manifest.write_text('{}')
    transform=np.eye(4);transform[:3,:3]=[[0,-1,0],[1,0,0],[0,0,1]];transform[:3,3]=[3,4,5]
    class Mesh:
        def copy(self):return Mesh()
        def apply_transform(self,t):pass
        def export(self,path,**kw):Path(path).write_text('stub STL')
    (tmp_path/'body.solid.npz').write_text('stub identity')
    reference={'reference_sha256':'ref','reference_voxels':100,'voxel_pitch_mm':1}
    monkeypatch.setattr(score,'load_reference',lambda *a:reference)
    searches=[];measurements=[]
    def search(*a):
        searches.append(1)
        return dict(gap_voxels=5,selected_rotation_id=3,recommended_print_transform_mm=transform.tolist())
    monkeypatch.setattr(score,'best_print_pose',search)
    point=transform@np.array([1,2,3,1])
    def measure(mesh,t,config):
        measurements.append(np.asarray(t))
        return {'area_mm2':6,'regions':[dict(area_mm2=6,centroid_mm=point[:3].tolist(),
            bounds_mm=[point[:3].tolist(),point[:3].tolist()],global_face_ids=list(range(100))) for _ in range(5)]},Mesh()
    monkeypatch.setattr(overhang,'measure_part',measure)
    monkeypatch.setattr(overhang,'_verify_recommended_stl',lambda *a:{'status':'PASS'})
    monkeypatch.setattr(overhang,'_display_overlay',lambda *a:{'status':'PASS'})
    result=CheckerResult(checker='assembly_overhang',status='PASS',summary='stub',
        metrics={'items':[dict(part_id='body',status='PASS',area_mm2=42)]})
    result=overhang.partition_measurement(args,{'overhang':{'partition_objective':score.OBJECTIVE},
        'partition_reference_path':'stub'}, {'parts':[{'id':'body'}],'source_sha256':sha256_file(source)}, {'body':Mesh()},result,area_available)
    row=result.metrics['items'][0]
    assert len(searches)==1 and len(measurements)==int(area_available)
    assert row['input_pose_area_mm2']==42
    if area_available:
        regions=row['recommended_pose_regions']
        assert regions['frame']=='recommended_print:body' and regions['length_unit']=='mm'
        assert len(regions['regions'])==3 and 'global_face_ids' not in str(regions)
        restored=np.linalg.inv(regions['part_local_to_print_mm'])@np.r_[regions['regions'][0]['centroid_mm'],1]
        assert np.allclose(restored,[1,2,3,1])
        full=json.loads(Path(regions['evidence_ref']['path']).read_text())
        assert len(full['regions'])==5 and len(full['regions'][0]['global_face_ids'])==100
    else:
        assert 'recommended_pose_regions' not in row
