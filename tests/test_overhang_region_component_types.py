"""Real overhang regions accept both connected-component index containers."""
import numpy as np
import pytest
import trimesh

from adsl.agents.assembly_overhang import measure_part


@pytest.mark.parametrize('component_type', [list, np.ndarray], ids=['python_list', 'numpy_array'])
def test_nonzero_overhang_regions_accept_component_index_types(monkeypatch, component_type):
    mesh = trimesh.creation.box([2, 3, 4])
    mesh.apply_translation([0, 0, 3])
    connected_components = trimesh.graph.connected_components
    observed = []

    def component_containers(*args, **kwargs):
        components = connected_components(*args, **kwargs)
        converted = [np.asarray(ids, dtype=int) for ids in components]
        if component_type is list:
            converted = [ids.tolist() for ids in converted]
        observed.extend(converted)
        return converted

    monkeypatch.setattr(trimesh.graph, 'connected_components', component_containers)
    measured, _ = measure_part(mesh, np.eye(4), {
        'overhang_threshold_from_horizontal_deg': 45.0, 'layer_height_mm': 0.2})

    assert len(observed) == 1 and isinstance(observed[0], component_type)
    assert measured['area_mm2'] == pytest.approx(6.0)
    assert len(measured['regions']) == 1
    region = measured['regions'][0]
    assert region['area_mm2'] == pytest.approx(6.0)
    expected_ids = np.flatnonzero(mesh.face_normals[:, 2] < -0.5).tolist()
    assert sorted(region['global_face_ids']) == expected_ids
    assert all(type(face_id) is int for face_id in region['global_face_ids'])
    assert region['centroid_mm'] == pytest.approx([0.0, 0.0, 1.0])
    assert all(type(value) is float for value in region['centroid_mm'])
    np.testing.assert_allclose(region['bounds_mm'], [[-1.0, -1.5, 1.0], [1.0, 1.5, 1.0]])
    assert all(type(value) is float for bound in region['bounds_mm'] for value in bound)
