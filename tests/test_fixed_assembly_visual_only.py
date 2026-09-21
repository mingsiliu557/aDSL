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
    assert any(f['code']=='EXPORTED_FILE_PLACEMENT_OR_SCALE_MISMATCH' for f in manifest['failures'])


def test_triangle_encoding_difference_is_regression_only(tmp_path):
    from test_fixed_assembly_exports import _files
    mesh,manifest,_=_files(tmp_path)
    # Identical box surface/bounds, different tessellation: not a runtime gate.
    reference=mesh.subdivide()
    before=reference.faces.copy()
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':reference})
    assert not manifest['failures']
    assert manifest['triangle_comparison']=='NOT_EXECUTED'
    assert all(row['scope']=='file_structure_units_and_placement' for row in manifest['export_consistency'])
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':reference},compare_triangles=True)
    assert manifest['triangle_comparison']=='REGRESSION_ONLY'
    assert any(f['code']=='EXPORTED_FILE_GEOMETRY_MISMATCH' for f in manifest['failures'])
    assert np.array_equal(before,reference.faces)


@pytest.mark.parametrize('defect', ['missing_file','missing_part','wrong_units'])
def test_basic_export_errors_remain_failures(tmp_path,defect):
    from test_fixed_assembly_exports import _files
    mesh,manifest,_=_files(tmp_path)
    if defect=='missing_file':
        (tmp_path/'part.stl').unlink()
    elif defect=='missing_part':
        # A readable scene without the declared part is still invalid.
        exporter._write_mesh_glb({'other':mesh},{'other':np.eye(4)},
            tmp_path/'scene.glb',manifest['mm_per_unit'])
    else:
        changed=mesh.copy();changed.apply_scale(2.)
        changed.apply_transform(np.asarray(manifest['parts'][0]['print_transform_mm']))
        changed.export(tmp_path/'part.stl')
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':mesh})
    expected='EXPORTED_FILE_PLACEMENT_OR_SCALE_MISMATCH' if defect=='wrong_units' else 'EXPORTED_FILE_INVALID'
    assert any(f['code']==expected for f in manifest['failures'])


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
    monkeypatch.setattr(exporter,'_serialization_triangles',lambda *a:pytest.fail('regression comparison in production'))
    result=exporter.export_assembly(assembly,tmp_path/'output',source_sha256='test',
        expected={**CONFIG,'validation_mode':'visual_only'})
    assert evaluated==['bar.glb','stem.glb']
    assert result['status']=='NOT_EVALUATED' and result['export_status']=='PASS'
    assert result['diagnostic']['display_available']
    assert result['diagnostic']['semantic_completeness']=='NOT_EVALUATED'
    assert result['backend']['within_part_union']=='NOT_EXECUTED'
    assert result['triangle_comparison']=='NOT_EXECUTED'
    assert not any('connected_components' in p for p in result['parts'])


def test_mode_is_explicit_and_old_default_unchanged():
    assert FixedAssemblyConfig.model_validate(CONFIG).validation_mode=='geometry'
    assert not FixedAssemblyConfig.model_validate(CONFIG).require_multiple_parts
    assert FixedAssemblyConfig.model_validate({**CONFIG,'require_multiple_parts':True}).require_multiple_parts
    with pytest.raises(ValueError): FixedAssemblyConfig.model_validate({**CONFIG,'require_multiple_parts':'false'})
    assert FixedAssemblyConfig.model_validate({**CONFIG,'validation_mode':'visual_only'}).validation_mode=='visual_only'
    with pytest.raises(ValueError): FixedAssemblyConfig.model_validate({**CONFIG,'validation_mode':'ignore_everything'})


def test_serialization_redundant_faces_do_not_change_surface_or_mutate_mesh(tmp_path):
    import trimesh
    from test_fixed_assembly_exports import _files
    mesh,manifest,_=_files(tmp_path)
    faces=np.vstack([mesh.faces,mesh.faces[:2],[[0,0,1]]])
    reference=trimesh.Trimesh(mesh.vertices.copy(),faces,process=False)
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':reference},compare_triangles=True)
    assert not manifest['failures']
    assert np.array_equal(reference.faces,faces)  # No mesh repair/filtering.
    for row in manifest['export_consistency']:
        assert row['reference_encoding']['exact_zero_area_faces']==1
        assert row['reference_encoding']['exact_duplicate_surface_faces']==2
    # Losing a real surface must still fail, even when all vertices remain.
    reference=trimesh.Trimesh(mesh.vertices.copy(),mesh.faces[1:],process=False)
    exporter._verify_written_exports(tmp_path,manifest,{},surface_meshes={'part':reference},compare_triangles=True)
    assert any(f['code']=='EXPORTED_FILE_GEOMETRY_MISMATCH' for f in manifest['failures'])


def test_serialization_keeps_tiny_positive_area_triangles():
    import trimesh
    mesh=trimesh.Trimesh([[0,0,0],[1,0,0],[0,1e-12,0]],[[0,1,2]],process=False)
    triangles,info=exporter._serialization_triangles(mesh)
    assert len(triangles)==1 and info['exact_zero_area_faces']==0


@pytest.mark.parametrize('last_pass', [False,True])
def test_five_round_budget_allows_four_repairs_including_last_attempt(tmp_path,monkeypatch,last_pass):
    state=mock_flow(tmp_path,monkeypatch,[('NOT_EVALUATED',False)]*4+[('NOT_EVALUATED',last_pass)])
    state=(state[0],replace(state[1],checker_specs=(),max_rounds=5,
        fixed_assembly={**CONFIG,'validation_mode':'visual_only'}),*state[2:])
    execute=flow.execute_asset_source
    def display(*a,**kw):
        result=execute(*a,**kw)
        path=result.output_root/'assembly/assembly_manifest.json'
        row=json.loads(path.read_text())
        row.update(export_status='PASS',diagnostic={'display_available':True})
        path.write_text(json.dumps(row))
        return result
    monkeypatch.setattr(flow,'execute_asset_source',display)
    result,book=run_flow(state)
    assert len(state[-1])==4 and book['max_rounds']==5
    assert book['working']=='attempt_0004'
    assert book['retained']==('attempt_0004' if last_pass else 'original')
    assert result.approved is last_pass
    assert book['stop_reason']==('visual_code_and_export_passed' if last_pass else 'round_budget_exhausted')
    assert [call['payload']['remaining_repairs_after_this_attempt'] for call in state[-1]]==[3,2,1,0]
    assert all(call['payload']['current_repair_authorized'] for call in state[-1])
    assert all('remaining_repairs' not in call['payload'] for call in state[-1])
    assert 'count excludes the current attempt' in state[-1][-1]['payload']['assignment']
