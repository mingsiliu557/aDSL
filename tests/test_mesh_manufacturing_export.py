"""Manufacturing Mesh64/STL remain usable when a display cannot be represented."""
import hashlib
import importlib
import runpy
from pathlib import Path

import numpy as np
import pytest
import trimesh

pytest.importorskip('bpy')
a = importlib.import_module('adsl.core.export.export_assembly')
from adsl.core import Cube
from adsl.core.export.mesh_validity import MeshEvaluationError, mesh_metrics
from adsl.core.assembly_topology import (read_print_mesh, part_measurement,
                                         interface_measurement)


def unavailable_display(*args, **kwargs):
    raise MeshEvaluationError('TARGET_PRECISION_UNREPRESENTABLE', 'forced display conversion failure',
        stage='target_precision', failure_kind='target_precision', attempts=[])


def checker_measurements(output, report):
    parts = {p['id']:p for p in report['parts']}
    solids, rows = {}, []
    for name, part in parts.items():
        mesh = read_print_mesh(output/part['stl'], part['print_transform_mm'])
        assert mesh_metrics(mesh.vertices, mesh.faces)['valid']
        row, solids[name] = part_measurement(mesh, name, part['mesh_face_groups'])
        rows.append(row)
    interfaces = [interface_measurement(c, parts, solids, report['mm_per_unit'])
                  for c in report['connections']]
    return rows, interfaces


def test_evaluated_preserves_mesh64_when_display_conversion_fails(tmp_path, monkeypatch):
    monkeypatch.setattr(a, '_write_mesh_glb', unavailable_display)
    mesh, solid, evidence = a.evaluated(Cube(2), tmp_path/'part.glb', 2.5, keep_materials=True)
    assert mesh.vertices.dtype == np.float64
    assert solid.volume() == pytest.approx(125.)
    assert evidence['manufacturing_geometry_valid']
    assert evidence['display_status'] == 'FAIL' and not evidence['display_complete']
    assert evidence['display_failures'][0]['stage'] == 'target_precision'
    assert evidence['display_failures'][0]['output_role'] == 'display'
    assert not (tmp_path/'part.glb').exists()


@pytest.mark.parametrize('mode', ['geometry', 'visual_only'])
def test_all_print_parts_and_interfaces_survive_missing_display(tmp_path, monkeypatch, mode):
    from test_fixed_assembly_multi_mate import shelves, CONFIG
    monkeypatch.setattr(a, '_write_mesh_glb', unavailable_display)
    output = tmp_path/'assembly'
    report = a.export_assembly(shelves(), output, source_sha256='offline_fixture',
                              expected={**CONFIG, 'validation_mode':mode})
    assert report['status'] == ('PASS' if mode=='geometry' else 'NOT_EVALUATED'), report['failures']
    assert report['manufacturing_status'] == 'PASS'
    assert report['manufacturing_geometry_valid']
    assert report['display_status'] == report['export_status'] == 'FAIL'
    assert report['scene_glb'] is None and report['exploded_glb'] is None
    assert all(p['glb'] is None and p['display_status']=='FAIL' for p in report['parts'])
    assert len(report['parts']) == 4 and len(report['connections']) == 4
    assert {p['stl'] for p in report['parts']}.issubset(report['files_sha256'])
    assert not report['failures']
    assert all(r['output_role']=='display' for r in report['display_failures'])
    assert report['canonical_scene_consistency']['source'] == 'manufacturing_geometry'
    rows, interfaces = checker_measurements(output, report)
    assert len(rows)==len(interfaces)==4
    assert all(r['status']=='PASS' for r in rows+interfaces)


def test_normal_display_and_manufacturing_files_both_pass(tmp_path):
    from test_fixed_assembly_multi_mate import pair
    report = a.export_assembly(pair(), tmp_path, source_sha256='offline_fixture',
        expected=dict(mm_per_unit=1., fit_offset_mm=.2, final_size_mm=[60.,20.,62.]))
    assert report['status']==report['manufacturing_status']==report['display_status']==report['export_status']=='PASS', report['failures']
    assert report['scene_glb']=='scene.glb' and report['exploded_glb']=='exploded.glb'
    assert not report['display_failures']
    assert all((tmp_path/p['glb']).is_file() and (tmp_path/p['stl']).is_file() for p in report['parts'])
    assert {p['stl'] for p in report['parts']}.issubset(report['files_sha256'])
    rows, interfaces = checker_measurements(tmp_path, report)
    assert all(r['status']=='PASS' for r in rows+interfaces)


@pytest.mark.parametrize('fail', [False, True])
def test_display_preparation_is_reused_without_touching_canonical_mesh(tmp_path, monkeypatch, fail):
    validity = importlib.import_module('adsl.core.export.mesh_validity')
    mesh, _, _ = a.evaluated(Cube(2), tmp_path/'unused.glb', 1., _write_display=False)
    before_vertices, before_faces = mesh.vertices.copy(), mesh.faces.copy()
    original = validity.target_mesh
    calls = []
    def target(*args, **kwargs):
        calls.append(kwargs['node_path'])
        return unavailable_display() if fail else original(*args, **kwargs)
    monkeypatch.setattr(validity, 'target_mesh', target)
    cache = {}
    first = a._display_export({'part':mesh}, {'part':np.eye(4)}, tmp_path/'first.glb', 1., cache)
    transform = trimesh.transformations.rotation_matrix(.3, [0,0,1]); transform[:3,3]=[5,8,2]
    second = a._display_export({'part':mesh}, {'part':transform}, tmp_path/'second.glb', 1., cache)
    assert len(calls)==1
    assert first['status']==second['status']==('FAIL' if fail else 'PASS')
    np.testing.assert_array_equal(mesh.vertices, before_vertices)
    np.testing.assert_array_equal(mesh.faces, before_faces)
    if not fail:
        scene = trimesh.load(tmp_path/'second.glb', force='scene', process=False)
        z_up = np.array([[1,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]])
        assert np.allclose(z_up@scene.graph['part'][0]@np.linalg.inv(z_up), transform)


def test_original_lamp_manufacturing_and_declared_interface_are_measurable(tmp_path, monkeypatch):
    # Original source stays unchanged; a display-only fault cannot hide either
    # canonical print part or its independently measured actual interface.
    fixture = Path(__file__).parents[1]/'reports/benchmark_six_boolean_failure_20261007/source.py'
    original_sha = hashlib.sha256(fixture.read_bytes()).hexdigest()
    assembly = runpy.run_path(str(fixture))['assembly']
    monkeypatch.setattr(a, '_write_mesh_glb', unavailable_display)
    report = a.export_assembly(assembly, tmp_path, source_sha256=original_sha,
        expected=dict(validation_mode='visual_only', mm_per_unit=1., fit_offset_mm=.2,
                      final_size_mm=[26.693637297766752, 50.56927536447469, 150.]))
    assert hashlib.sha256(fixture.read_bytes()).hexdigest()==original_sha
    assert report['manufacturing_status']=='PASS', report['failures']
    assert report['status']=='NOT_EVALUATED' and report['display_status']=='FAIL'
    assert {p['id'] for p in report['parts']}=={'base_body','upper_structure'}
    assert [c['id'] for c in report['connections']]==['upright_to_base']
    rows, interfaces = checker_measurements(tmp_path, report)
    assert len(rows)==2 and len(interfaces)==1
    assert all(r['status']=='PASS' for r in rows+interfaces), rows+interfaces
    assert all(r['scope']=='mesh_validity' and r['status']=='PASS'
               for r in report['manufacturing_file_validation'])
