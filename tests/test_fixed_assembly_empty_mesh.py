"""Reject empty evaluated objects without rerunning Blender or model APIs."""
import json
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import numpy as np
import pytest
import trimesh

from adsl.core.export import export_assembly as exporter


def _scene(monkeypatch, vertices, faces):
    bsdf = SimpleNamespace(inputs={key: SimpleNamespace(default_value=value)
        for key, value in [('Base Color', [1., 1., 1., 1.]), ('Alpha', 1.),
                           ('Metallic', 0.), ('Roughness', .5)]})
    material = SimpleNamespace(name='white', use_nodes=True,
        node_tree=SimpleNamespace(nodes={'Principled BSDF': bsdf}),
        blend_method='OPAQUE', use_backface_culling=False)
    data = SimpleNamespace(calc_loop_triangles=lambda: None,
        vertices=[SimpleNamespace(co=np.asarray(vertex)) for vertex in vertices],
        loop_triangles=[SimpleNamespace(vertices=face, polygon_index=0) for face in faces],
        materials=[material], polygons=[SimpleNamespace(material_index=0)])
    obj = SimpleNamespace(name='evaluated_fixture', type='MESH', data=data, matrix_world=np.eye(3))
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(
        context=SimpleNamespace(scene=SimpleNamespace(objects=[obj]))))
    monkeypatch.setattr(exporter, 'export_glb', lambda *args: None)


@pytest.mark.parametrize('vertices,faces', [([], []), ([], [(0, 1, 2)]), ([(0., 0., 0.)], [])])
@pytest.mark.parametrize('keep_materials', [False, True])
def test_empty_object_rejected_before_welding_or_adjacency(tmp_path, monkeypatch,
                                                          vertices, faces, keep_materials):
    _scene(monkeypatch, vertices, faces)
    unique = Mock(side_effect=AssertionError('empty mesh reached vertex welding'))
    adjacency = Mock(side_effect=AssertionError('empty mesh reached face adjacency'))
    monkeypatch.setattr(exporter.np, 'unique', unique)
    monkeypatch.setattr(trimesh.graph, 'face_adjacency', adjacency)
    with pytest.raises(ValueError) as error:
        exporter.evaluated(None, tmp_path / 'unused.glb', 1., keep_materials=keep_materials)
    assert str(error.value) == ("empty evaluated mesh 'evaluated_fixture': "
        f"vertices={len(vertices)}, loop_triangles={len(faces)}")
    unique.assert_not_called()
    adjacency.assert_not_called()


def test_empty_object_uses_existing_part_geometry_failure(tmp_path, monkeypatch):
    _scene(monkeypatch, [], [])
    assembly = SimpleNamespace(validate=lambda: None, mm_per_unit=1., root_id='part',
        parts={'part': None})
    report = exporter.export_assembly(assembly, tmp_path, source_sha256='fixture',
                                      expected={'mm_per_unit': 1.})
    assert report['status'] == 'FAIL'
    assert report['failures'] == [dict(code='PART_GEOMETRY_INVALID', part_id='part',
        reason="empty evaluated mesh 'evaluated_fixture': vertices=0, loop_triangles=0")]
    assert json.loads((tmp_path / 'assembly_manifest.json').read_text()) == report


@pytest.mark.parametrize('keep_materials', [False, True])
def test_closed_tetrahedron_keeps_existing_evaluation_path(tmp_path, monkeypatch, keep_materials):
    vertices = [(0., 0., 0.), (1., 0., 0.), (0., 1., 0.), (0., 0., 1.)]
    faces = [(0, 2, 1), (0, 1, 3), (0, 3, 2), (1, 2, 3)]
    _scene(monkeypatch, vertices, faces)
    mesh, solid, diagnostics = exporter.evaluated(None, tmp_path / 'unused.glb', 2.,
                                                 keep_materials=keep_materials)
    assert mesh.is_volume
    assert mesh.volume == pytest.approx(8. / 6.)
    assert solid.volume() == pytest.approx(mesh.volume)
    assert np.allclose(mesh.bounds, [[0., 0., 0.], [2., 2., 2.]])
    assert diagnostics == dict(input_shells=1, exact_duplicate_vertices_merged=0,
                               proximity_welding=False)
    if keep_materials:
        assert mesh.metadata['materials'][0]['name'] == 'white'
        assert np.array_equal(mesh.face_attributes['material'], [0, 0, 0, 0])
