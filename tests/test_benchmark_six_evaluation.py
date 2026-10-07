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
