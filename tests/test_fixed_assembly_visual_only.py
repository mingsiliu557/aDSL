"""Visual connector mode: mocks and saved-mesh serialization, no real API/CSG."""
import asyncio
from dataclasses import replace
import json
import numpy as np
import pytest
from adsl.agents import fixed_assembly as flow
from adsl.agents.models import FixedAssemblyConfig
from adsl.agents.service import ObjectWorkflow
from adsl.core.export import export_assembly as exporter
from test_fixed_assembly import mock_flow, run_flow, CONFIG
from test_fixed_assembly_diagnostics import critic_runtime


@pytest.mark.parametrize('complete,export_ok,accepted', [(True,True,True),(False,True,False),(True,False,False)])
def test_visual_mode_critics_and_approval_scope(tmp_path,monkeypatch,complete,export_ok,accepted):
    state=mock_flow(tmp_path,monkeypatch,[('NOT_EVALUATED',True)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=1,
        fixed_assembly={**CONFIG,'validation_mode':'visual_only'}),*state[2:])
    w,r,rt,_,_=state
    calls=critic_runtime(rt)
    run_critic=rt.run
    async def clean_review(**kw):
        response=await run_critic(**kw)
        response.final_output.required_changes=[]
        return response
    rt.run=clean_review
    monkeypatch.setattr(w,'_review_generation_image',ObjectWorkflow._review_generation_image.__get__(w))
    monkeypatch.setattr(w,'_review_generation_code',ObjectWorkflow._review_generation_code.__get__(w))
    execute=flow.execute_asset_source
    def run(*a,**kw):
        assert kw['render'] and kw['fixed_assembly']['validation_mode']=='visual_only'
        result=execute(*a,**kw)
        path=result.output_root/'assembly/assembly_manifest.json'
        report=json.loads(path.read_text())
        report.update(export_status='PASS' if export_ok else 'FAIL', diagnostic={'complete':complete})
        path.write_text(json.dumps(report))
        return result
    monkeypatch.setattr(flow,'execute_asset_source',run)
    result,book=run_flow(state)
    assert result.approved is accepted
    assert len(calls)==1  # Image approval must not trigger Code because geometry is unmeasured.
    assert 'assembly_diagnostic' not in json.dumps(calls[0]['input'])
    report=json.loads((tmp_path/'assembly_result.json').read_text())
    assert report['geometry_validation']=='NOT_EVALUATED'
    assert report['interface_and_appearance_approved'] is None
    assert report['verification_scope']=='visual_code_only'
    assert book['qualified']==('original' if accepted else None)


def test_visual_mesh_does_not_require_a_closed_volume(tmp_path,monkeypatch):
    from test_fixed_assembly_empty_mesh import _scene
    _scene(monkeypatch,[(0,0,0),(1,0,0),(0,1,0)],[(0,1,2)])
    monkeypatch.setattr(exporter,'mesh_solid',lambda *a,**k:pytest.fail('no solid validation in visual mode'))
    mesh,solid,info=exporter.evaluated(None,tmp_path/'part.glb',2.,
        keep_materials=True,validate_geometry=False)
    assert solid is None and info['geometry_validation']=='NOT_EVALUATED'
    assert info['display_complete'] and len(mesh.faces)==1
    assert np.allclose(mesh.bounds,[[0,0,0],[2,2,0]])


def test_export_comparison_does_not_validate_solids_and_rejects_changed_files(tmp_path,monkeypatch):
    from test_fixed_assembly_exports import _files
    mesh,manifest,_=_files(tmp_path)
    monkeypatch.setattr(exporter,'mesh_solid',lambda *a,**k:pytest.fail('no solid check'))
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':mesh})
    assert not manifest['failures']
    transform=np.asarray(manifest['parts'][0]['assembly_transform']).copy();transform[0,3]+=.5
    exporter._write_mesh_glb({'part':mesh},{'part':transform},tmp_path/'scene.glb',manifest['mm_per_unit'])
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':mesh})
    assert any(f['code']=='EXPORTED_FILE_GEOMETRY_MISMATCH' for f in manifest['failures'])


def test_visual_export_only_evaluates_final_parts_not_bodies_or_interface_checks(tmp_path,monkeypatch):
    from test_fixed_assembly_exports import _files
    from test_fixed_assembly import build,connect
    fixture=tmp_path/'fixture';fixture.mkdir()
    mesh,_,_=_files(fixture)
    assembly=build();connect(assembly)
    evaluated=[]
    def display(shape,path,unit,**kw):
        assert kw=={'keep_materials':True,'validate_geometry':False}
        evaluated.append(path.name)
        return mesh.copy(),None,{'display_complete':True,'omitted_mesh_nodes':[]}
    monkeypatch.setattr(exporter,'evaluated',display)
    monkeypatch.setattr(exporter,'mesh_solid',lambda *a,**k:pytest.fail('no geometric check'))
    result=exporter.export_assembly(assembly,tmp_path/'output',source_sha256='test',
        expected={**CONFIG,'validation_mode':'visual_only'})
    assert evaluated==['bar.glb','stem.glb']
    assert result['status']=='NOT_EVALUATED' and result['export_status']=='PASS'
    assert result['diagnostic']['display_available']
    assert result['diagnostic']['semantic_completeness']=='NOT_EVALUATED'
    assert result['backend']['within_part_union']=='NOT_EXECUTED'
    assert not any('connected_components' in p for p in result['parts'])


def test_mode_is_explicit_and_old_default_unchanged():
    assert FixedAssemblyConfig.model_validate(CONFIG).validation_mode=='geometry'
    assert FixedAssemblyConfig.model_validate({**CONFIG,'validation_mode':'visual_only'}).validation_mode=='visual_only'
    with pytest.raises(ValueError): FixedAssemblyConfig.model_validate({**CONFIG,'validation_mode':'ignore_everything'})


def test_five_round_budget_allows_four_repairs_and_preserves_original(tmp_path,monkeypatch):
    state=mock_flow(tmp_path,monkeypatch,[('NOT_EVALUATED',False)]*5)
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=5,
        fixed_assembly={**CONFIG,'validation_mode':'visual_only'}),*state[2:])
    result,book=run_flow(state)
    assert len(state[-1])==4 and book['max_rounds']==5
    assert book['working']=='attempt_0004' and book['retained']=='original'
    assert not result.approved and book['stop_reason']=='round_budget_exhausted'
    assert [call['payload']['remaining_repairs'] for call in state[-1]]==[3,2,1,0]
