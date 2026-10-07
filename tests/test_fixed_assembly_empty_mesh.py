"""Reject empty evaluated objects without rerunning Blender or model APIs."""
import json
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import trimesh

from adsl.core.export import export_assembly as exporter


def _scene(monkeypatch, vertices, faces):
    """Supply authored mesh data to the canonical evaluator, never through bpy."""
    from adsl.core import Asset
    shape = Asset(label='evaluated_fixture')
    shape.add_primitive(dict(type='mesh', params={
        'vertices':np.asarray(vertices, dtype=np.float64).reshape(-1, 3),
        'triangles':np.asarray(faces, dtype=np.int64).reshape(-1, 3)},
        xform=np.eye(4), color=(1., 1., 1.), alpha=None))
    monkeypatch.setattr(exporter, 'export_glb', lambda *args, **kwargs:
                        pytest.fail('Canonical evaluation must not recover geometry from a GLB'))
    # This fixture isolates Mesh64 evaluation; native display/file boundaries
    # have their own tests and are deliberately stubbed here.
    monkeypatch.setattr(exporter, '_write_mesh_glb', lambda *args, **kwargs:
                        {'status':'PASS', 'method':'offline_display_stub'})
    return shape


@pytest.mark.parametrize('vertices,faces', [([], []), ([], [(0, 1, 2)]), ([(0., 0., 0.)], [])])
@pytest.mark.parametrize('keep_materials', [False, True])
def test_empty_object_rejected_before_welding_or_adjacency(tmp_path, monkeypatch,
                                                          vertices, faces, keep_materials):
    shape = _scene(monkeypatch, vertices, faces)
    unique = Mock(side_effect=AssertionError('empty mesh reached vertex welding'))
    adjacency = Mock(side_effect=AssertionError('empty mesh reached face adjacency'))
    monkeypatch.setattr(exporter.np, 'unique', unique)
    monkeypatch.setattr(trimesh.graph, 'face_adjacency', adjacency)
    with pytest.raises(ValueError) as error:
        exporter.evaluated(shape, tmp_path / 'unused.glb', 1., keep_materials=keep_materials)
    diagnostic = error.value.diagnostic
    assert diagnostic['code']==('INPUT_GEOMETRY_INVALID' if faces else 'EMPTY_REQUIRED_GEOMETRY')
    if faces:
        assert diagnostic['metrics']['invalid_indices']==3
    unique.assert_not_called()
    adjacency.assert_not_called()


def test_empty_object_uses_existing_part_geometry_failure(tmp_path, monkeypatch):
    shape = _scene(monkeypatch, [], [])
    assembly = SimpleNamespace(validate=lambda: None, mm_per_unit=1., root_id='part',
        parts={'part': shape}, components={'part': ('part',)},
        transforms={'part': np.eye(4)}, connections=[])
    report = exporter.export_assembly(assembly, tmp_path, source_sha256='fixture',
                                      expected={'mm_per_unit': 1.})
    assert report['status'] == 'FAIL'
    failure = report['failures'][0]
    assert failure['code']=='EMPTY_REQUIRED_GEOMETRY' and failure['part_id']=='part'
    assert failure['failure_kind']=='candidate_evaluation'
    assert failure['diagnostic']['stage']=='internal_evaluation'
    assert json.loads((tmp_path / 'assembly_manifest.json').read_text()) == report


def test_part_file_error_is_not_labelled_as_bad_geometry(tmp_path,monkeypatch):
    assembly = SimpleNamespace(validate=lambda:None,mm_per_unit=1.,root_id='part',
        parts={'part':None},components={'part':('part',)},transforms={'part':np.eye(4)},connections=[])
    def unavailable(*a,**kw):raise PermissionError('mock part.glb is not writable')
    monkeypatch.setattr(exporter,'evaluated',unavailable)
    report=exporter.export_assembly(assembly,tmp_path,source_sha256='fixture',
        expected={'mm_per_unit':1.,'fit_offset_mm':.2,'validation_mode':'visual_only'})
    assert report['export_status']=='FAIL'
    failure=report['failures'][0]
    assert failure['part_id']=='part' and failure['stage']=='evaluate_part'
    assert failure['code']=='PART_EXPORT_FAILED' and failure['failure_kind']=='export'
    assert json.loads((tmp_path/'assembly_manifest.json').read_text())==report


@pytest.mark.parametrize('keep_materials', [False, True])
def test_closed_tetrahedron_keeps_existing_evaluation_path(tmp_path, monkeypatch, keep_materials):
    vertices = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (0., 0., 1.)]
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    shape = _scene(monkeypatch, vertices, faces)
    mesh, solid, diagnostics = exporter.evaluated(shape, tmp_path / 'unused.glb', 2.,
                                                 keep_materials=keep_materials)
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(8. / 6.)
    assert solid.volume() == pytest.approx(mesh.volume)
    assert np.allclose(mesh.bounds, [[0., 0., 0.], [2., 2., 2.]])
    assert diagnostics['input_shells']==1
    assert diagnostics['exact_duplicate_vertices_merged']==0 and not diagnostics['proximity_welding']
    assert diagnostics['internal_evaluation']['status']=='PASS'
    assert diagnostics['internal_evaluation']['precision']=='float64'
    assert diagnostics['target_precision']['status']=='PASS'
    assert diagnostics['canonical_mesh']['valid']
    assert diagnostics['canonical_mesh']['self_intersection']=='NOT_EVALUATED'
    if keep_materials:
        assert mesh.metadata['materials'][0]['base_color']==[1.,1.,1.,1.]
        assert np.array_equal(mesh.face_attributes['material'], [0, 0, 0, 0])
