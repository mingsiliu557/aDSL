"""Single print mode wiring: fake runtime, checker and file publication only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import numpy as np
import pytest
from adsl.agents import fixed_assembly as flow
from adsl.agents.models import FixedAssemblyConfig, FixedAssemblyPlan, ObjectRequest, RepairPolicy, RepairProposal, RepairTarget
from adsl.agents.service import ObjectWorkflow
from adsl.agents.prompts import object_prompt, support_prompt_options, fixed_assembly_for_agent
from adsl.agents.repair_controller import RepairController
from adsl.agents.utils.io import write_json
from test_fixed_assembly import CONFIG, plan_data, mock_flow


def single_plan():
    data = plan_data()
    data.update(print_parts=[dict(id='crossbar', components=['crossbar','stem'])], connections=[])
    return FixedAssemblyPlan.model_validate(data)


def request(tmp_path, *, metric='area', orientation=False, **overrides):
    overhang = dict(support_metric=metric, shape_editable=metric=='nsv', layer_height_mm=.2,
                    overhang_threshold_from_horizontal_deg=45)
    overhang.update(overrides)
    return ObjectRequest('T bracket', tmp_path, 'test', fixed_assembly={**CONFIG,'print_mode':'single',
        'physics':{'overhang':overhang}}, repair_policy=RepairPolicy(print_orientation_editable=orientation))


@pytest.mark.parametrize('value', [None, 'split'])
def test_mode_default(value):
    assert flow.print_mode({}) == flow.print_mode({'print_mode':value}) == 'split'
    assert support_prompt_options({'print_mode':value}) == {}


def test_config_conflicts(tmp_path):
    for mode in ('bad', False, 0, [], {}):
        with pytest.raises(ValueError): flow.print_mode({'print_mode':mode})
    with pytest.raises(ValueError):
        FixedAssemblyConfig.model_validate({**CONFIG,'print_mode':'single','require_multiple_parts':True})
    for r in [request(tmp_path, orientation=True), request(tmp_path,metric='nsv',shape_editable=False),
              request(tmp_path,partition_objective={}),
              replace(request(tmp_path),repair_policy=RepairPolicy(print_partition_editable=True))]:
        with pytest.raises(ValueError): ObjectWorkflow._validate_request(r)
    for r in [request(tmp_path),request(tmp_path,metric='nsv'),request(tmp_path,metric='nsv',orientation=True)]:
        if r.fixed_assembly['physics']['overhang']['support_metric']=='nsv':
            from adsl.agents.models import CheckerSpec
            r=replace(r,checker_specs=(CheckerSpec(name='assembly_overhang',command=['stub']),))
        ObjectWorkflow._validate_request(r)


@pytest.mark.parametrize('kind', ['two', 'connection', 'wrong_id'])
def test_single_plan_checker(kind):
    plan = single_plan()
    if kind=='two':plan=FixedAssemblyPlan.model_validate(plan_data())
    elif kind=='connection':plan=plan.model_copy(update={'connections':[object()]})
    else:plan=plan.model_copy(update={'root_part':'other'})
    with pytest.raises(ValueError,match='SINGLE_PLAN_INVALID'):flow.check_single_plan(plan)


@pytest.mark.parametrize('second_valid', [True,False])
def test_planner_retry_is_exactly_once(tmp_path,second_valid):
    calls=[]
    class Runtime:
        def agent(self,**kw): return kw
        async def run(self,**kw):
            calls.append(kw)
            return SimpleNamespace(final_output=single_plan() if len(calls)==2 and second_valid else FixedAssemblyPlan.model_validate(plan_data()))
    coro=ObjectWorkflow.__new__(ObjectWorkflow)._plan_generation(Runtime(),request(tmp_path))
    if second_valid:assert asyncio.run(coro)==single_plan()
    else:
        with pytest.raises(ValueError,match='SINGLE_PLAN_INVALID'):asyncio.run(coro)
    assert len(calls)==2 and 'SINGLE_PLAN_INVALID' in calls[1]['input']
    assert all(c['role']=='planner' and c['stage']=='plan' for c in calls)


@pytest.mark.parametrize('bad', ['parse','size'])
def test_other_planner_errors_are_not_retried(tmp_path,bad):
    result={} if bad=='parse' else single_plan().model_copy(update={'mm_per_unit':2})
    runtime=SimpleNamespace(agent=lambda **kw:kw,run=AsyncMock(return_value=SimpleNamespace(final_output=result)))
    with pytest.raises(ValueError):asyncio.run(ObjectWorkflow.__new__(ObjectWorkflow)._plan_generation(runtime,request(tmp_path)))
    assert runtime.run.await_count==1


@pytest.mark.parametrize('metric',['area','nsv'])
@pytest.mark.parametrize('role',['planner','coder','engineering_critic','code_critic','code_critic_review'])
def test_single_prompts(metric,role):
    config={'print_mode':'single','physics':{'overhang':{'support_metric':metric,'shape_editable':True}}}
    options=support_prompt_options(config)
    prompt=object_prompt(role,articulation=False,fixed_assembly=True,**options)
    assert 'print_mode=single' in prompt
    assert 'Never propose regroup_print_parts or change_print_orientation' in prompt
    assert 'Initial print grouping.' not in prompt
    assert 'On merge, remove internal connectors.' not in prompt
    if metric=='area':assert fixed_assembly_for_agent(config) is config


def bound_manifest():
    return dict(source_sha256='current',parts=[dict(id='crossbar',print_transform_mm=np.eye(4).tolist())],
                part_declarations=[dict(id='crossbar',components=['crossbar','stem'])],connections=[])


@pytest.mark.parametrize('bad',['count','id','connections','pose','missing','stale'])
def test_single_manifest_rejects(bad):
    report=bound_manifest();manifest=deepcopy(report)
    if bad=='count':manifest['parts']*=2
    elif bad=='id':manifest['parts'][0]['id']='wrong'
    elif bad=='connections':manifest['connections']=[{'id':'unexpected'}]
    elif bad=='pose':manifest['parts'][0]['print_transform_mm'][0][0]=-1
    elif bad=='missing':manifest.pop('part_declarations')
    else:manifest['source_sha256']='stale'
    assert flow.single_part_check(report,manifest,'crossbar')['conclusion']=='REJECT'


def test_single_declared_pose_ignores_recommendation():
    manifest=bound_manifest();manifest['parts'][0]['print_transform_mm'][2][3]=8
    manifest['recommended_print_transform_mm']=np.diag([-1,-1,1,1]).tolist()
    assert flow.single_part_check(manifest,manifest,'crossbar')['conclusion']=='ACCEPT'


@pytest.mark.parametrize('action',['regroup_print_parts','change_print_orientation'])
@pytest.mark.parametrize('orientation',[True,False])
def test_action_and_generic_controller_rejection(tmp_path,action,orientation):
    proposal=RepairProposal(proposal_id='p',finding_ids=['f'],hypothesis='test',target=RepairTarget(),action=action)
    with pytest.raises(ValueError,match='print_mode=single'):
        flow._check_single_action(proposal,{'print_mode':'single'},orientation)
    ctrl=RepairController(workspace=tmp_path,round_root=tmp_path,baseline_source=tmp_path/'source.py',
        source_index=None,findings=[],checker_specs_sha256='s',policy=RepairPolicy(print_orientation_editable=orientation),fixed_assembly={'print_mode':'single'})
    result,errors=ctrl.normalize_proposal(proposal)
    assert result is None and 'print_mode=single' in errors[0]
    if action=='change_print_orientation':assert ('automatic search' if orientation else 'fixed upright') in errors[0]


@pytest.mark.parametrize('metric',['area','nsv'])
def test_real_controller_multibody_draft_repairs(tmp_path,monkeypatch,metric):
    from adsl.agents import assembly_physics as physics
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerResult
    w,r,rt,src,calls=mock_flow(tmp_path,monkeypatch,[('PASS',True),('PASS',True)])
    r=replace(request(tmp_path,metric=metric),max_rounds=2,checker_specs=())
    if metric=='nsv':
        r=replace(r,checker_specs=(physics.checker_spec('assembly_overhang'),))
        def checks(specs,**kw):
            from test_support_volume import measurement
            return [CheckerRun(specs[0],CheckerResult(checker='assembly_overhang',status='PASS',summary='stub',
                metrics={'support':measurement(12)['support'],'support_measurement_config_sha256':'test'},
                assumptions={'source_sha256':flow.file_hash(kw['source'])}),kw['root'],())]
        monkeypatch.setattr(physics,'run_assembly_checks',checks)
    execute=flow.execute_asset_source
    count=[]
    def wrapped(src,out,**kw):
        ex=execute(src,out,**kw);m=bound_manifest();m.update(status='PASS',failures=[],source_sha256=flow.file_hash(src))
        count.append(1)
        if len(count)==1:m['parts']*=2;m['part_declarations']*=2
        write_json(out/'assembly/assembly_manifest.json',m);return ex
    monkeypatch.setattr(flow,'execute_asset_source',wrapped)
    result=asyncio.run(flow.iterate_fixed_assembly(w,runtime=rt,request=r,workspace=tmp_path,source_path=src,plan=single_plan()))
    book=json.loads((tmp_path/'assembly_versions.json').read_text())
    assert len(calls)==1
    assert 'exactly one print part crossbar' in calls[0]['payload']['feedback']['primary_objective']
    assert book['versions']['original']['reviews']['single_part_check']['conclusion']=='REJECT'
    assert book['versions']['attempt_0001']['reviews']['single_part_check']['conclusion']=='ACCEPT'
    assert book['retained']=='attempt_0001' and result.approved
    assert all(v['reviews'].get('mode_a_pose_check') is None for v in book['versions'].values())


def test_single_auto_search_without_partition(tmp_path,monkeypatch):
    from test_support_overhang import setup
    from test_support_volume import row
    from adsl.agents import assembly_overhang as overhang, support_volume as support
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    write_json(args.manifest,manifest)
    transform=np.diag([-1.,-1.,1.,1.]); seen=[]
    def best(m,**kw):seen.append(kw);return dict(support=row(),recommended_print_transform_mm=transform.tolist(),selected_rotation_id=1)
    monkeypatch.setattr(support,'best_support_pose',best)
    monkeypatch.setattr(overhang,'_verify_recommended_stl',lambda *a:{'status':'PASS'})
    monkeypatch.setattr(overhang,'partition_measurement',lambda *a:pytest.fail('no partition'))
    result=overhang.analyze(args,{'overhang':{**config,'print_mode':'single','support_metric':'nsv','shape_editable':True,'orientation_editable':True}})
    assert len(seen)==1 and result.status=='PASS'
    assert not any(f.rule_id=='PRINT_ORIENTATION_OPPORTUNITY' for f in result.findings)
    layout=json.loads((tmp_path/'print_layout.json').read_text())
    assert layout['parts'][0]['recommended_print_transform_mm']==transform.tolist()
    assert (tmp_path/layout['parts'][0]['stl']).is_file()


@pytest.mark.parametrize('metric',['area','nsv'])
def test_multibody_checker_does_not_crash(tmp_path,monkeypatch,metric):
    from test_support_overhang import setup
    from adsl.agents import assembly_overhang as overhang, support_volume as support
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    manifest['parts']*=2;manifest['part_declarations']*=2;write_json(args.manifest,manifest)
    monkeypatch.setattr(support,'best_support_pose',lambda *a,**kw:pytest.fail('cannot choose subset'))
    result=overhang.analyze(args,{'overhang':{**config,'print_mode':'single','support_metric':metric,'orientation_editable':metric=='nsv'}})
    if metric=='nsv':assert result.status=='INDETERMINATE' and 'single_mode_part_count=2' in result.summary
    assert not any(f.rule_id=='PRINT_ORIENTATION_OPPORTUNITY' for f in result.findings)


def test_frozen_hash_split_resume(tmp_path,monkeypatch):
    from test_fixed_assembly import run_flow
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    _,book=run_flow(f);old=book['config_sha256']
    for value in (None,'split'):
        w,r,rt,src,calls=f;r=replace(r,fixed_assembly={**r.fixed_assembly,'print_mode':value})
        _,book=run_flow((w,r,rt,src,calls));assert book['config_sha256']==old
    frozen={**CONFIG,'assembly_plan':FixedAssemblyPlan.model_validate(plan_data()).model_dump()}
    assert old==hashlib.sha256(json.dumps(frozen,sort_keys=True).encode()).hexdigest()


def publication_fixture(tmp_path,monkeypatch):
    manifest=bound_manifest();source=tmp_path/'source.py';source.write_text('source')
    sha=flow.file_hash(source);manifest['source_sha256']=sha
    asset=tmp_path/'retained_asset';folder=asset/'assembly';folder.mkdir(parents=True)
    write_json(folder/'assembly_manifest.json',manifest)
    out=tmp_path/'retained_checks';out.mkdir();stl=out/'recommended.stl';stl.write_bytes(b'rotated-stl')
    layout=dict(mode='single',status='PASS',source_sha256=sha,manifest_sha256=flow.file_hash(folder/'assembly_manifest.json'),parts=[dict(part_id='crossbar',stl=stl.name,stl_sha256=flow.file_hash(stl),recommended_print_transform_mm=np.diag([-1,-1,1,1]).tolist())])
    path=out/'print_layout.json';write_json(path,layout);review=dict(status='PASS',artifacts={'print_layout':str(path)})
    record=dict(source=str(source),reviews={'assembly_overhang':review})
    write_json(tmp_path/'assembly_versions.json',dict(retained='original',versions={'original':record}))
    monkeypatch.setattr(flow,'assert_version',lambda v:None)
    monkeypatch.setattr(flow,'version_assets',lambda v:(None,SimpleNamespace(output_root=asset),None))
    return review,sha,path,layout


@pytest.mark.parametrize('bad',[None,'source_sha256','manifest_sha256','mode','part_id','stl_sha256','other_candidate'])
def test_publication_identity(tmp_path,monkeypatch,bad):
    review,sha,path,layout=publication_fixture(tmp_path,monkeypatch)
    if bad in ('source_sha256','manifest_sha256','mode'):layout[bad]='wrong'
    elif bad in ('part_id','stl_sha256'):layout['parts'][0][bad]='wrong'
    elif bad=='other_candidate':
        other=tmp_path/'other.json';write_json(other,layout);review=deepcopy(review);review['artifacts']['print_layout']=str(other)
    write_json(path,layout)
    if bad:
        with pytest.raises(ValueError):flow.publish_single_print(review,tmp_path,sha,'original','crossbar')
    else:
        published,parts=flow.publish_single_print(review,tmp_path,sha,'original','crossbar')
        assert parts[0]['recommended_print_transform_mm']==np.diag([-1,-1,1,1]).tolist()
        assert __import__('pathlib').Path(parts[0]['stl']).read_bytes()==b'rotated-stl'


@pytest.mark.parametrize('kind',['missing','not_pass','layout_not_pass'])
def test_unavailable_publication_is_not_error(tmp_path,monkeypatch,kind):
    review,sha,path,layout=publication_fixture(tmp_path,monkeypatch)
    if kind=='missing':path.unlink()
    elif kind=='not_pass':review['status']='INDETERMINATE'
    else:layout['status']='INDETERMINATE';write_json(path,layout)
    assert flow.publish_single_print(review,tmp_path,sha,'original','crossbar')==(None,[])


def test_invalid_initial_single_no_change_never_approved(tmp_path,monkeypatch):
    w,r,rt,src,calls=mock_flow(tmp_path,monkeypatch,[('PASS',True)],patch_status='NO_CHANGE')
    r=replace(request(tmp_path),max_rounds=2,checker_specs=())
    execute=flow.execute_asset_source
    def wrapped(src,out,**kw):
        ex=execute(src,out,**kw);m=bound_manifest();m.update(status='PASS',failures=[],source_sha256=flow.file_hash(src))
        m['connections']=[{'id':'forbidden'}];write_json(out/'assembly/assembly_manifest.json',m);return ex
    monkeypatch.setattr(flow,'execute_asset_source',wrapped)
    result=asyncio.run(flow.iterate_fixed_assembly(w,runtime=rt,request=r,workspace=tmp_path,source_path=src,plan=single_plan()))
    assert not result.approved and len(calls)==1
    saved=json.loads((tmp_path/'assembly_result.json').read_text())
    assert saved['print_mode']=='single' and not saved['approved']
    assert saved['reviews']['single_part_check']['rule']=='single_connections'


def test_resume_single_invalid_plan_does_not_call_model(tmp_path,monkeypatch):
    r=request(tmp_path);write_json(tmp_path/'plan.json',plan_data());write_json(tmp_path/'checkpoint.json',{'mode':'generate','stage':'planned'})
    (tmp_path/'source.py').write_text('')
    runtime=SimpleNamespace(usage=SimpleNamespace(update_manifest=lambda **kw:None),run=AsyncMock())
    w=ObjectWorkflow.__new__(ObjectWorkflow);monkeypatch.setattr(w,'_runtime',lambda *a,**kw:runtime)
    with pytest.raises(ValueError,match='SINGLE_PLAN_INVALID'):asyncio.run(w.resume(r))
    runtime.run.assert_not_called()


@pytest.mark.parametrize('action',['regroup_print_parts','change_print_orientation'])
def test_engineering_actual_entry_rejects_single_actions(tmp_path,monkeypatch,action):
    from adsl.agents import assembly_topology as topology
    from adsl.agents.models import EngineeringCriticDecision, CheckerResult
    from adsl.agents.checkers import CheckerRun
    source=tmp_path/'source.py';source.write_text('source')
    p=RepairProposal(proposal_id='bad',finding_ids=['f'],hypothesis='test',target=RepairTarget(),action=action)
    runtime=SimpleNamespace(agent=lambda **kw:kw,run=AsyncMock(return_value=SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,observations=[],repair_proposals=[p]))))
    from adsl.agents import service
    monkeypatch.setattr(service,'_has_assigned_source_read',lambda ctx:True)
    run=CheckerRun(topology.checker_spec(),CheckerResult(checker='assembly_topology',status='PASS',summary='stub'),tmp_path,())
    feedback={'source_version':'original'}
    result=asyncio.run(topology.engineer(ObjectWorkflow.__new__(ObjectWorkflow),runtime,request(tmp_path),single_plan(),source,None,tmp_path,run,{'current_assembly':{}},feedback,1))
    assert result is None and 'print_mode=single' in feedback['engineering']['reason']
    assert 'print_mode=single' in runtime.run.call_args.kwargs['agent']['instructions']


def test_image_only_single_retries_before_freezing_size(tmp_path):
    from experiments.densicrafter_image_only.workflow import ImageOnlyWorkflow
    from test_densicrafter_image_only import request as image_request
    r=image_request(tmp_path);r.fixed_assembly['print_mode']='single'
    runtime=SimpleNamespace(agent=lambda **kw:kw,run=AsyncMock(side_effect=[SimpleNamespace(final_output=FixedAssemblyPlan.model_validate(plan_data())),SimpleNamespace(final_output=single_plan())]))
    plan=asyncio.run(ImageOnlyWorkflow()._plan_generation(runtime,r))
    assert runtime.run.await_count==2
    assert len(plan.print_parts)==1 and max(plan.final_size_mm)==150
    assert r.fixed_assembly['final_size_mm']==plan.final_size_mm


@pytest.mark.parametrize('gain', [True, False])
def test_single_optional_shape_uses_existing_support_acceptance(tmp_path,monkeypatch,gain):
    from test_support_workflow import support_fixture
    f,checks,inputs=support_fixture(tmp_path,monkeypatch,[12,8 if gain else 12])
    w,r,rt,src,calls=f;r=replace(r,fixed_assembly={**r.fixed_assembly,'print_mode':'single'})
    plan=single_plan().model_copy(update={'root_part':'body','print_parts':[single_plan().print_parts[0].model_copy(update={'id':'body'})]})
    result=asyncio.run(flow.iterate_fixed_assembly(w,runtime=rt,request=r,workspace=tmp_path,source_path=src,plan=plan))
    book=json.loads((tmp_path/'assembly_versions.json').read_text())
    assert result.approved and len(calls)==1
    assert book['retained']==('attempt_0001' if gain else 'original')
    review=book['versions']['attempt_0001']['reviews']
    assert review['single_part_check']['conclusion']=='ACCEPT'
    assert review['support_postcheck']['conclusion']==('ACCEPT' if gain else 'REJECT')
    assert inputs[0]['support_baseline_version']=='original'


@pytest.mark.parametrize('mode', ['single', 'split'])
def test_runtime_records_mode_before_planner(tmp_path, monkeypatch, mode):
    from unittest.mock import Mock
    from adsl.agents import service
    runtime = Mock()
    monkeypatch.setattr(service, 'AgentRuntime', lambda **kwargs: runtime)
    workflow = ObjectWorkflow.__new__(ObjectWorkflow)
    workflow.model_profile = None
    req = request(tmp_path)
    req = replace(req, fixed_assembly={**req.fixed_assembly, 'print_mode': mode})
    assert workflow._runtime(req, tmp_path, mode='generate') is runtime
    assert runtime.usage.update_manifest.call_args.kwargs['print_mode'] == mode
