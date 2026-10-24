"""Targeted offline protocol tests; no model invocation or production job."""
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh

_PATH = Path(__file__).resolve().parents[1] / "experiments" / "benchmark_six" / "evaluate.py"
_SPEC = importlib.util.spec_from_file_location("six_offline_evaluate", _PATH)
evaluation = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(evaluation)


def official_fixture(root, *, disconnected=False):
    root.mkdir(parents=True, exist_ok=True)
    source = root / "source.py"
    source.write_text("# frozen selected native model\n")
    scene = trimesh.Scene()
    parent = np.eye(4)
    parent[:3, 3] = [2, 3, 4]
    scene.graph.update(frame_to="parent", matrix=parent)
    scene.add_geometry(trimesh.creation.box([2, 2, 2]), node_name="red_node",
                       geom_name="red_material", parent_node_name="parent")
    child = np.eye(4)
    child[0, 3] = 4 if disconnected else 1
    scene.add_geometry(trimesh.creation.box([2, 2, 2]), node_name="blue_node",
                       geom_name="blue_material", parent_node_name="parent", transform=child)
    glb = root / "selected.glb"
    scene.export(glb)
    return source, glb


def test_official_scene_transforms_material_nodes_are_one_body(tmp_path):
    from adsl.agents.assembly_physics import load_parts
    source, glb = official_fixture(tmp_path)
    original_hash = evaluation.sha256(glb)
    path = evaluation.official_manifest(glb, source, tmp_path / "adapted")
    manifest = evaluation.read_json(path)
    assert len(manifest["parts"]) == len(manifest["part_declarations"]) == 1
    assert manifest["connections"] == []
    assert len(manifest["parts"][0]["mesh_face_groups"]) == 2
    assert manifest["normalization_scale"] == pytest.approx(50)
    # Parent translation and glTF Y->Z conversion are both applied once.
    node_boxes = np.asarray([r["world_bounds"] for r in manifest["raw_scene_nodes"]])
    assert node_boxes[:, 0].min(axis=0).tolist() == pytest.approx([1, -5, 2])
    output = tmp_path / "measure"
    output.mkdir()
    _, rows, meshes = load_parts(SimpleNamespace(manifest=path, source=source, output=output, topology_cache=None))
    assert rows[0]["status"] == "PASS"
    # Two overlapping 2-cubes: true union volume is 12, not 16.
    assert meshes["whole"].volume == pytest.approx(12 * 50 ** 3)
    assert evaluation.sha256(glb) == original_hash


def test_disconnected_official_keeps_n_one_and_material_measurement(tmp_path):
    from adsl.agents.assembly_physics import load_parts
    source, glb = official_fixture(tmp_path, disconnected=True)
    path = evaluation.official_manifest(glb, source, tmp_path / "adapted")
    output = tmp_path / "measure"
    output.mkdir()
    _, rows, meshes = load_parts(SimpleNamespace(manifest=path, source=source, output=output, topology_cache=None))
    assert len(rows) == 1 and rows[0]["status"] == "FAIL"
    assert rows[0]["component_count"] == 2
    assert set(meshes) == {"whole"}  # reliable material remains measurable


def test_open_baseline_is_unmeasurable_without_fill(tmp_path):
    from adsl.agents.assembly_physics import load_parts
    source = tmp_path / "source.py"
    source.write_text("# open native surface\n")
    box = trimesh.creation.box()
    box.update_faces(np.arange(len(box.faces) - 1))
    glb = tmp_path / "open.glb"
    trimesh.Scene(box).export(glb)
    path = evaluation.official_manifest(glb, source, tmp_path / "adapted")
    output = tmp_path / "measure"
    output.mkdir()
    _, rows, meshes = load_parts(SimpleNamespace(manifest=path, source=source, output=output, topology_cache=None))
    assert rows[0]["status"] == "INDETERMINATE"
    assert not meshes
    assert evaluation.read_json(path)["mesh_repair"] == "NONE"


def ours_fixture(root):
    from test_assembly_physics import fixture
    return fixture(root)


def test_ours_scales_native_parts_frames_and_connector_dimensions_once(tmp_path):
    from adsl.core.assembly_topology import read_print_mesh, mm_matrix
    args, native = ours_fixture(tmp_path / "native")
    original_hash = evaluation.sha256(args.manifest)
    path = evaluation.ours_manifest(args.manifest, args.source, tmp_path / "adapted")
    manifest = evaluation.read_json(path)
    factor = manifest["normalization_scale"]
    assert factor == pytest.approx(2.5)
    assert manifest["mm_per_unit"] == pytest.approx(2.5)
    assert len(manifest["parts"]) == len(native["parts"]) == 2
    assert manifest["connections"][0]["parameters"]["fit_offset_mm"] == pytest.approx(.5)
    for before, after in zip(native["parts"], manifest["parts"]):
        original = read_print_mesh(args.manifest.parent / before["stl"], before["print_transform_mm"])
        scaled = read_print_mesh(path.parent / after["stl"], after["print_transform_mm"])
        np.testing.assert_allclose(scaled.bounds, original.bounds * factor, atol=1e-10)
        first = mm_matrix(before["assembly_transform"], native["mm_per_unit"])
        second = mm_matrix(after["assembly_transform"], manifest["mm_per_unit"])
        np.testing.assert_allclose(second[:3, 3], first[:3, 3] * factor)
        np.testing.assert_array_equal(second[:3, :3], first[:3, :3])
    assert evaluation.sha256(args.manifest) == original_hash


def test_missing_native_declared_part_cannot_use_partial_normalization(tmp_path):
    args, native = ours_fixture(tmp_path / "native")
    (args.manifest.parent / native["parts"][1]["stl"]).unlink()
    with pytest.raises(ValueError, match="complete native part bounds unavailable"):
        evaluation.ours_manifest(args.manifest, args.source, tmp_path / "adapted")


def test_print_metrics_real_solid_and_independent_area_gap_poses(tmp_path, monkeypatch):
    from adsl.agents import assembly_physics, partition_score
    from adsl.agents import support_volume
    support_calls=[]
    def measure_support(mesh, **kwargs):
        support_calls.append((mesh.bounds.copy(),kwargs))
        return dict(status='PASS',support_volume_mm3=3.,mesh_volume_mm3=24.,nsv=.125,
            risky_projected_area_mm2=1.,risky_face_count=1,ray_backend='stub')
    monkeypatch.setattr(support_volume,'support_volume',measure_support)
    mesh = trimesh.creation.box([2, 3, 4])
    # Freeze a nonzero G outcome at rotation 9 to catch accidental use of the
    # area-selected orientation (the real cube's minimum area ties at rotation 0).
    monkeypatch.setattr(assembly_physics, "load_parts", lambda args:
                        ({"parts": [{"id": "p"}], "part_declarations": [{"id": "p"}]},
                         [{"part_id": "p", "status": "FAIL"}], {"p": mesh}))
    real = partition_score.best_print_pose
    gap_real = real(mesh, {"voxel_pitch_mm": 2})
    assert gap_real["gap_voxels"] == 0 and len(gap_real["orientations"]) == 24
    monkeypatch.setattr(partition_score, "best_print_pose", lambda *args:
                        {"gap_voxels": 7, "selected_rotation_id": 9,
                         "recommended_print_transform_mm": np.eye(4).tolist(), "orientations": []})
    result = evaluation.print_measurements("unused", "unused", tmp_path, 2.)
    assert result["N"] == 1 and result["G"] == 7
    assert result["gap_volume_mm3"] == 56
    assert result["material_volume_mm3"] == pytest.approx(24)
    assert result["min_overhang_area_mm2"] == 0
    row = result["parts"][0]
    assert row["geometry_status"] == "FAIL" and row["status"] == "PASS"
    assert row["gap_best_pose"]["selected_rotation_id"] == 9
    assert row["area_best_pose"]["rotation_id"] == 0
    assert len(row["area_orientations"]) == 24
    assert len(support_calls)==24
    assert result['support']['nsv_total']==.125
    expected=np.eye(4);expected[:3,3]=-mesh.bounds[0]
    assert row['support_print_transform_mm']==expected.tolist()
    assert row['support_best_pose']['selected_rotation_id']==0  # deterministic tie


def prepare_job(root, case_id="Toys4K_dragon_007", arm="official", failed=True):
    case = {"case_id": case_id, "requirement": "A flying dragon in the shown pose.",
            "input_image": "/tmp/reference.png", "voxel_pitch_mm": 2.3, "standing_applicable": True}
    evaluation.write_json(root / "config" / "cases.json", {"cases": [case]})
    job = {"case_id": case_id, "arm": arm, "generation": {"status": "generation_failed" if failed else "completed"}}
    evaluation.write_json(root / "jobs" / case_id / arm / "job.json", job)
    return case, job


def test_failed_generation_keeps_denominators_dragon_na_and_null_metrics(tmp_path, monkeypatch):
    prepare_job(tmp_path)
    monkeypatch.setattr(evaluation, "render_final", lambda *a, **k: pytest.fail("failed output rendered"))
    result = evaluation.evaluate(tmp_path, "Toys4K_dragon_007", "official")
    assert result["status"] == "FAIL" and result["included_in_denominator"]
    assert result["denominators"] == {"appearance": True, "printing": True, "standing": False}
    assert result["standing"]["status"] == result["interface"]["status"] == "NA"
    assert result["metrics"]["N"] == 1
    assert result["metrics"]["G"] is result["metrics"]["min_overhang_area_mm2"] is None
    assert result["native_dapper"]["status"] == "NA"
    request = evaluation.read_json(tmp_path / "jobs" / "Toys4K_dragon_007" / "official" / "evaluation" / "image_critic_request.json")
    assert request["status"] == "INDETERMINATE" and request["rendered_images"] == []
    assert "arm" not in request


@pytest.mark.parametrize("arm", ["official", "ours"])
def test_api_interrupted_generation_stays_unknown_in_fixed_denominator(tmp_path, monkeypatch, arm):
    _, job = prepare_job(tmp_path, arm=arm)
    job["generation"] = {"status": "API_INTERRUPTED", "reason": "APIStatusError: HTTP 408",
                         "api_interruptions": [{"stage": "initial_code", "status_code": 408}]}
    directory = tmp_path / "jobs" / "Toys4K_dragon_007" / arm
    evaluation.write_json(directory / "job.json", job)
    monkeypatch.setattr(evaluation, "render_final", lambda *a, **k: pytest.fail("interrupted output rendered"))
    result = evaluation.evaluate(tmp_path, "Toys4K_dragon_007", arm)
    assert result["status"] == "API_INTERRUPTED" and result["included_in_denominator"]
    assert result["failure_stage"] == "generation_api_interruption"
    assert result["denominators"] == {"appearance": True, "printing": True, "standing": False}
    assert result["appearance"]["status"] == result["topology"]["status"] == "INDETERMINATE"
    assert result["metrics"]["G"] is result["metrics"]["min_overhang_area_mm2"] is None
    assert result["standing"]["status"] == "NA"
    assert result["generation"]["api_interruptions"][0]["status_code"] == 408


def test_render_failure_never_falls_back_to_native_generation_views(tmp_path, monkeypatch):
    _, job = prepare_job(tmp_path, "robot_050", failed=False)
    directory = tmp_path / "jobs" / "robot_050" / "official"
    source, glb = official_fixture(directory)
    job.update(source=str(source), selected_glb=str(glb), render_paths=["original_camera.png"] * 8)
    evaluation.write_json(directory / "job.json", job)
    monkeypatch.setattr(evaluation, "render_final", lambda *a:
                        {"status": "INDETERMINATE", "native_views": [], "neutral_views": []})
    monkeypatch.setattr(evaluation, "physical_checks", lambda *a: (_ for _ in ()).throw(ValueError("invalid material")))
    result = evaluation.evaluate(tmp_path, "robot_050", "official")
    assert result["appearance"]["status"] == "INDETERMINATE"
    assert result["metrics"]["G"] is result["metrics"]["min_overhang_area_mm2"] is None
    request = evaluation.read_json(directory / "evaluation" / "image_critic_request.json")
    assert request["rendered_images"] == [] and request["read_only"]
    assert result['standing_evaluation']['evaluation_outcome']=='FAIL'
    assert result['main_table_metrics']['nsv'] is None


def test_pending_generation_is_not_evaluated_or_counted_failed(tmp_path,monkeypatch):
    _,job=prepare_job(tmp_path,case_id='pending')
    job['generation']['status']='RUNNING'
    evaluation.write_json(tmp_path/'jobs/pending/official/job.json',job)
    monkeypatch.setattr(evaluation,'physical_checks',lambda *a:pytest.fail('pending task measured'))
    monkeypatch.setattr(evaluation,'render_final',lambda *a:pytest.fail('pending task rendered'))
    monkeypatch.setattr(evaluation.geometry_metrics,'load_evaluation_parts',
                        lambda *a:pytest.fail('pending task loaded for connectivity'))
    result=evaluation.evaluate(tmp_path,'pending','official')
    assert result['standing_evaluation']['evaluation_outcome']=='PENDING'
    assert result['main_table_metrics']['N'] is None
    assert result['dataset_eligibility']['status']=='PENDING'
    assert result['dataset_eligibility']['eligible'] is False


def test_terminal_generation_failure_counts_without_observed_tipping(tmp_path):
    prepare_job(tmp_path,case_id='failed')
    result=evaluation.evaluate(tmp_path,'failed','official')
    row=result['standing_evaluation']
    assert row['evaluation_outcome']=='FAIL' and 'GENERATION_FAILED' in row['reason_codes']
    assert row['observation'] is None
    assert result['main_table_metrics']['nsv'] is None
    assert result['dataset_eligibility']['status']=='EXCLUDED'
    assert result['dataset_eligibility']['eligible'] is False
    assert result['connectivity']['reason_codes']==['GENERATION_FAILED']


def test_standing_pass_is_independent_in_evaluation_record():
    from test_physical_metrics import record
    raw=record()
    result=dict(case_id='one',denominators={'standing':True},generation={'status':'COMPLETED'},
        standing=raw['checker'],topology={'status':'FAIL','metrics':{'items':[]}},
        metrics={},selected_output_sha256='asset',selected_source_sha256='source')
    evaluation.attach_physical_evaluation(result,state='TERMINAL')
    assert result['standing_evaluation']['evaluation_outcome']=='PASS'
    assert result['topology']['status']=='FAIL'


def test_reporting_uses_all_cases_and_lists_metric_samples(tmp_path):
    from experiments.benchmark_six.build_review import build
    from test_physical_metrics import record
    evaluation.write_json(tmp_path/'config/cases.json',{'cases':[
        {'case_id':'one','standing_applicable':True},
        {'case_id':'later','standing_applicable':True}]})
    for arm in ('official','ours'):
        folder=tmp_path/'jobs/one'/arm
        evaluation.write_json(folder/'job.json',{'generation':{'status':'COMPLETED'}})
        evaluation.write_json(folder/'evaluation/result.json',{'case_id':'one', 'arm':arm, 'selected_source_sha256':'source',
            'physical_evaluation_record':record()})
    result=build(tmp_path)
    assert result['physical']['status']=='INCOMPLETE'
    for row in result['physical']['table']:
        assert row['standing_success_rate'] is None
        assert row['structural_connectivity_rate'] is row['nsv'] is row['N'] is None
    sample=result['physical']['samples']['ours']['standing']
    assert sample['planned_total']==2 and sample['success_count']==1 and sample['pending_count']==1
    assert (tmp_path/'review/physical_metrics.json').is_file()


def test_report_builder_rejects_legacy_protocol_without_rewriting(tmp_path):
    from experiments.benchmark_six.build_review import build
    evaluation.write_json(tmp_path/'config/cases.json',{'cases':[{'case_id':'old'}]})
    path=tmp_path/'jobs/old/official/evaluation/result.json'
    evaluation.write_json(path,{'standing':{'status':'INDETERMINATE'}})
    before=path.read_bytes()
    with pytest.raises(ValueError,match='legacy'):
        build(tmp_path)
    assert path.read_bytes()==before


def test_render_command_uses_common_cli_profile_without_a_real_render(tmp_path, monkeypatch):
    calls = []
    def fake_run(command, **kwargs):
        calls.append((command, kwargs["env"]))
        directory = Path(command[command.index("--output-dir") + 1])
        for index in range(1, 9):
            (directory / f"render_{index:04d}.png").write_bytes(b"test-image")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(evaluation.subprocess, "run", fake_run)
    result = evaluation.render_final(tmp_path / "native_selected.glb", tmp_path / "views")
    assert result["status"] == "PASS"
    assert len(result["native_views"]) == len(result["neutral_views"]) == 8
    for command, env in calls:
        assert "bpy.context.scene.cycles.device = 'CPU'" in command[2]
        assert command[command.index("--view-layout") + 1] == "review_eight"
        assert command[command.index("--width") + 1] == "512"
        assert command[command.index("--render-samples") + 1] == "64"
        assert env["ADSL_RENDER_ENGINE"] == "CYCLES" and env["CUDA_VISIBLE_DEVICES"] == ""


def test_existing_topology_subprocess_measures_official_whole(tmp_path):
    source, glb = official_fixture(tmp_path / "native")
    path = evaluation.official_manifest(glb, source, tmp_path / "adapted")
    results, paths = evaluation.physical_checks(path, source, tmp_path / "physical", False)
    assert results["assembly_topology"]["status"] == "PASS"
    assert results["assembly_standing"]["status"] == "NA"
    assert set(paths) == {"topology"}
    assert Path(paths["topology"]).is_file()
    assert not (tmp_path / "physical" / "checkers" / "assembly_fea").exists()


def test_native_dapper_only_accepts_selected_source_and_manifest_binding(tmp_path):
    args, _ = ours_fixture(tmp_path / "selected")
    path = tmp_path / "native_overhang.json"
    report = {"assumptions": {"source_sha256": evaluation.sha256(args.source),
                               "manifest_sha256": evaluation.sha256(args.manifest)},
              "metrics": {"partition_objective": {"score": 99, "gap_voxels": 0}}}
    evaluation.write_json(path, report)
    job = {"arm": "ours", "source": str(args.source), "selected_manifest": str(args.manifest),
           "selected_source_sha256": evaluation.sha256(args.source), "native_partition_result": str(path)}
    assert evaluation.native_dapper(job, tmp_path)["status"] == "AVAILABLE"
    assert not evaluation.native_dapper(job, tmp_path)["cross_method_comparable"]
    # A rejected working candidate may have a better score but the wrong source.
    report["assumptions"]["source_sha256"] = "rejected-candidate-source"
    report["metrics"]["partition_objective"]["score"] = 1000
    evaluation.write_json(path, report)
    assert evaluation.native_dapper(job, tmp_path)["status"] == "UNAVAILABLE"
    # Merely finding a report in a nearby checker directory is insufficient.
    job.pop("native_partition_result")
    assert evaluation.native_dapper(job, tmp_path)["status"] == "UNAVAILABLE"


def test_partial_print_measurement_keeps_unknown_totals_and_declared_n(tmp_path, monkeypatch):
    from adsl.agents import assembly_physics
    monkeypatch.setattr(assembly_physics, "load_parts", lambda args:
                        ({"parts": [], "part_declarations": [{"id": "missing"}]},
                         [{"part_id": "missing", "status": "INDETERMINATE", "reason": "open mesh"}], {}))
    result = evaluation.print_measurements("unused", "unused", tmp_path, .4)
    assert result["status"] == "INDETERMINATE" and result["N"] == 1
    assert result["G"] is result["gap_volume_mm3"] is result["min_overhang_area_mm2"] is None


@pytest.mark.parametrize('missing',[False,True])
def test_area_and_gap_optima_are_independent_with_stub_parts(tmp_path,monkeypatch,missing):
    from adsl.agents import assembly_physics,assembly_overhang,partition_score,support_volume
    report={'parts':[{'id':'a'},{'id':'b'}]}
    rows=[{'part_id':p['id'],'status':'PASS'} for p in report['parts']]
    class Mesh:
        vertices=np.array([[0.,0,0],[1,2,3]])
        volume=3.
        def copy(self): return Mesh()
        def apply_transform(self, transform): pass
    meshes={'a':Mesh(),'b':Mesh()} if not missing else {'a':Mesh()}
    monkeypatch.setattr(assembly_physics,'load_parts',lambda *a:(report,rows,meshes))
    calls=[]
    def area(mesh,t,config):
        i=len(calls)%24;calls.append(i)
        return {'area_mm2':2 if i==4 else 10,'uncertainty':{'bound_mm2':0}},None
    monkeypatch.setattr(assembly_overhang,'measure_part',area)
    monkeypatch.setattr(partition_score,'best_print_pose',lambda *a:dict(gap_voxels=7,selected_rotation_id=9,
        recommended_print_transform_mm=np.eye(4).tolist()))
    monkeypatch.setattr(support_volume,'support_volume',lambda *a,**k:dict(status='PASS',
        support_volume_mm3=1.,mesh_volume_mm3=3.,risky_projected_area_mm2=1.,ray_backend='stub'))
    out=evaluation.print_measurements('unused','unused',tmp_path,1.)
    assert out['N']==2 and len(calls)==(24 if missing else 48)
    assert out['parts'][0]['area_best_pose']['rotation_id']==4
    assert out['parts'][0]['gap_best_pose']['selected_rotation_id']==9
    assert out['min_overhang_area_mm2']==(None if missing else 4)
    assert out['G']==(None if missing else 14)
    assert out['support']['nsv_total']==(None if missing else pytest.approx(1/3))


def _stub_final_connectivity_flow(tmp_path, monkeypatch, arm, *, error_stage=None,
                                  measurable=True, binding_conflict=False):
    """Drive the actual final evaluator without models, simulation or geometry."""
    from copy import deepcopy
    from test_physical_metrics import record
    case_id = 'final_asset'
    _, job = prepare_job(tmp_path, case_id, arm, failed=False)
    directory = tmp_path / 'jobs' / case_id / arm
    source, glb = directory / 'source.py', directory / 'selected.glb'
    source.write_text('# frozen final source; never execute\n')
    glb.write_bytes(b'frozen native output; never render')
    original = directory / 'original_manifest.json'
    evaluation.write_json(original, dict(parts=[dict(id='body')]))
    job.update(source=str(source), selected_glb=str(glb), selected_manifest=str(original), approved=True)
    evaluation.write_json(directory / 'job.json', job)
    before = (directory / 'job.json').read_bytes()
    calls = []
    def manifest(*args):
        if error_stage == 'manifest':
            raise ValueError('manifest cannot be adapted')
        folder = Path(args[-1])
        folder.mkdir(parents=True, exist_ok=True)
        path = folder / 'assembly_manifest.json'
        stl = folder / 'body.stl'
        stl.write_bytes(b'hash-bound fixture mesh; loader is stubbed')
        evaluation.write_json(path, dict(parts=[dict(id='body', stl=stl.name)],
            source_sha256=evaluation.sha256(source), files_sha256={stl.name:evaluation.sha256(stl)}))
        return path
    monkeypatch.setattr(evaluation, 'official_manifest', manifest)
    monkeypatch.setattr(evaluation, 'ours_manifest', manifest)
    monkeypatch.setattr(evaluation, 'render_final', lambda *a:
        dict(status='PASS', native_views=['frozen.png']*8, neutral_views=[]))
    physics = dict(assembly_topology=dict(status='FAIL', metrics=dict(items=[])),
                   assembly_standing=record()['checker'])
    def check(*args):
        if error_stage == 'physics':
            raise ValueError('existing physical check unavailable')
        return deepcopy(physics), {}
    monkeypatch.setattr(evaluation, 'physical_checks', check)
    def printing(*args):
        if error_stage == 'printing':
            raise ValueError('existing printing measure unavailable')
        return dict(status='PASS', N=1, parts=[dict(part_id='body', material_volume_mm3=1.)])
    monkeypatch.setattr(evaluation, 'print_measurements', printing)
    monkeypatch.setattr(evaluation, 'native_dapper', lambda *a: dict(status='NA'))
    loaded_parts = [dict(part_id='body')]
    def load(record):
        calls.append(('load', record))
        data = record['geometry_input']
        assert data['kind']=='fixed_assembly' and data['source_path']==str(source)
        assert data['manifest_sha256']==evaluation.sha256(data['manifest_path'])
        return dict(status='PASS', asset_sha256='a'*64, parts=loaded_parts)
    monkeypatch.setattr(evaluation.geometry_metrics, 'load_evaluation_parts', load)
    def measure(parts, **kwargs):
        assert parts is loaded_parts
        calls.append(('measure', kwargs))
        return dict(protocol_id=evaluation.geometry_metrics.CONNECTIVITY_PROTOCOL,
            status='PASS' if measurable else 'UNAVAILABLE', overall_pass=False if measurable else None,
            part_count=1, material_component_count=2 if measurable else None,
            graph_component_count=2 if measurable else None, internal_connected=False,
            asset_sha256=('b' if binding_conflict else 'a')*64,
            measurement_config_sha256='c'*64, evidence_ref=None,
            reason_codes=['GEOMETRIC_GRAPH_DISCONNECTED' if measurable else 'GLOBAL_MATERIAL_UNAVAILABLE'],
            details=dict(measured=measurable), measurement_config=dict(protocol='stub'))
    monkeypatch.setattr(evaluation.geometry_metrics, 'measure_connectivity', measure)
    return case_id, directory, calls, physics, before


@pytest.mark.parametrize('arm', ['official', 'ours'])
def test_final_evaluator_uses_same_connectivity_and_keeps_measured_failures(tmp_path, monkeypatch, arm):
    cid, folder, calls, physics, before = _stub_final_connectivity_flow(tmp_path, monkeypatch, arm)
    result = evaluation.evaluate(tmp_path, cid, arm)
    assert [c[0] for c in calls]==['load','measure']
    assert calls[1][1]==dict(delta_ratio=.005, delta_floor_mm=0.)
    assert result['connectivity']['status']=='PASS' and result['connectivity']['overall_pass'] is False
    assert result['dataset_eligibility']['status']=='ELIGIBLE'
    assert result['dataset_eligibility']['eligible'] is True
    assert result['status']=='FAIL' and result['topology']==physics['assembly_topology']
    assert result['standing']==physics['assembly_standing']
    assert result['standing_evaluation']['evaluation_outcome']=='PASS'
    assert result['physical_evaluation_record']['asset_sha256']==evaluation.sha256(folder/'selected.glb')
    assert result['connectivity_asset_sha256']=='a'*64
    assert result['admission_record']['asset_sha256']=='a'*64
    ref = result['connectivity']['evidence_ref']
    evidence = folder/'evaluation'/ref['path']
    assert ref['file_sha256']==evaluation.sha256(evidence)
    assert evaluation.read_json(evidence)['asset_sha256']==ref['asset_sha256']=='a'*64
    assert (folder/'job.json').read_bytes()==before


@pytest.mark.parametrize('root,verified,kept,expected', [
    (False,True,['SELF_WEIGHT_COMPONENT_TIPPING'],['COMPONENT_TIPPING']),
    (True,True,['SELF_WEIGHT_TIPPING'],['TIPPING']),
    (True,True,['SELF_WEIGHT_COMPONENT_TIPPING','SELF_WEIGHT_TIPPING'],['COMPONENT_TIPPING','TIPPING']),
    (False,False,['SELF_WEIGHT_COMPONENT_TIPPING'],['COMPONENT_TIPPING','OBSERVATION_UNAVAILABLE']),
])
def test_final_evaluator_keeps_filtered_standing_findings_and_component_observation(
        tmp_path,monkeypatch,root,verified,kept,expected):
    cid,_,_,physics,_=_stub_final_connectivity_flow(tmp_path,monkeypatch,'ours')
    standing=physics['assembly_standing']
    standing['status']='FAIL'
    standing['findings']=[{'rule_id':rule} for rule in kept]
    standing['metrics']['standing_observation'].update(status='FAIL',
        observed_tipping=root,peak_tilt_deg=35. if root else .001,
        component_tilt_verified=verified,observed_component_tipping=True,max_component_tilt_deg=90.)
    result=evaluation.evaluate(tmp_path,cid,'ours')
    classified=result['standing_evaluation']
    assert classified['protocol_id']=='material_component_standing_v1'
    assert classified['evaluation_outcome']=='FAIL' and classified['reason_codes']==expected
    assert classified['observation']==standing['metrics']['standing_observation']
    assert classified['observation']['observed_tipping'] is root
    assert result['standing']==standing


@pytest.mark.parametrize('stage', ['physics', 'printing'])
def test_final_connectivity_still_runs_after_other_measurement_error(tmp_path, monkeypatch, stage):
    cid, _, calls, _, _ = _stub_final_connectivity_flow(tmp_path, monkeypatch, 'ours', error_stage=stage)
    result = evaluation.evaluate(tmp_path, cid, 'ours')
    assert result['failure_stage']=='geometry_or_physics'
    assert [c[0] for c in calls]==['load','measure']
    assert result['dataset_eligibility']['eligible'] is True


@pytest.mark.parametrize('missing_manifest', [False, True])
def test_final_connectivity_unavailable_does_not_enter_dataset(tmp_path, monkeypatch, missing_manifest):
    cid, _, calls, _, _ = _stub_final_connectivity_flow(tmp_path, monkeypatch, 'ours',
        error_stage='manifest' if missing_manifest else None, measurable=False)
    result = evaluation.evaluate(tmp_path, cid, 'ours')
    assert result['connectivity']['status']=='UNAVAILABLE'
    assert result['connectivity']['overall_pass'] is None
    assert result['dataset_eligibility']['status']=='EXCLUDED'
    assert result['dataset_eligibility']['eligible'] is False
    assert len(calls)==(0 if missing_manifest else 2)


def test_final_connectivity_binding_conflict_is_not_admitted(tmp_path, monkeypatch):
    cid, _, _, _, _ = _stub_final_connectivity_flow(tmp_path, monkeypatch, 'ours', binding_conflict=True)
    with pytest.raises(ValueError, match='connectivity asset/protocol/print-part count mismatch'):
        evaluation.evaluate(tmp_path, cid, 'ours')


@pytest.mark.parametrize('changed,match', [
    ('manifest', 'final connectivity manifest hash mismatch'),
    ('source', 'final connectivity source hash mismatch'),
    ('stl', 'print mesh hash missing/mismatched'),
])
def test_final_connectivity_loading_hash_conflict_is_a_hard_error(tmp_path, monkeypatch, changed, match):
    cid, folder, calls, _, _ = _stub_final_connectivity_flow(tmp_path, monkeypatch, 'ours')
    original_check = evaluation.physical_checks
    def change_bound_file(manifest, source, *args):
        checked = original_check(manifest, source, *args)
        target = {'manifest':Path(manifest), 'source':Path(source),
                  'stl':Path(manifest).parent/'body.stl'}[changed]
        target.write_bytes(target.read_bytes()+b'\n')
        return checked
    monkeypatch.setattr(evaluation, 'physical_checks', change_bound_file)
    with pytest.raises(ValueError, match=match):
        evaluation.evaluate(tmp_path, cid, 'ours')
    assert calls==[]  # No softened loader result and no geometry query.
    saved = evaluation.read_json(folder/'evaluation/result.json')
    assert 'dataset_eligibility' not in saved


def test_final_connectivity_bound_but_invalid_geometry_remains_unavailable(tmp_path, monkeypatch):
    cid, _, _, _, _ = _stub_final_connectivity_flow(tmp_path, monkeypatch, 'ours')
    monkeypatch.setattr(evaluation.geometry_metrics, 'load_evaluation_parts', lambda *a:
        dict(status='UNAVAILABLE', reason_codes=['GEOMETRY_INPUT_UNAVAILABLE'],
             reason='invalid coordinate transform', asset_sha256=None, parts=[]))
    monkeypatch.setattr(evaluation.geometry_metrics, 'measure_connectivity',
        lambda *a, **k:pytest.fail('unavailable loaded geometry measured'))
    result = evaluation.evaluate(tmp_path, cid, 'ours')
    assert result['connectivity']['status']=='UNAVAILABLE'
    assert result['dataset_eligibility']['status']=='EXCLUDED'
    assert result['dataset_eligibility']['eligible'] is False
