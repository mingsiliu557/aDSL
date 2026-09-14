from pathlib import Path
import json

import numpy as np
import pytest
import trimesh

pytest.importorskip("manifold3d")
from experiments.overhang_feedback.exterior_manifold import merge
from experiments.support_requirement_critical_surfaces import analyze


def row(mesh):
    return {"vertices": mesh.vertices.tolist(), "faces": mesh.faces.tolist()}


def test_overlapping_boxes_union_without_repair(tmp_path):
    a = trimesh.creation.box([2, 2, 2])
    b = a.copy()
    b.apply_translation([1, 0, 0])
    result = merge([row(a), row(b)], tmp_path / "accepted.json")
    mesh = trimesh.Trimesh(result["vertices"], result["faces"], process=False)
    assert mesh.is_volume and np.all(mesh.area_faces > 0)
    assert mesh.volume == pytest.approx(12)
    assert np.allclose(mesh.bounds, [[-1, -1, -1], [2, 1, 1]])


def test_gap_preserved(tmp_path):
    a = trimesh.creation.box()
    b = a.copy()
    b.apply_translation([1.01, 0, 0])
    result = merge([row(a), row(b)], tmp_path / "accepted.json")
    mesh = trimesh.Trimesh(result["vertices"], result["faces"], process=False)
    assert len(mesh.split()) == 2
    assert mesh.volume == pytest.approx(2)


def test_open_operand_rejected_and_recorded(tmp_path):
    payload = row(trimesh.creation.box())
    payload["faces"].pop()
    diagnostic = tmp_path / "accepted.json"
    with pytest.raises(ValueError, match="rejected operand 0"):
        merge([payload], diagnostic)
    assert json.loads(diagnostic.read_text())[0]["status"] != "Error.NoError"


def test_default_detector_uses_manifold_subprocess(tmp_path):
    box = trimesh.creation.box()
    tagged = [analyze.TaggedMesh("box", box, 0, len(box.faces))]
    parts, version = analyze.detector_exterior(tagged, tmp_path)
    assert version == "3.5.2"
    assert sum(x.mesh.volume for x in parts) == pytest.approx(1)
    assert json.loads((tmp_path / "exterior.json").read_text())["backend"] == "manifold_union"


def test_frozen_backend_mismatch_is_not_compared(tmp_path, monkeypatch):
    box = trimesh.creation.box()
    tagged = [analyze.TaggedMesh("box", box, 0, 12)]
    monkeypatch.setattr(analyze, "load_print_meshes", lambda *a: (tagged, {"print_scale_factor_mm_per_source_unit": 1}))
    monkeypatch.setattr(analyze, "detector_exterior", lambda *a: (tagged, "3.5.2"))
    monkeypatch.setattr(analyze, "slicer_identity", lambda *a: "test")
    monkeypatch.setattr(analyze, "sha256_file", lambda *a: "test")
    result = analyze.analyze_case("x", tmp_path, tmp_path / "out", {
        "profile": {}, "cases": {"x": {"frozen_measurement": {"exterior_method": "blender_exact_union"}}}},
        Path("unused"), Path("unused"), 1)
    assert result["status"] == "MEASUREMENT_CONDITIONS_CHANGED"
    assert "overhang" not in result
