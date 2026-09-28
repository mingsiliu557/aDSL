"""Small real Manifold tests and mock role/budget/publication contracts."""
import asyncio
from dataclasses import asdict, replace
import json
from pathlib import Path
from types import SimpleNamespace

import manifold3d as mf
import numpy as np
import pytest
import trimesh

from adsl.core.assembly import TabSlot
from adsl.core.assembly_topology import (part_measurement,query_solids,interface_measurement,solid_mesh)
from adsl.agents import assembly_topology as adapter
from adsl.agents.checkers import CheckerRun
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.models import (CheckerResult,CheckerFinding,EngineeringCriticDecision,
    RepairProposal,RepairTarget,FixedAssemblyPlan)
from adsl.agents.utils.io import write_json
from test_fixed_assembly import mock_flow,run_flow,plan_data


def fixture_pair(fit=.2):
    p=TabSlot(12,6,6,7,fit)
    tab,slot=query_solids(p)
    solids={'stem':mf.Manifold.cube((20,12,50),True).translate((0,0,-25))+tab,
            'bar':mf.Manifold.cube((60,20,12),True).translate((0,0,6))-slot}
    matrix=np.eye(4).tolist()
    c=dict(id='joint',tab_part='stem',slot_part='bar',tab_port='tab',slot_port='slot',
        parameter_name='shared',parameters=asdict(p),tab_frame=matrix,slot_frame=matrix)
    parts={k:{'assembly_transform':matrix} for k in solids}
    return c,parts,solids


@pytest.mark.parametrize('small_edge',[False,True])
def test_solid_worker_transport_preserves_float64_geometry(tmp_path,small_edge):
    # A finite positive-area face can collapse if a float64 union is stored as float32.
    edge=1e-8 if small_edge else 1.
    mesh=trimesh.Trimesh([[1.,0.,0.],[1.+edge,0.,0.],[1.,1.,0.]],[[0,1,2]],process=False)
    assert mesh.area_faces[0]>0
    if small_edge:
        lossy=trimesh.Trimesh(mesh.vertices.astype(np.float32),mesh.faces,process=False)
        assert lossy.area_faces[0]==0
    adapter._save_solid_mesh(mesh,tmp_path/'solid.npz')
    loaded=adapter._load_solid_mesh(tmp_path/'solid.npz')
    assert np.array_equal(loaded.vertices,mesh.vertices)
    assert np.array_equal(loaded.faces,mesh.faces)
    assert np.array_equal(loaded.area_faces,mesh.area_faces)


@pytest.mark.parametrize('offset,count',[(None,1),((.5,.5,.5),1),((3,0,0),2),((2,2,0),2),((2,2,2),2)])
def test_material_union_not_raw_shell_count(offset,count):
    box=mf.Manifold.cube((2,2,2),True)
    mesh=solid_mesh(box)
    if offset is not None: mesh=trimesh.util.concatenate([mesh,solid_mesh(box.translate(offset))])
    row,_=part_measurement(mesh,'piece')
    assert row['component_count']==count
    assert row['status']==('PASS' if count==1 else 'FAIL')
    if count>1: assert row['component_bounds_mm'] and row['nearest_components']


def test_cavity_is_not_a_disconnected_material_piece():
    hollow=mf.Manifold.cube((4,4,4),True)-mf.Manifold.cube((2,2,2),True)
    row,result=part_measurement(solid_mesh(hollow),'hollow')
    assert row['status']=='PASS' and row['component_count']==1
    assert result.volume()==pytest.approx(56)


def test_face_touch_union_preserves_exported_shell_groups():
    box=mf.Manifold.cube((2,2,2),True)
    a,b=solid_mesh(box),solid_mesh(box.translate((2,0,0)))
    mesh=trimesh.util.concatenate([a,b])
    row,_=part_measurement(mesh,'piece',[[0,len(a.faces)],[len(a.faces),len(mesh.faces)]])
    assert row['status']=='PASS' and row['component_count']==1


@pytest.mark.parametrize('fit',[.2,-.1])
def test_tab_slot_fit_and_zero_contact(fit):
    c,parts,solids=fixture_pair(fit)
    row=interface_measurement(c,parts,solids,1)
    assert row['status']=='PASS',row
    if fit>0: assert row['local_interference_mm3']==pytest.approx(0)
    else:
        assert row['fit_kind']=='nominal_interference'
        assert row['local_interference_mm3']>0 and row['undeclared_local_interference_mm3']==pytest.approx(0)


@pytest.mark.parametrize('change,code',[
    ('move','TAB_MISSING_OR_MISPLACED'),('missing_tab','TAB_MISSING_OR_MISPLACED'),
    ('missing_slot','SLOT_BLOCKED_BY_MATERIAL')])
def test_actual_mesh_defects_not_only_declarations(change,code):
    c,parts,solids=fixture_pair()
    if change=='move': solids['stem']=solids['stem'].translate((30,0,0))
    if change=='missing_tab': solids['stem']=mf.Manifold.cube((20,12,50),True).translate((0,0,-25))
    if change=='missing_slot': solids['bar']=mf.Manifold.cube((60,20,12),True).translate((0,0,6))
    row=interface_measurement(c,parts,solids,1)
    assert row['status']=='FAIL' and code in row['failures']


def test_open_slot_and_non_axis_mm_transform():
    c,parts,solids=fixture_pair()
    # Through slot: no floor; retain lateral local receiving boundaries.
    tab,slot=query_solids(TabSlot(**c['parameters']))
    solids['bar']=mf.Manifold.cube((60,20,5),True).translate((0,0,2.5))-slot
    angle=.3
    mat=np.array([[np.cos(angle),-np.sin(angle),0,3],[np.sin(angle),np.cos(angle),0,4],[0,0,1,5],[0,0,0,1]])
    for part in parts.values(): part['assembly_transform']=mat.tolist()
    assert interface_measurement(c,parts,solids,10)['status']=='PASS'


def test_air_is_not_a_slot_wall():
    c,parts,solids=fixture_pair()
    solids['bar']=solids['bar'].translate((100,0,0))
    row=interface_measurement(c,parts,solids,1)
    assert row['status']=='INDETERMINATE' and not row['local_wall_evidence']


def result(status,source):
    return CheckerResult(checker=adapter.NAME,status=status,summary=status,
        assumptions={'source_sha256':sha256_file(source)}, findings=[] if status=='PASS' else [
        CheckerFinding(finding_id='piece:disconnected',rule_id='INTERNAL_PART_DISCONNECTED',
            category='geometry_failure',repairability='geometry',message='piece has two material components'),
        CheckerFinding(finding_id='joint:misplaced',rule_id='INTERFACE_MISREGISTERED',
            category='geometry_failure',repairability='geometry',message='joint displaced')])


def setup_flow(tmp_path,monkeypatch,statuses):
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)]*len(statuses))
    f=(f[0],replace(f[1],checker_specs=(adapter.checker_spec(),)),*f[2:])
    def run(spec,*,execution,source,root):
        out=root/'checkers'/adapter.NAME; out.mkdir(parents=True)
        r=result(statuses.pop(0),source);write_json(out/'result.json',r.model_dump())
        return CheckerRun(spec,r,out,())
    monkeypatch.setattr(adapter,'run_assembly_topology',run)
    return f


def test_batched_engineer_source_fallback_one_budget_and_publication(tmp_path,monkeypatch):
    f=setup_flow(tmp_path,monkeypatch,['FAIL','PASS'])
    calls=[]
    async def run(**kw):
        calls.append(kw)
        assert 'piece:disconnected' in str(kw['input']) and 'joint:misplaced' in str(kw['input'])
        assert 'source.py' in str(kw['input']) and 'bridge_parent/scope-only rules do NOT apply' in kw['agent']['instructions']
        ctx=kw['context'];ctx.record('read_file',ctx.source_path)
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,observations=[],
            repair_proposals=[RepairProposal(proposal_id='coordinated',finding_ids=['piece:disconnected','joint:misplaced'],
                hypothesis='correct the grouping and local frame',evidence=['actual two components'],
                target=RepairTarget(),action='reshape')]))
    f[2].run=run
    final,book=run_flow(f)
    assert final.approved and len(calls)==len(f[-1])==1
    assert book['retained']=='attempt_0001'
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines())==1
    checks=json.loads((tmp_path/'checker_results.json').read_text())
    assert checks['results'][0]['status']=='PASS'
    assert checks['source_sha256']==sha256_file(tmp_path/'source.py')
    assert f[-1][0]['payload']['feedback']['engineering_proposal']['proposal_id']=='coordinated'


def test_empty_proposal_allows_coder_to_decline_and_saves_original(tmp_path,monkeypatch):
    f=setup_flow(tmp_path,monkeypatch,['FAIL'])
    async def stop(*a,**kw): return None
    monkeypatch.setattr(adapter,'engineer',stop)
    async def decline(**kw):
        f[-1].append(kw)
        return {'status':'NO_CHANGE','reason':'No safe local change'}
    monkeypatch.setattr(f[0],'_repair',decline)
    final,book=run_flow(f)
    assert not final.approved and len(f[-1])==1
    assert book['retained']=='original' and book['stop_reason']=='NO_CHANGE'
    assert (tmp_path/'source.py').read_text()=='original'


def test_one_round_budget_does_not_call_engineer(tmp_path,monkeypatch):
    f=setup_flow(tmp_path,monkeypatch,['FAIL'])
    f=(f[0],replace(f[1],max_rounds=1),*f[2:])
    async def forbidden(*a,**kw): raise AssertionError('no repair budget')
    monkeypatch.setattr(adapter,'engineer',forbidden)
    final,book=run_flow(f)
    assert not final.approved and not f[-1] and book['retained']=='original'
    assert json.loads((tmp_path/'checker_results.json').read_text())['results'][0]['status']=='FAIL'


def test_timeout_preserves_partial_failure_and_unverified(tmp_path,monkeypatch):
    source=tmp_path/'source.py';source.write_text('original')
    out=tmp_path/'checkers'/adapter.NAME;out.mkdir(parents=True)
    write_json(out/'report.json',{'source_sha256':sha256_file(source),'items':[
        {'kind':'part','part_id':'bad','status':'FAIL','code':'INTERNAL_PART_DISCONNECTED'}]})
    raw=CheckerResult(checker=adapter.NAME,status='ERROR',summary='timeout',violations=[{'code':'CHECKER_TIMEOUT'}])
    monkeypatch.setattr(adapter,'run_checker',lambda *a,**kw:CheckerRun(adapter.checker_spec(),raw,out,()))
    run=adapter.run_assembly_topology(adapter.checker_spec(),execution=None,source=source,root=tmp_path)
    assert run.result.status=='FAIL'
    assert run.result.metrics['items'][-1]['status']=='INDETERMINATE'
    assert (out/'execution_error.json').exists() and source.read_text()=='original'


def test_real_subprocess_invalid_part_does_not_block_good_part(tmp_path):
    source=tmp_path/'source.py';source.write_text('not executed')
    folder=tmp_path/'assembly';folder.mkdir()
    good=solid_mesh(mf.Manifold.cube((2,2,2),True))
    bad=good.copy();bad.update_faces(np.arange(len(bad.faces)-1))
    good.export(folder/'good.stl');bad.export(folder/'bad.stl')
    parts=[dict(id=name,stl=name+'.stl',print_transform_mm=np.eye(4).tolist(),
                assembly_transform=np.eye(4).tolist(),components=[name]) for name in ('bad','good')]
    write_json(folder/'assembly_manifest.json',dict(source_sha256=sha256_file(source),mm_per_unit=1,
        parts=parts,part_declarations=parts,connections=[],
        files_sha256={p.name:sha256_file(p) for p in folder.glob('*.stl')}))
    args=SimpleNamespace(source=source,manifest=folder/'assembly_manifest.json',
        output=tmp_path/'check',source_index=None,budget_seconds=30)
    args.output.mkdir()
    adapter.measure(args)
    result=json.loads((args.output/'result.json').read_text())
    assert result['status']=='INDETERMINATE'
    bad,good=[r for r in result['metrics']['items'] if r['kind']=='part']
    assert bad['status']=='INDETERMINATE' and 'open' in bad['reason']
    assert bad['code']=='OPEN_PRINT_MESH' and bad['boundary_edge_count']==3
    assert bad['bounds_mm'] and Path(bad['boundary_report']).is_file()
    assert good['status']=='PASS'


def test_localized_open_mesh_reaches_engineer_and_coder_without_becoming_fail(tmp_path,monkeypatch):
    f=setup_flow(tmp_path,monkeypatch,['INDETERMINATE','INDETERMINATE'])
    def check(spec,*,execution,source,root):
        out=root/'checkers'/adapter.NAME;out.mkdir(parents=True)
        rows=[dict(kind='part',part_id='shelf',status='INDETERMINATE',code='OPEN_PRINT_MESH',
            boundary_edge_count=14,bounds_mm=[[-37,-24,2],[40,-7,2]],
            boundary_regions=[{'bounds_mm':[[-37,-24,2],[23,-23,2]],'boundary_edge_count':4}]),
            dict(kind='interface',connection_id='joint',status='INDETERMINATE',
                 code='DEPENDENCY_MESH_UNAVAILABLE',tab_part='shelf',slot_part='frame')]
        r=adapter.make_result({},rows,source,out)
        write_json(out/'result.json',r.model_dump())
        return CheckerRun(spec,r,out,())
    monkeypatch.setattr(adapter,'run_assembly_topology',check)
    f=(f[0],replace(f[1],max_rounds=2),*f[2:])
    calls=[]
    async def run(**kw):
        calls.append(kw)
        text=str(kw['input'])
        assert 'OPEN_PRINT_MESH' in text and 'boundary_edge_count' in text and '14' in text
        assert 'boundary_regions_preview' in text and 'INDETERMINATE' in text
        assert 'NOT confirmed disconnection' in kw['agent']['instructions']
        ctx=kw['context'];ctx.record('read_file',ctx.source_path)
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,observations=[],
            repair_proposals=[RepairProposal(proposal_id='local',
                finding_ids=['assembly_topology:shelf:OPEN_PRINT_MESH'],
                hypothesis='inspect local decoration at measured boundary; cause uncertain',
                evidence=['14 exported boundary edges'],target=RepairTarget(),action='reshape')]))
    f[2].run=run
    final,book=run_flow(f)
    assert len(calls)==len(f[-1])==1
    assert 'OPEN_PRINT_MESH' in str(f[-1][0]['payload']['feedback']['typed_findings'])
    assert not final.approved and book['retained']=='original'
    assert book['versions']['attempt_0001']['reviews']['assembly_topology']['status']=='INDETERMINATE'
    assert (tmp_path/'source.py').read_text()=='original'


def test_unlocated_or_infrastructure_error_is_not_source_repair_feedback(tmp_path):
    from adsl.agents.service import _checker_evidence,_actionable_findings
    source=tmp_path/'source.py';source.write_text('original')
    rows=[dict(kind='part',part_id='part',status='INDETERMINATE',code='CHECKER_UNAVAILABLE'),
          dict(kind='part',part_id='other',status='INDETERMINATE',code='OPEN_PRINT_MESH')]
    r=adapter.make_result({},rows,source,tmp_path)
    run=CheckerRun(adapter.checker_spec(),r,tmp_path,())
    assert not _actionable_findings(run)
    assert not _checker_evidence([run],workspace=tmp_path)['typed_findings']


@pytest.mark.parametrize('profile_name',['stepcode-gpt-5.6-sol','cliproxy-gpt-5.6-sol'])
def test_saved_repair_freezes_selected_api_profile(tmp_path,monkeypatch,profile_name):
    from experiments.fixed_assembly_prompt import verify_topology as entry
    saved=tmp_path/'saved';generate=saved/'generate';generate.mkdir(parents=True)
    for name in ('assembly','render'): (generate/name).mkdir()
    (generate/'source.py').write_text('original source, never executed')
    write_json(generate/'plan.json',{})
    write_json(generate/'runtime_config.json',{'request':{'requirement':'original task'}})
    write_json(saved/'input.json',{'fixed_assembly':{'mm_per_unit':1,'fit_offset_mm':.2}})
    monkeypatch.setattr(entry,'run_assembly_topology',lambda *a,**kw:SimpleNamespace(
        result=CheckerResult(checker=adapter.NAME,status='INDETERMINATE',summary='fixture')))
    profile=entry.REPO/'adsl-agents/configs/llm'/f'{profile_name}.yaml'
    out=tmp_path/'new';entry.measure(saved,out,profile)
    record=json.loads((out/'input.json').read_text())
    assert record['api_profile']==profile_name and record['llm_config']==str(profile.resolve())
    assert record['llm_config_sha256']==sha256_file(profile)
    assert record['initial_generations']==0 and record['max_source_repairs']==1
    assert (out/'source.py').read_text()==(generate/'source.py').read_text()


def test_real_short_timeout_is_unverified_and_reaped(tmp_path):
    from adsl.agents.utils.execution import ExecutionResult
    source=tmp_path/'source.py';source.write_text('original')
    spec=adapter.checker_spec().model_copy(update={'timeout_seconds':.05,
        'command':['{python}','-c','import time; time.sleep(10)']})
    execution=ExecutionResult(tmp_path,tmp_path/'scene.glb',None,(),'','')
    run=adapter.run_assembly_topology(spec,execution=execution,source=source,root=tmp_path)
    assert run.result.status=='INDETERMINATE'
    assert (run.output_dir/'execution_error.json').exists() and source.read_text()=='original'


def test_rejected_candidate_cannot_publish_its_checker_result(tmp_path,monkeypatch):
    f=mock_flow(tmp_path,monkeypatch,[('PASS',False),('PASS',True)])
    f=(f[0],replace(f[1],checker_specs=(adapter.checker_spec(),),max_rounds=2),*f[2:])
    statuses=['PASS','FAIL']
    def run(spec,*,execution,source,root):
        out=root/'checkers'/adapter.NAME;out.mkdir(parents=True)
        r=result(statuses.pop(0),source);write_json(out/'result.json',r.model_dump())
        return CheckerRun(spec,r,out,())
    monkeypatch.setattr(adapter,'run_assembly_topology',run)
    final,book=run_flow(f)
    assert not final.approved and book['retained']=='original'
    checks=json.loads((tmp_path/'checker_results.json').read_text())
    assert checks['results'][0]['status']=='PASS'  # original, not the rejected FAIL
    assert checks['source_sha256']==sha256_file(tmp_path/'source.py')
    assert book['versions']['attempt_0001']['reviews']['assembly_topology']['status']=='FAIL'


def test_cached_initial_geometry_and_measurement_are_reused(tmp_path,monkeypatch):
    from adsl.agents import fixed_assembly as flow
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    w,r,rt,source,calls=f
    r=replace(r,checker_specs=(adapter.checker_spec(),))
    root=tmp_path/'rounds/round_01'
    ex=flow.execute_asset_source(source,root/'asset',fixed_assembly=r.fixed_assembly,export_urdf=False)
    report=ex.output_root/'assembly/assembly_manifest.json'
    checked=result('PASS',source)
    checked.assumptions['manifest_sha256']=sha256_file(report)
    checked.assumptions['topology_scope_version']=adapter.TOPOLOGY_SCOPE_VERSION
    out=root/'checkers'/adapter.NAME;out.mkdir(parents=True)
    write_json(out/'result.json',checked.model_dump())
    run=CheckerRun(adapter.checker_spec(),checked,out,())
    def forbidden(*a,**kw): raise AssertionError('initial CSG/checker must not run again')
    monkeypatch.setattr(flow,'execute_asset_source',forbidden)
    monkeypatch.setattr(adapter,'run_assembly_topology',forbidden)
    final=asyncio.run(flow.iterate_fixed_assembly(w,runtime=rt,request=r,workspace=tmp_path,
        source_path=source,plan=FixedAssemblyPlan.model_validate(plan_data()),
        initial_execution=ex,initial_topology_run=run))
    assert final.approved and not calls


def test_outer_timeout_kills_nested_worker_group(tmp_path):
    import os
    from adsl.agents.utils.execution import ExecutionResult
    source=tmp_path/'source.py';source.write_text('unchanged')
    import inspect
    # Exercise the actual cleanup function without spending the 3-second
    # timeout importing the unrelated Agent SDK on a cold filesystem.
    program=('import os,signal,subprocess,sys\nfrom pathlib import Path\n'+
        inspect.getsource(adapter.install_worker_cleanup)+
        '\ninstall_worker_cleanup(); p=subprocess.Popen([sys.executable,"-c","import time;time.sleep(30)"],start_new_session=True); '
        'print(p.pid,flush=True)\ntry: p.wait()\nfinally: p.wait(timeout=2)\n')
    program=program.replace('{','{{').replace('}','}}')  # checker command placeholders
    spec=adapter.checker_spec().model_copy(update={'timeout_seconds':3,'command':['{python}','-c',program]})
    execution=ExecutionResult(tmp_path,tmp_path/'scene.glb',None,(),'','')
    run=adapter.run_assembly_topology(spec,execution=execution,source=source,root=tmp_path)
    pid=int((run.output_dir/'stdout.log').read_text().strip().splitlines()[-1])
    with pytest.raises(ProcessLookupError): os.kill(pid,0)
    assert run.result.status=='INDETERMINATE'


def pair_manifest(tmp_path, *, collision=True, bad=False, single=False):
    from test_assembly_interference import collision_pair
    from adsl.agents.utils.execution import ExecutionResult
    c,parts,solids=collision_pair() if collision else fixture_pair()
    if single:
        parts={k:v for k,v in parts.items() if k=='bar'}
        solids={k:v for k,v in solids.items() if k=='bar'}
    source=tmp_path/'source.py';source.write_text('# fixture meshes; source is not executed\n')
    folder=tmp_path/'assembly';folder.mkdir()
    if bad:parts['bad']={'assembly_transform':np.eye(4).tolist()}
    declarations=[]
    for n,p in parts.items():
        declarations.append(dict(p,id=n,stl=n+'.stl',components=[n],print_transform_mm=np.eye(4).tolist()))
        if n in solids:solid_mesh(solids[n]).export(folder/(n+'.stl'),file_type='stl_ascii')
    manifest=dict(source_sha256=sha256_file(source),mm_per_unit=1.,parts=declarations,
        part_declarations=declarations,connections=[] if single else [c],
        files_sha256={p.name:sha256_file(p) for p in folder.glob('*.stl')})
    write_json(folder/'assembly_manifest.json',manifest)
    return source,ExecutionResult(folder,folder/'scene.glb',None,(),'',''),manifest


def test_real_subprocess_pair_finds_overlap_outside_passing_interface(tmp_path):
    source,execution,manifest=pair_manifest(tmp_path)
    run=adapter.run_assembly_topology(adapter.checker_spec(),execution=execution,source=source,root=tmp_path)
    parsed=CheckerResult.model_validate(json.loads((run.output_dir/'result.json').read_text()))
    assert parsed.status=='FAIL'
    rows=parsed.metrics['items'];pair=next(r for r in rows if r['kind']=='pair')
    assert all(r['status']=='PASS' for r in rows if r['kind']!='pair')
    assert pair['part_ids']==['bar','stem'] and pair['frame']=='assembly'
    assert pair['undeclared_interference_mm3']>pair['volume_tolerance_mm3']
    assert Path(pair['report_path']).is_file()
    finding=parsed.findings[0]
    assert finding.finding_id=='assembly_topology:pair:bar:stem:UNDECLARED_PART_INTERFERENCE'
    assert finding.region.part_names==['bar','stem'] and finding.region.frame=='assembly'
    assert finding.region.bounds==pair['bounds_mm']
    assert finding.region.details['connection_ids']==['joint']
    assert finding.metric.value==pair['undeclared_interference_mm3']
    assert finding.metric.absolute_tolerance==finding.metric.relative_tolerance==0
    assert parsed.assumptions['topology_scope_version']==adapter.TOPOLOGY_SCOPE_VERSION
    assert json.loads((run.output_dir/'report.json').read_text())['topology_scope_version']==adapter.TOPOLOGY_SCOPE_VERSION


@pytest.mark.parametrize('single',[False,True])
def test_real_subprocess_pair_missing_dependency_or_single_part(tmp_path,single):
    source,execution,_=pair_manifest(tmp_path,collision=False,bad=not single,single=single)
    run=adapter.run_assembly_topology(adapter.checker_spec(),execution=execution,source=source,root=tmp_path)
    pairs=[r for r in run.result.metrics['items'] if r['kind']=='pair']
    if single:
        assert pairs==[] and run.result.status=='PASS'
    else:
        assert len(pairs)==3 and run.result.status=='INDETERMINATE'
        assert next(r for r in pairs if r['pair_id']=='bar:stem')['status']=='PASS'
        for r in pairs:
            if 'bad' in r['part_ids']:
                assert r['code']=='DEPENDENCY_MESH_UNAVAILABLE'
                assert 'undeclared_interference_mm3' not in r


def test_pair_timeout_preserves_other_rows_and_unexecuted_pairs(tmp_path,monkeypatch):
    source,execution,manifest=pair_manifest(tmp_path,collision=False)
    # Third declared part gives three unordered pairs.
    third={**manifest['parts'][0],'id':'third'}
    manifest['parts'].append(third)
    manifest['part_declarations']=list(manifest['parts'])
    write_json(execution.output_root/'assembly_manifest.json',manifest)
    clock=[0.];monkeypatch.setattr(adapter.time,'monotonic',lambda:clock[0])
    def run(spec,*,execution,source_path,round_root):
        out=round_root/'checkers'/adapter.NAME;out.mkdir(parents=True)
        if '--part' in spec.command:
            name=spec.command[spec.command.index('--part')+1]
            adapter._save_solid_mesh(solid_mesh(mf.Manifold.cube((1,1,1))),out/'solid.npz')
            item=dict(kind='part',part_id=name,status='PASS',code='CONNECTED_PRINT_PART',
                length_tolerance_mm=.00001,surface_area_mm2=6.)
        elif '--connection' in spec.command:
            item=dict(kind='interface',connection_id='joint',status='PASS',code='INTERFACE_GEOMETRY_PAIRED')
        else:
            clock[0]=100.
            return CheckerRun(spec,CheckerResult(checker=adapter.NAME,status='ERROR',summary='pair timeout',
                violations=[{'code':'CHECKER_TIMEOUT'}]),out,())
        return CheckerRun(spec,CheckerResult(checker=adapter.NAME,status='PASS',summary='fixture',metrics={'item':item}),out,())
    monkeypatch.setattr(adapter,'run_checker',run)
    output=tmp_path/'measure';output.mkdir()
    adapter.measure(SimpleNamespace(source=source,manifest=execution.output_root/'assembly_manifest.json',
        output=output,source_index=None,budget_seconds=30))
    result=json.loads((output/'result.json').read_text());rows=result['metrics']['items']
    assert len([r for r in rows if r['kind']=='part' and r['status']=='PASS'])==3
    pairs=[r for r in rows if r['kind']=='pair']
    assert [r['code'] for r in pairs]==['CHECKER_TIMEOUT','CHECKER_BUDGET_NOT_EXECUTED','CHECKER_BUDGET_NOT_EXECUTED']
    assert all(r['status']=='INDETERMINATE' and 'undeclared_interference_mm3' not in r for r in pairs)
    assert result['status']=='INDETERMINATE'


@pytest.mark.parametrize('scope',[None,1])
def test_old_topology_scope_remeasures_without_reexport(tmp_path,monkeypatch,scope):
    from adsl.agents import fixed_assembly as flow
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    w,r,rt,source,calls=f;r=replace(r,checker_specs=(adapter.checker_spec(),))
    ex=flow.execute_asset_source(source,tmp_path/'rounds/round_01/asset',fixed_assembly=r.fixed_assembly,export_urdf=False)
    measured=result('PASS',source);measured.assumptions.update(manifest_sha256=sha256_file(ex.output_root/'assembly/assembly_manifest.json'))
    if scope is not None:measured.assumptions['topology_scope_version']=scope
    out=tmp_path/'old';out.mkdir();write_json(out/'result.json',measured.model_dump())
    cached=CheckerRun(adapter.checker_spec(),measured,out,());old=(out/'result.json').read_bytes()
    def forbidden(*a,**kw):pytest.fail('cached geometry re-exported')
    monkeypatch.setattr(flow,'execute_asset_source',forbidden)
    rechecks=[]
    def check(spec,*,execution,source,root):
        rechecks.append(sha256_file(source));new=result('PASS',source)
        new.assumptions['topology_scope_version']=adapter.TOPOLOGY_SCOPE_VERSION
        folder=root/'checkers'/adapter.NAME;folder.mkdir(parents=True)
        write_json(folder/'result.json',new.model_dump());return CheckerRun(spec,new,folder,())
    monkeypatch.setattr(adapter,'run_assembly_topology',check)
    final=asyncio.run(flow.iterate_fixed_assembly(w,runtime=rt,request=r,workspace=tmp_path,
        source_path=source,plan=FixedAssemblyPlan.model_validate(plan_data()),initial_execution=ex,initial_topology_run=cached))
    assert final.approved and len(rechecks)==1 and not calls
    assert (out/'result.json').read_bytes()==old


def test_pair_worker_query_failure_has_identity_and_no_fake_volume(tmp_path,monkeypatch):
    import sys
    import adsl.core.assembly_topology as core
    source,execution,_=pair_manifest(tmp_path)
    paths={}
    for name in ['bar','stem']:
        path=tmp_path/(name+'.npz');adapter._save_solid_mesh(solid_mesh(mf.Manifold.cube((1,1,1))),path);paths[name]=str(path)
    write_json(tmp_path/'solids.json',paths)
    def broken(*a,**kw):raise ValueError('native intersection rejected')
    monkeypatch.setattr(core,'pair_interference_measurement',broken)
    output=tmp_path/'worker'
    monkeypatch.setattr(sys,'argv',['topology','--manifest',str(execution.output_root/'assembly_manifest.json'),
        '--source',str(source),'--output',str(output),'--solids',str(tmp_path/'solids.json'),
        '--pair','stem','bar','--length-tolerance-mm','.001','--volume-tolerance-mm3','.01'])
    adapter.main();row=json.loads((output/'result.json').read_text())['metrics']['item']
    assert row['status']=='INDETERMINATE' and row['code']=='PAIR_QUERY_UNAVAILABLE'
    assert row['part_ids']==['bar','stem'] and row['pair_id']=='bar:stem'
    assert 'undeclared_interference_mm3' not in row
