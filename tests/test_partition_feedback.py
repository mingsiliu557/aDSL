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
    feedback=dict(source_version='original',checker_summary=[],typed_findings=[],evidence_files=[])
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
