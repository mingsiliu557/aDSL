"""Mock model/geometry, REAL _repair/read_file/apply_patch through tool wrappers."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from agents.tool_context import ToolContext

from adsl.agents import assembly_topology as adapter, fixed_assembly as flow
from adsl.agents.models import EngineeringCriticDecision, RepairProposal, RepairTarget, FixedAssemblyPlan
from adsl.agents.overhang_edit import file_hash
from adsl.agents.service import ObjectWorkflow
from adsl.agents.tools.files import read_file, apply_patch
from test_assembly_topology import setup_flow
from test_fixed_assembly import run_flow, plan_data


async def tool(tool, ctx, call_id, **params):
    wire=json.dumps(params)
    wrapper=ToolContext(ctx,tool_name=tool.name,tool_call_id=call_id,tool_arguments=wire)
    return await tool.on_invoke_tool(wrapper,wire)


def payload(call):
    value=call['input']
    return json.loads(value if isinstance(value,str) else value[0]['content'][0]['text'])


@pytest.mark.parametrize('engineering_mode',['unresolved_scope','parse_error','invalid_id','empty'])
def test_two_feedback_sources_one_real_repair_with_path_recovery(tmp_path,monkeypatch,engineering_mode):
    state=setup_flow(tmp_path,monkeypatch,['FAIL','PASS'])
    w,request,runtime,source,_=state
    request=replace(request,max_rounds=2)
    source.write_text('class Piece:\n    value = 1\n')
    initial_hash=file_hash(source)
    boundary=tmp_path/'boundary_localization.json';boundary.write_text('{"edges":14}')
    evidence=[dict(path=str(boundary),purpose='initial boundary locations',
                   source_sha256=initial_hash,version_role='initial_source_only')]
    execute=flow.execute_asset_source
    executed=[]
    def generate(*a,**kw):
        ex=execute(*a,**kw);executed.append(ex)
        # Both appearance and geometry need repair on the first evaluation.
        from test_fixed_assembly import mock_flow
        mock_flow.appearance=len(executed)>1
        return ex
    monkeypatch.setattr(flow,'execute_asset_source',generate)
    monkeypatch.setattr(w,'_repair',ObjectWorkflow._repair.__get__(w))
    calls=[]
    async def model(**kw):
        data=payload(kw);calls.append((kw['role'],data))
        ctx=kw['context']
        if kw['role'].startswith('engineering'):
            assert data['pending_reviews']['code_critic']['required_changes']==['repair']
            assert len(data['typed_findings'])==2
            assert data['evidence_files'][0]['path']==str(boundary)
            await tool(read_file,ctx,'engineer_source',path=data['assigned_source'])
            if engineering_mode=='parse_error': return SimpleNamespace(final_output='{broken proposal')
            proposal=RepairProposal(proposal_id='advice',finding_ids=['piece:disconnected'],
                hypothesis='local source correction',target=RepairTarget(allowed_scopes=['Piece','Piece.value']),
                action='reshape',possible_regressions=['Preserve the visible body'])
            if engineering_mode=='invalid_id': proposal.finding_ids=['invented finding']
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['Cause remains uncertain'],repair_proposals=[] if engineering_mode=='empty' else [proposal]))
        assert kw['role']=='coder:assembly:2'
        feedback=data['feedback']
        assert feedback['code_critic']['required_changes']==['repair']
        assert feedback['checker_summary'][0]['status']=='FAIL'
        assert feedback['actionable_finding_ids']==['piece:disconnected','joint:misplaced']
        assert data['evidence_files']==calls[0][1]['evidence_files']
        assert data['source_sha256']==initial_hash
        assert data['current_repair_authorized'] and data['remaining_repairs_after_this_attempt']==0
        if engineering_mode=='unresolved_scope':
            assert feedback['engineering_proposal']['target']['allowed_scopes']==['Piece']
            assert feedback['engineering']['unresolved_location_hints']==['Piece.value']
        else:
            assert 'engineering_proposal' not in feedback
            assert feedback['engineering']['status'] in ('NO_PROPOSAL','UNAVAILABLE')
        wrong=str(ctx.source_path.parent/boundary.name)
        error=json.loads(await tool(read_file,ctx,'wrong_path',path=wrong))
        assert error['code']=='READ_NOT_FOUND'
        exact=data['evidence_files'][0]['path']
        assert json.loads(await tool(read_file,ctx,'correct_path',path=exact))['edges']==14
        text=await tool(read_file,ctx,'coder_source',path=data['assigned_source'])
        await tool(apply_patch,ctx,'patch',path=data['assigned_source'],old_text=text,
                   new_text=text.replace('value = 1','value = 2'))
        return SimpleNamespace(final_output='Applied local edit; recheck required.')
    runtime.run=model
    final=asyncio.run(flow.iterate_fixed_assembly(w,runtime=runtime,request=request,workspace=tmp_path,
        source_path=source,plan=FixedAssemblyPlan.model_validate(plan_data()),evidence_files=evidence))
    book=json.loads((tmp_path/'assembly_versions.json').read_text())
    assert final.approved and book['retained']=='attempt_0001' and len(executed)==2
    assert len(calls)==2 and len((tmp_path/'repair_history.jsonl').read_text().splitlines())==1
    assert file_hash(tmp_path/'original/source.py')==initial_hash
    assert 'value = 2' in source.read_text()
    outcome=json.loads(next((tmp_path/'rounds/round_02').rglob('edit_outcome.json')).read_text())
    assert outcome['status']=='CHANGED'
    assert [e['success'] for e in outcome['tool_events']]==[False,True,True,True]
    assert book['feedback']['evidence_files'][0]['version_role']=='initial_source_only'
    assert not book['feedback']['evidence_files'][0]['matches_current_source']
    checks=json.loads((tmp_path/'checker_results.json').read_text())
    assert checks['source_sha256']==file_hash(source)==checks['results'][0]['assumptions']['source_sha256']


def test_no_actionable_geometry_does_not_blindly_edit(tmp_path,monkeypatch):
    state=setup_flow(tmp_path,monkeypatch,['INDETERMINATE'])
    async def forbidden(**kw): pytest.fail('unverified/unlocated result is not a repair target')
    state[2].run=forbidden
    final,book=run_flow(state)
    assert not final.approved and not state[-1]
    assert book['versions']['original']['reviews']['image_review_status']=='COMPLETED'
    assert book['stop_reason']=='topology_unverified_no_executable_feedback'
    assert json.loads((tmp_path/'checker_results.json').read_text())['results'][0]['status']=='INDETERMINATE'


def test_real_no_change_with_no_engineering_advice_preserves_assets(tmp_path,monkeypatch):
    state=setup_flow(tmp_path,monkeypatch,['FAIL'])
    w,request,runtime,source,_=state
    monkeypatch.setattr(w,'_repair',ObjectWorkflow._repair.__get__(w))
    calls=[]
    async def model(**kw):
        calls.append(kw['role']);data=payload(kw)
        await tool(read_file,kw['context'],'read',path=data['assigned_source'])
        if kw['role'].startswith('engineering'):
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['No safe localized recommendation'],repair_proposals=[]))
        return SimpleNamespace(final_output='NO_CHANGE: no safe local modification')
    runtime.run=model
    final,book=run_flow(state)
    assert not final.approved and book['stop_reason']=='NO_CHANGE' and book['retained']=='original'
    assert (tmp_path/'source.py').read_text()==(tmp_path/'scene.glb').read_text()=='original'
    assert len(calls)==2 and len((tmp_path/'repair_history.jsonl').read_text().splitlines())==1
    # Resume of a completed ledger must not call the model or charge again.
    run_flow(state)
    assert len(calls)==2 and len((tmp_path/'repair_history.jsonl').read_text().splitlines())==1


def test_proposal_does_not_leak_into_next_round(tmp_path,monkeypatch):
    state=setup_flow(tmp_path,monkeypatch,['FAIL','FAIL','PASS'])
    seen=[]
    async def advice(*args):
        seen.append(args[-2]['source_version'])
        if len(seen)>1: return None
        return RepairProposal(proposal_id='first_only',finding_ids=['piece:disconnected'],
            hypothesis='first advice',target=RepairTarget(),action='reshape')
    monkeypatch.setattr(adapter,'engineer',advice)
    final,book=run_flow(state)
    assert final.approved and seen==['original','attempt_0001']
    proposals=[json.loads(p.read_text()) for p in sorted((tmp_path/'rounds').rglob('proposal.json'))]
    assert [p['proposal_id'] for p in proposals]==['first_only','assembly_or_appearance']
    assert proposals[1]['finding_ids']==['piece:disconnected','joint:misplaced']


def test_optional_missing_or_outside_evidence_does_not_abort(tmp_path):
    feedback={'typed_findings':[{'result_ref':'current.json','key_values':{'boundary_report':'missing.json'}}]}
    adapter.prepare_evidence(feedback,workspace=tmp_path,source_sha256='current',evidence_files=[
        {'path':str(tmp_path.parent/'outside.json'),'purpose':'optional','source_sha256':'old',
         'version_role':'initial_source_only'}])
    assert feedback['evidence_files'][0]['path'] is None
    assert feedback['evidence_files'][0]['reason']=='outside_workspace'
    assert all(row['availability']=='UNAVAILABLE' for row in feedback['evidence_files'])
    assert feedback['typed_findings'][0]['result_ref']==str(tmp_path/'current.json')


def test_evidence_normalization_does_not_mutate_version_bound_reviews(tmp_path):
    from adsl.agents.overhang_edit import version_record, assert_version
    source=tmp_path/'source.py';source.write_text('original')
    issue={'report_path':'execution_error.json'}
    failures=[{'report_path':'assembly/manifest.json','code':'EXPORT_FAILED'}]
    reviews={'geometry':{'failures':failures},'render_issue':issue}
    version=version_record('original',source,None,reviews=reviews)
    feedback={'failures':failures,'render_issue':issue}
    adapter.prepare_evidence(feedback,workspace=tmp_path,source_sha256=file_hash(source))
    assert_version(version)
    assert issue['report_path']=='execution_error.json'
    assert feedback['render_issue']['report_path']==str(tmp_path/'execution_error.json')


def test_interference_real_geometry_drives_engineering_coder_recheck(tmp_path,monkeypatch):
    """Mock LLM/appearance only; evaluate source to solids/STL and run real topology."""
    import numpy as np
    from adsl.core.assembly_topology import solid_mesh
    from adsl.agents import assembly_physics
    from adsl.agents.models import CheckerResult
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.utils.io import write_json
    from test_fixed_assembly import mock_flow
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True),('PASS',True)])
    w,request,runtime,source,_=state
    request=replace(request,max_rounds=2,checker_specs=(adapter.checker_spec(),
        assembly_physics.checker_spec('assembly_overhang')))
    source.write_text('''from dataclasses import asdict
import manifold3d as mf
import numpy as np
from adsl.core.assembly import TabSlot
from adsl.core.assembly_topology import query_solids
block_top = 2.0
p = TabSlot(12,6,6,7,.2)
tab,slot = query_solids(p)
body = mf.Manifold.cube((20,12,50),True).translate((0,0,-25.25))
block = mf.Manifold.cube((3,4,block_top+2),True).translate((8.5,0,(block_top-2)/2))
solids = {'stem': body+tab+block,
          'crossbar': mf.Manifold.cube((60,20,12),True).translate((0,0,6))-slot}
frame = np.eye(4).tolist()
connection = dict(id='joint',tab_part='stem',slot_part='crossbar',tab_port='tab',slot_port='slot',
    parameter_name='joint',parameters=asdict(p),tab_frame=frame,slot_frame=frame)
''')
    initial_hash=file_hash(source)
    stub_export=flow.execute_asset_source;generated=[]
    def export(current,out,**kwargs):
        execution=stub_export(current,out,**kwargs)
        namespace={};exec(compile(current.read_text(),str(current),'exec'),namespace)
        folder=execution.output_root/'assembly';parts=[]
        for name,solid in namespace['solids'].items():
            solid_mesh(solid).export(folder/(name+'.stl'),file_type='stl_ascii')
            parts.append(dict(id=name,stl=name+'.stl',components=[name],
                assembly_transform=np.eye(4).tolist(),print_transform_mm=np.eye(4).tolist()))
        manifest=dict(status='PASS',failures=[],source_sha256=file_hash(current),mm_per_unit=1.,
            parts=parts,part_declarations=parts,connections=[namespace['connection']],
            files_sha256={p.name:file_hash(p) for p in folder.glob('*.stl')})
        write_json(folder/'assembly_manifest.json',manifest);generated.append(file_hash(current))
        return execution
    monkeypatch.setattr(flow,'execute_asset_source',export)
    monkeypatch.setattr(w,'_repair',ObjectWorkflow._repair.__get__(w))
    other_checks=[]
    def other_check(spec,**kw):
        other_checks.append(file_hash(kw['source_path']))
        folder=kw['round_root']/'checkers'/spec.name;folder.mkdir(parents=True)
        result=CheckerResult(checker=spec.name,status='PASS',summary='mock unmodified secondary checker')
        write_json(folder/'result.json',result.model_dump())
        return CheckerRun(spec,result,folder,())
    monkeypatch.setattr(assembly_physics,'run_checker',other_check)
    calls=[]
    async def model(**kw):
        data=payload(kw);calls.append((kw['role'],data));ctx=kw['context']
        text=await tool(read_file,ctx,'current_source',path=data['assigned_source'])
        feedback=data if kw['role'].startswith('engineering') else data['feedback']
        findings=feedback['typed_findings'];assert len(findings)==1
        finding=findings[0]
        assert finding['rule_id']=='UNDECLARED_PART_INTERFERENCE'
        assert finding['region']['part_names']==['crossbar','stem']
        assert finding['region']['frame']=='assembly' and finding['region']['bounds']
        assert finding['region']['details']['related_connection_ids']==['joint']
        assert finding['metric']['value']>finding['metric']['threshold']
        assert finding['metric']['relative_tolerance']==finding['metric']['absolute_tolerance']==0
        assert {r['checker'] for r in feedback['checker_summary']}=={'assembly_topology','assembly_overhang'}
        if kw['role'].startswith('engineering'):
            pair=next(r for r in data['assembly_item_statuses'] if r['kind']=='pair')
            assert pair['pair_id']=='crossbar:stem' and pair['part_ids']==['crossbar','stem']
            assert 'A declared connection does not excuse overlap elsewhere' in kw['agent']['instructions']
            return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
                observations=['The added block projects above the stop plane outside the joint'],repair_proposals=[
                RepairProposal(proposal_id='lower_block',finding_ids=[finding['finding_id']],
                    hypothesis='Lower the block top while preserving the interface',evidence=['Measured overlap outside the joint'],
                    target=RepairTarget(allowed_scopes=['<module>']),action='reshape')]))
        assert kw['role']=='coder:assembly:2'
        assert data['source_sha256']==initial_hash
        await tool(apply_patch,ctx,'lower_block',path=data['assigned_source'],old_text=text,
                   new_text=text.replace('block_top = 2.0','block_top = 0.0'))
        return SimpleNamespace(final_output='Lowered the block top; recheck all selected tools.')
    runtime.run=model
    final=asyncio.run(flow.iterate_fixed_assembly(w,runtime=runtime,request=request,workspace=tmp_path,
        source_path=source,plan=FixedAssemblyPlan.model_validate(plan_data())))
    book=json.loads((tmp_path/'assembly_versions.json').read_text())
    assert final.approved and book['retained']=='attempt_0001'
    assert len(calls)==2 and len(generated)==len(other_checks)==2 and generated[0]!=generated[1]
    original=book['versions']['original']['reviews']['assembly_topology']
    revised=book['versions']['attempt_0001']['reviews']['assembly_topology']
    assert original['status']=='FAIL' and revised['status']=='PASS'
    assert all(r['status']=='PASS' for r in revised['metrics']['items'])
    checks=json.loads((tmp_path/'checker_results.json').read_text())
    assert checks['source_sha256']==generated[1]==file_hash(source)
    assert file_hash(tmp_path/'original/source.py')==initial_hash


def test_unavailable_pair_does_not_request_geometric_repair(tmp_path,monkeypatch):
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.utils.io import write_json
    state=setup_flow(tmp_path,monkeypatch,['INDETERMINATE'])
    def check(spec,*,execution,source,root):
        out=root/'checkers'/adapter.NAME;out.mkdir(parents=True)
        result=adapter.make_result({},[dict(kind='pair',pair_id='crossbar:stem',part_ids=['crossbar','stem'],
            status='INDETERMINATE',code='PAIR_QUERY_UNAVAILABLE',reason='native query failed')],source,out)
        write_json(out/'result.json',result.model_dump());return CheckerRun(spec,result,out,())
    monkeypatch.setattr(adapter,'run_assembly_topology',check)
    async def forbidden(**kw):pytest.fail('unavailable intersection is not a geometric repair target')
    state[2].run=forbidden
    final,book=run_flow(state)
    assert not final.approved and not state[-1]
    assert book['stop_reason']=='topology_unverified_no_executable_feedback'
