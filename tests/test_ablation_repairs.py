"""Repair v2 contracts; synthetic evidence and scripted runtimes only."""
import asyncio
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import pytest
import adsl.core as core
import adsl.core.constructive as constructive
from adsl.agents.utils.ablation_source import check_profile_source,DisabledProfileAPI,DISABLED_PROFILE_APIS
from adsl.agents.utils.asset_executor import _execute_generated_source
from adsl.agents import fixed_assembly as flow
from adsl.agents.service import ObjectWorkflow
from adsl.agents.models import CheckerSpec,FixedAssemblyPlan
from test_ablation import request

@pytest.mark.parametrize('text',[
 'from adsl.core.constructive import loft',
 'from adsl.core.constructive import *\nf=hull',
 'import adsl.core.constructive as c\nc.loft([])',
 'import adsl\nf=adsl.core.constructive.loft',
 'from adsl import core\nmake=core.loft',
 "from adsl import core\ngetattr(core, 'loft')([])",
])
def test_ast_submodule_and_load(text):
    with pytest.raises(DisabledProfileAPI):check_profile_source(text)


def test_runtime_dynamic_guard_restores_objects_and_line(tmp_path,monkeypatch):
    import adsl.agents.utils.asset_executor as executor
    # Fake exported function avoids any geometry generation for the recovery call.
    scene=core.Asset()
    monkeypatch.setattr(core,'loft',lambda *a:scene)
    saved={(m,n):getattr(m,n) for m in (core,constructive) for n in DISABLED_PROFILE_APIS}
    source=tmp_path/'source.py'
    source.write_text("from adsl import core\nname=''.join(['lo','ft'])\nscene=getattr(core,name)([])\n")
    check_profile_source(source.read_text())
    with pytest.raises(DisabledProfileAPI):_execute_generated_source(source,tmp_path/'asset',ablation='profile_ops')
    diagnostic=json.loads((tmp_path/'asset/source_execution_error.json').read_text())
    assert diagnostic['code']=='GENERATED_SOURCE_EXECUTION_FAILED'
    assert diagnostic['name']=='loft' and diagnostic['reason']=='ABLATION_DISABLED_API'
    assert diagnostic['source_locations'][0]['line']==3
    assert all(getattr(m,n) is original for (m,n),original in saved.items())
    assert _execute_generated_source(source,tmp_path/'other')['scene'] is scene
    source.write_text('from adsl.core import Asset\nscene=Asset()')
    _execute_generated_source(source,tmp_path/'ok',ablation='profile_ops')
    assert all(getattr(m,n) is original for (m,n),original in saved.items())


def test_tracing_nested_evidence_preserves_geometry_and_diagnostic(tmp_path):
    from adsl.agents.ablation_views import sanitized_evidence,tracing_view,record_hidden_tracing
    from adsl.agents.tools.context import AgentToolContext
    from adsl.agents.tools.files import read_file
    from test_assembly_feedback_recovery import tool
    src=tmp_path/'source.py';src.write_text('stub')
    inner={'findings':[{'finding_id':'physical','source_candidates':[{'source_locations':[{'line':7}]}],
                       'region':{'coordinates':[[1.,2.,3.]]},'scope':'printed millimetres'}]}
    (tmp_path/'inner.json').write_text(json.dumps(inner))
    (tmp_path/'outer.json').write_text(json.dumps({'edge_ref':{'path':'inner.json','json_pointer':'/findings/0/region'}}))
    (tmp_path/'source_index.json').write_text('{}')
    raw={'report_ref':{'path':'outer.json'},'geometry_report_ref':{'path':'inner.json'},
         'evidence_files':[{'path':'inner.json','json_pointer':'/findings/0/source_candidates'}],
         'source_index_ref':{'path':'source_index.json'},
         'execution_failure':{'code':'GENERATED_SOURCE_EXECUTION_FAILED','source_locations':[{'path':str(src),'line':3}],
                              'node_path':'scene.body','exception_type':'ValueError'}}
    original=deepcopy(raw);ctx=AgentToolContext(tmp_path,src,ablation='tracing')
    before=set(tmp_path.rglob('*'))
    preview=sanitized_evidence(raw,workspace=tmp_path,source_path=src)
    assert set(tmp_path.rglob('*'))==before
    out=sanitized_evidence(raw,workspace=tmp_path,source_path=src,context=ctx,write=True)
    assert out==preview and raw==original
    assert out['execution_failure']['node_path']=='scene.body'
    assert out['execution_failure']['source_locations']==raw['execution_failure']['source_locations']
    assert out['evidence_files'][0]['reason']=='hidden_by_ablation'
    assert tracing_view(tracing_view(inner))==tracing_view(inner)
    for path in ctx.sanitized_evidence_paths:
        doc=json.loads(path.read_text())
        assert 'source_candidates' not in json.dumps(doc)
        assert 'ABLATION_READ_DENIED' not in asyncio.run(tool(read_file,ctx,'safe',path=str(path)))
    region=json.loads((tmp_path/out['geometry_report_ref']['path']).read_text())['findings'][0]
    assert region['region']==inner['findings'][0]['region'] and region['scope']=='printed millimetres'
    assert sanitized_evidence(out,workspace=tmp_path,source_path=src,context=ctx,write=True)==out
    files=set(tmp_path.rglob('*'))
    assert sanitized_evidence(raw,workspace=tmp_path,source_path=src,context=ctx,write=True)==out
    assert set(tmp_path.rglob('*'))==files
    record_hidden_tracing(raw,workspace=tmp_path,source_path=src,source_version='original',role='coder')
    assert not (tmp_path/'ablation_private').exists() # no inline physical trace; traceback is not logged
    for name in ('inner.json','source_index.json'):
        assert 'ABLATION_READ_DENIED' in asyncio.run(tool(read_file,ctx,'raw',path=name))


@pytest.mark.parametrize('variant',[None,'tracing','actions','profile_ops','fab_plan'])
@pytest.mark.parametrize('key,value', [('layer_height_mm',0),('overhang_threshold_from_horizontal_deg',90),('missing_checker',None)])
def test_nsv_errors_before_first_model(tmp_path,monkeypatch,variant,key,value):
    cfg={'physics':{'overhang':{'support_metric':'nsv','shape_editable':True,'layer_height_mm':.2,'overhang_threshold_from_horizontal_deg':45}}}
    if key!='missing_checker':cfg['physics']['overhang'][key]=value
    req=request(tmp_path,variant,**cfg)
    if key=='missing_checker':req=replace(req,checker_specs=())
    workflow=ObjectWorkflow.__new__(ObjectWorkflow)
    monkeypatch.setattr(workflow,'_runtime',lambda *a,**k:pytest.fail('must reject before runtime'))
    with pytest.raises(ValueError):asyncio.run(workflow.generate(req))

@pytest.mark.parametrize('variant',['tracing','actions','profile_ops','fab_plan'])
def test_required_checkers(tmp_path,variant):
    req=request(tmp_path,variant)
    for name in ('assembly_topology','assembly_overhang'):
        with pytest.raises(ValueError):ObjectWorkflow._validate_request(replace(req,checker_specs=(CheckerSpec(name=name,command=['stub']),)))
    if variant!='fab_plan':ObjectWorkflow._validate_request(req) # standing is optional

@pytest.mark.parametrize('values,bad', [([12,3],False),([12,12],False),([12,7,3],True),([12,7,12],True)])
def test_actions_differential_shape_with_partition(tmp_path,monkeypatch,values,bad):
    from test_support_workflow import d_fixture
    from test_fixed_assembly import run_flow
    outputs=[]
    for variant in (None,'actions'):
        with monkeypatch.context() as patch:
            state,checks,captured,refs,published=d_fixture(tmp_path/(variant or 'full'),patch,values,bad_middle=False)
            req=replace(state[1],fixed_assembly={**state[1].fixed_assembly,'ablation':variant})
            state=(state[0],req,*state[2:])
            if bad:
                from adsl.agents import assembly_physics as physics
                from adsl.agents.models import CheckerResult
                from adsl.agents.checkers import CheckerRun
                original=physics.run_assembly_checks
                def with_failure(specs,**kw):
                    runs=original(specs,**kw)
                    failed=len(state[-1])==1
                    findings=[physics.finding('assembly_topology','INTERNAL_PART_DISCONNECTED','stub defect',
                        category='geometry_failure',repairability='design_variable',required=True)] if failed else []
                    extra=CheckerRun(CheckerSpec(name='assembly_topology',command=['stub']),
                        CheckerResult(checker='assembly_topology',status='FAIL' if failed else 'PASS',summary='stub',findings=findings,
                            assumptions={'source_sha256':flow.file_hash(kw['source'])}),kw['root'],())
                    return runs+[extra]
                patch.setattr(physics,'run_assembly_checks',with_failure)
            result,book=run_flow(state)
            assert book['stop_reason']!='partition_intent_unavailable'
            outputs.append((result.approved,book['retained'],book['qualified'],book['stop_reason'],
                            state[3].read_text(),book['feedback'].get('support_baseline_version'),
                            book['feedback'].get('comparison_baseline_version'),
                            Path(book['partition_reference']).name if book.get('partition_reference') else None,
                            [(v['reviews'].get('accepted'),v['reviews'].get('reason')) for v in book['versions'].values()]))
    assert outputs[0]==outputs[1]


def test_actions_forbids_group_and_authored_rotation():
    import numpy as np
    before={'part_declarations':[{'id':'body','components':['a','b']}],
            'parts':[{'id':'body','print_transform_mm':np.eye(4).tolist()}]}
    assert flow._actions_structure_preserved(before,deepcopy(before))
    candidate=deepcopy(before);candidate['part_declarations'][0]['components']=['a']
    assert not flow._actions_structure_preserved(before,candidate)
    candidate=deepcopy(before);candidate['parts'][0]['print_transform_mm'][0][0]=-1
    assert not flow._actions_structure_preserved(before,candidate)


def test_tracing_keeps_internal_candidate_verification(tmp_path,monkeypatch):
    from adsl.agents import agent_feedback as a
    from test_agent_feedback import assembly_candidates_input
    source,raw,ref,payload=assembly_candidates_input(tmp_path,count=6)
    original=deepcopy(payload); seen=[]
    method=a._Projection.candidate_ref
    def capture(self,*args,**kw):
        result=method(self,*args,**kw);seen.append(deepcopy(result));return result
    monkeypatch.setattr(a._Projection,'candidate_ref',capture)
    opts=dict(workspace=tmp_path,source_path=source,source_version='original',role='engineering',report_ref=ref)
    a.payload_for_agent(payload,**opts);full=deepcopy(seen);seen.clear()
    out=a.payload_for_agent(payload,**opts,ablation='tracing')
    assert seen==full and payload==original
    assert all('ablation_evidence' not in json.dumps(ref) for ref in seen)
    assert a.payload_for_agent(out,**opts,ablation='tracing')==out


@pytest.mark.parametrize('change',['group','orientation'])
def test_actions_structured_edit_is_rejected_in_loop(tmp_path,monkeypatch,change):
    from test_support_workflow import d_fixture
    from test_fixed_assembly import run_flow
    state,*_=d_fixture(tmp_path,monkeypatch,[12,3])
    req=replace(state[1],fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'})
    state=(state[0],req,*state[2:])
    executor=flow.execute_asset_source
    def execute(src,out,**kw):
        result=executor(src,out,**kw)
        if state[-1]:
            path=out/'assembly/assembly_manifest.json';report=json.loads(path.read_text())
            if change=='group':report['part_declarations'][0]['components']=['changed']
            else:report['parts'][0]['print_transform_mm'][0][0]=-1
            path.write_text(json.dumps(report))
        return result
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    _,book=run_flow(state)
    assert book['retained']=='original'
    assert book['versions']['attempt_0001']['reviews']['reason']=='ABLATION_STRUCTURED_ACTION_FORBIDDEN'


def test_actions_budget_exhaustion_uses_existing_reason(tmp_path,monkeypatch):
    from test_support_workflow import d_fixture
    from test_fixed_assembly import run_flow
    state,*_=d_fixture(tmp_path,monkeypatch,[12,3])
    req=replace(state[1],fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'})
    monkeypatch.setattr(flow.RepairController,'budget_error',lambda self:'fixture budget exhausted')
    _,book=run_flow((state[0],req,*state[2:]))
    assert book['stop_reason']=='repair_budget_exhausted'
    assert book['retained']=='original'


@pytest.mark.parametrize('rule',['PRINT_PARTITION_OPPORTUNITY','PRINT_ORIENTATION_OPPORTUNITY'])
def test_actions_only_structured_opportunity_stops(tmp_path,monkeypatch,rule):
    from test_support_workflow import d_fixture
    from test_fixed_assembly import run_flow
    from adsl.agents import assembly_physics as physics
    state,*_=d_fixture(tmp_path,monkeypatch,[12,3])
    req=replace(state[1],fixed_assembly={**state[1].fixed_assembly,'ablation':'actions'},
                repair_policy=state[1].repair_policy.model_copy(update={'print_partition_editable':True,'print_orientation_editable':True}))
    original=physics.run_assembly_checks
    def checks(*a,**kw):
        runs=original(*a,**kw)
        for run in runs:
            for finding in run.result.findings:finding.rule_id=rule
        return runs
    monkeypatch.setattr(physics,'run_assembly_checks',checks)
    _,book=run_flow((state[0],req,*state[2:]))
    assert book['stop_reason']=='optional_optimization_disabled_by_ablation'
    assert not state[-1]
