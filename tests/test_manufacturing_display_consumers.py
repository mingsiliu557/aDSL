"""Manufacturing/checker inputs and print files survive missing display assets."""
from dataclasses import replace
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from adsl.agents import assembly_physics as physics, assembly_topology as topology
from adsl.agents import assembly_overhang as overhang, fixed_assembly as flow
from adsl.agents.checkers import CheckerRun
from adsl.agents.models import CheckerResult
from adsl.agents.overhang_edit import version_record
from adsl.agents.partition_score import OBJECTIVE, publish_print_layout
from adsl.agents.source_index import parse_source_nodes
from adsl.agents.utils.execution import ExecutionResult
from adsl.agents.utils.io import write_json
from test_assembly_physics import fixture, OVERHANG
from test_fixed_assembly import mock_flow, run_flow
from test_partition_feedback import measured_fixture


@pytest.mark.parametrize('mode', ['geometry', 'visual_only'])
def test_no_display_fixed_flow_keeps_manufacturing_checks_and_unknown_appearance(
        tmp_path, monkeypatch, mode):
    state = mock_flow(tmp_path, monkeypatch, [('PASS' if mode=='geometry' else 'NOT_EVALUATED', True)])
    w, request, runtime, source, _ = state
    specs = (topology.checker_spec(), physics.checker_spec('assembly_overhang'),
             physics.checker_spec('assembly_standing'))
    request = replace(request, max_rounds=1, checker_specs=specs,
                      fixed_assembly={**request.fixed_assembly, 'validation_mode':mode})
    execute = flow.execute_asset_source
    def manufacture(*args, **kwargs):
        execution = execute(*args, **kwargs)
        execution.glb_path.unlink()
        for image in execution.render_paths:
            image.unlink()
        path = execution.output_root/'assembly/assembly_manifest.json'
        report = json.loads(path.read_text())
        report.update(manufacturing_status='PASS', display_status='FAIL', export_status='FAIL',
            display_failures=[{'code':'TARGET_PRECISION_UNREPRESENTABLE', 'stage':'target_precision',
                'part_id':'part', 'failure_kind':'candidate_evaluation',
                'diagnostic':{'code':'TARGET_PRECISION_UNREPRESENTABLE', 'stage':'target_precision',
                    'failure_kind':'target_precision', 'node_path':'part/union',
                    'operation':'UNION', 'input_count':3}}],
            diagnostic={'display_available':False, 'missing_parts':['part']})
        write_json(path, report)
        return replace(execution, render_paths=())
    monkeypatch.setattr(flow, 'execute_asset_source', manufacture)
    measured = []
    def checks(selected_specs, *, execution, source, root, **kwargs):
        assert not execution.glb_path.exists()
        assert (execution.glb_path.parent/'assembly_manifest.json').is_file()
        results = []
        for spec in selected_specs:
            measured.append(spec.name)
            out = root/'checkers'/spec.name
            result = CheckerResult(checker=spec.name, status='PASS', summary='offline mock measurement',
                                   assumptions={'source_sha256':flow.file_hash(source)})
            write_json(out/'result.json', result.model_dump())
            results.append(CheckerRun(spec, result, out, ()))
        return results
    monkeypatch.setattr(physics, 'run_assembly_checks', checks)
    (tmp_path/'scene.glb').write_bytes(b'stale earlier display')
    (tmp_path/'assembly').mkdir()
    (tmp_path/'assembly/scene.glb').write_bytes(b'stale earlier assembly display')
    (tmp_path/'assembly/old_part.stl').write_bytes(b'stale earlier print part')
    result, book = run_flow((w, request, runtime, source, state[-1]))
    assert measured==[s.name for s in specs]
    assert result.glb_path is None and not result.approved
    assert book['versions']['original']['reviews']['appearance_approved'] is None
    assert book['versions']['original']['reviews']['assembly_standing']['status']=='PASS'
    saved = json.loads((tmp_path/'assembly_result.json').read_text())
    assert saved['manufacturing_status']=='PASS' and saved['display_status']=='FAIL'
    assert saved['physical_validation']=='SELECTED_SCOPE_PASSED'
    assert saved['checker_statuses']=={s.name:'PASS' for s in specs}
    assert saved['visual_code_approved'] is None and not saved['approved']
    assert (tmp_path/'assembly/part.stl').is_file() and not (tmp_path/'scene.glb').exists()
    assert not (tmp_path/'assembly/scene.glb').exists()
    assert not (tmp_path/'assembly/old_part.stl').exists()
    assert book['feedback']['failure_feedback'][0]['output_role']=='display'
    assert book['feedback']['failure_feedback'][0]['geometry_repair_allowed']
    assert book['versions']['original']['reviews']['geometry']['failures']==[]


def test_missing_display_stays_strict_without_explicit_manufacturing_contract(tmp_path):
    source = tmp_path/'source.py'; source.write_text('original')
    execution = ExecutionResult(tmp_path, tmp_path/'absent.glb', None, (), '', '')
    with pytest.raises(ValueError, match='generated asset is missing'):
        version_record('original', source, execution, reviews={'geometry':{'status':'PASS'}})


def test_late_unbound_display_file_cannot_replace_saved_missing_display_state(tmp_path,monkeypatch):
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    request=replace(state[1],max_rounds=1,checker_specs=())
    execute=flow.execute_asset_source
    def no_display(*args,**kwargs):
        execution=execute(*args,**kwargs)
        execution.glb_path.unlink()
        for image in execution.render_paths:
            image.unlink()
        path=execution.output_root/'assembly/assembly_manifest.json'
        report=json.loads(path.read_text())
        report.update(manufacturing_status='PASS',display_status='FAIL',export_status='FAIL',
                      scene_glb=None,diagnostic={'display_available':False})
        write_json(path,report)
        return replace(execution,render_paths=())
    monkeypatch.setattr(flow,'execute_asset_source',no_display)
    assets=flow.version_assets
    def late_artifact(record):
        source,execution,runs=assets(record)
        assert str(execution.glb_path) not in record['files']
        execution.glb_path.write_bytes(b'late display not bound to saved version')
        return source,execution,runs
    monkeypatch.setattr(flow,'version_assets',late_artifact)
    result,book=run_flow((state[0],request,*state[2:]))
    assert result.glb_path is None and not result.approved
    assert not (tmp_path/'scene.glb').exists()
    assert book['versions']['original']['reviews']['geometry']['display_status']=='FAIL'
    assert (tmp_path/'assembly/part.stl').exists()


def test_valid_stls_do_not_override_visual_only_declaration_contract_failure(tmp_path, monkeypatch):
    state = mock_flow(tmp_path, monkeypatch, [('NOT_EVALUATED',True)])
    request = replace(state[1], max_rounds=1, checker_specs=(),
                      fixed_assembly={**state[1].fixed_assembly,'validation_mode':'visual_only'})
    execute = flow.execute_asset_source
    def broken_contract(*args, **kwargs):
        execution = execute(*args, **kwargs)
        path = execution.output_root/'assembly/assembly_manifest.json'
        report = json.loads(path.read_text())
        report.update(manufacturing_status='PASS',display_status='PASS',export_status='FAIL',
            diagnostic={'display_available':True},failures=[{'code':'ROOT_CHANGED',
                'failure_kind':'candidate_geometry','stage':'declaration'}])
        write_json(path,report)
        return execution
    monkeypatch.setattr(flow,'execute_asset_source',broken_contract)
    result,book=run_flow((state[0],request,*state[2:]))
    assert book['versions']['original']['reviews']['appearance_approved']
    assert not result.approved and not book['versions']['original']['reviews']['accepted']


@pytest.mark.parametrize('failed_file', ['scene.glb','exploded.glb'])
def test_explicit_scene_readback_failure_overrides_stale_diagnostic_and_vlm_approval(
        tmp_path,monkeypatch,failed_file):
    state=mock_flow(tmp_path,monkeypatch,[('PASS',True)])
    request=replace(state[1],max_rounds=1,checker_specs=())
    execute=flow.execute_asset_source
    def display_failed(*args,**kwargs):
        execution=execute(*args,**kwargs)
        path=execution.output_root/'assembly/assembly_manifest.json'
        report=json.loads(path.read_text())
        report.update(manufacturing_status='PASS',display_status='FAIL',export_status='FAIL',
            scene_glb='scene.glb' if failed_file=='exploded.glb' else None,
            diagnostic={'display_available':True},
            display_failures=[{'code':'EXPORTED_FILE_INVALID','file':failed_file,
                'failure_kind':'export','stage':'read_written_exports'}],
            export_consistency=[{'file':'scene.glb','status':
                'PASS' if failed_file=='exploded.glb' else 'FAIL','output_role':'display'},
                {'file':'part.stl','status':'PASS','output_role':'manufacturing'}])
        write_json(path,report)
        return execution
    monkeypatch.setattr(flow,'execute_asset_source',display_failed)
    result,book=run_flow((state[0],request,*state[2:]))
    review=book['versions']['original']['reviews']
    assert review['geometry']['manufacturing_status']=='PASS'
    assert review['geometry']['failures']==[]
    assert review['image_critic']['approved']
    if failed_file=='scene.glb':
        assert review['appearance_approved'] is None and not result.approved
    else:
        assert review['appearance_approved'] and result.approved


def test_display_feedback_keeps_valid_manufacturing_and_verified_source_identity(tmp_path):
    source = tmp_path/'source.py'
    source.write_text('class Piece:\n    value = 1\n')
    sid = next(n.source_id for n in parse_source_nodes(source) if n.kind=='class')
    index = tmp_path/'source_index.json'
    write_json(index, {'source_sha256':flow.file_hash(source), 'features':[
        {'feature_id':'feature:Piece', 'name':'Piece', 'resolution':'complete',
         'source_ids':[sid, 'source:unverified']} ]})
    report = dict(source_sha256=flow.file_hash(source), manufacturing_status='PASS',
        display_status='FAIL', failures=[], part_declarations=[{'id':'part','components':['Piece']}],
        display_failures=[{'part_id':'part', 'failure_kind':'candidate_evaluation',
            'code':'TARGET_PRECISION_UNREPRESENTABLE', 'stage':'target_precision',
            'diagnostic':{'code':'TARGET_PRECISION_UNREPRESENTABLE', 'stage':'target_precision',
                'failure_kind':'target_precision', 'node_path':'Piece/union',
                'operation':'UNION', 'input_count':3, 'source_ids':['source:unverified']}},
            {'code':'PART_EXPORT_FAILED','failure_kind':'export','stage':'write_glb',
             'reason':'OSError: display destination unavailable'}])
    feedback = flow._failure_feedback(report, source_sha256=flow.file_hash(source))
    assert feedback[0]['geometry_repair_allowed'] and not feedback[1]['geometry_repair_allowed']
    evidence = topology.evaluation_evidence_run(report, feedback, source, tmp_path/'evidence', index)
    assert len(evidence.result.findings)==1
    finding = evidence.result.findings[0]
    assert finding.region.details['output_role']=='display'
    assert finding.region.details['manufacturing_status']=='PASS'
    assert finding.region.details['physical_verdict']=='NOT_EVALUATED'
    assert finding.source_candidates[0].source_ids==[sid]
    assert report['manufacturing_status']=='PASS' and report['failures']==[]


def test_real_checker_processes_accept_complete_stls_without_scene_glb(tmp_path):
    args, _ = fixture(tmp_path/'assembly')
    execution = ExecutionResult(tmp_path, args.manifest.parent/'scene.glb', None, (), '', '')
    assert not execution.glb_path.exists()
    runs = physics.run_assembly_checks([physics.checker_spec('assembly_overhang'), topology.checker_spec()],
        execution=execution, source=args.source, root=tmp_path/'verification',
        physics={'overhang':OVERHANG})
    assert [r.spec.name for r in runs]==['assembly_topology','assembly_overhang']
    assert all(r.result.status=='PASS' for r in runs), [r.result.model_dump() for r in runs]
    items = runs[0].result.metrics['items']
    assert {r['connection_id'] for r in items if r['kind']=='interface'}=={'joint'}
    assert all(r['status']=='PASS' for r in items)
    assert len(runs[1].result.metrics['items'])==2


def test_optional_overlay_error_preserves_partition_score_and_verified_print_bundle(tmp_path, monkeypatch):
    args, _, baseline = measured_fixture(tmp_path/'assembly')
    monkeypatch.setattr(overhang, '_display_overlay', lambda mesh, path:
                        {'status':'FAIL', 'reason':'offline float32 display failure', 'file':str(path)})
    config = {'overhang':dict(OVERHANG, partition_objective=OBJECTIVE),
        'partition_reference_path':baseline.artifacts['partition_reference']}
    result = overhang.analyze(args, config)
    assert result.status=='PASS'
    assert result.metrics['partition_objective']==baseline.metrics['partition_objective']
    assert all(row['display_overlay']['status']=='FAIL' for row in result.metrics['items'])
    assert all(row['recommended_pose_display_overlay']['status']=='FAIL' for row in result.metrics['items'])
    assert all(row['recommended_print_validation']['exact_triangles_match'] for row in result.metrics['items'])
    layout, parts = publish_print_layout(result.model_dump(), tmp_path/'published',
                                        flow.file_hash(args.source), 'original')
    assert Path(layout).is_file() and len(parts)==2
    assert all(Path(row['stl']).read_bytes().startswith(b'solid ') for row in parts)
    assert all(row['file_validation']['status']=='PASS' for row in parts)
    assert not (args.manifest.parent/'scene.glb').exists()


def test_invalid_float32_overlay_is_recorded_without_changing_material_mesh(tmp_path):
    mesh = trimesh.Trimesh([[0,1,-50],[0,0,5.5],[1,0,5.5],[.5,0,5.5000005]],
        [[0,2,1],[0,1,3],[0,3,2],[1,2,3]], process=False)
    if mesh.volume < 0:
        mesh.invert()
    mesh.apply_translation((0,0,50))
    original = mesh.triangles.copy()
    mesh.visual.face_colors=np.tile([170,180,190,255], (len(mesh.faces),1))
    status = overhang._display_overlay(mesh, tmp_path/'overlay.glb')
    assert status['status']=='FAIL' and status['metrics']['zero_area_triangles']>0
    assert np.array_equal(original, mesh.triangles)


def test_changed_recommended_stl_is_rejected_as_print_file_not_physical_failure(tmp_path, monkeypatch):
    args, _, baseline = measured_fixture(tmp_path/'assembly')
    export = trimesh.Trimesh.export
    def corrupt(self, path, **kwargs):
        if str(path).endswith('.recommended.stl'):
            return export(trimesh.creation.box((1,1,1)), path, **kwargs)
        return export(self, path, **kwargs)
    monkeypatch.setattr(trimesh.Trimesh, 'export', corrupt)
    result = overhang.analyze(args, {'overhang':dict(OVERHANG, partition_objective=OBJECTIVE),
        'partition_reference_path':baseline.artifacts['partition_reference']})
    assert result.status=='INDETERMINATE'
    assert result.metrics['partition_objective']['score'] is None
    assert all(row['gap_voxels'] is None and row['status']=='INDETERMINATE'
               for row in result.metrics['items'])
    assert all('STL does not match' in row['reason'] for row in result.metrics['items'])
    assert not any(f.category=='physical_violation' for f in result.findings)
