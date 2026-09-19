"""Small saved-mesh export regressions; no model API or source regeneration."""
from dataclasses import replace
import json

import numpy as np
import pytest
import trimesh

from adsl.core.export import export_assembly as exporter
from adsl.agents import fixed_assembly as flow
from test_fixed_assembly import mock_flow, run_flow


def _files(tmp_path):
    mesh = trimesh.creation.box(extents=(10., 12., 14.))
    mesh.metadata['materials'] = [
        dict(name='red_body', base_color=[.8, .1, .05, 1.], alpha=1.,
             metallic=.1, roughness=.35, blend_method='OPAQUE', use_backface_culling=False),
        dict(name='blue_detail', base_color=[.05, .2, .7, 1.], alpha=1.,
             metallic=.2, roughness=.65, blend_method='OPAQUE', use_backface_culling=True),
    ]
    mesh.face_attributes['material'] = np.arange(len(mesh.faces), dtype=int) % 2
    original_vertices = mesh.vertices.copy()
    original_faces = mesh.faces.copy()
    original_materials = mesh.face_attributes['material'].copy()
    mm_per_unit = 2.5
    transform = trimesh.transformations.rotation_matrix(np.pi / 6, [1, 2, 3])
    transform[:3, 3] = [2., 3., -4.]
    exploded = transform.copy()
    exploded[0, 3] += 20.
    print_transform = np.eye(4)
    print_transform[2, 3] = -mesh.bounds[0, 2]
    printed = mesh.copy()
    printed.apply_transform(print_transform)
    printed.export(tmp_path / 'part.stl')
    exporter._write_mesh_glb({'part': mesh}, {'part': np.eye(4)},
                             tmp_path / 'part.glb', mm_per_unit)
    exporter._write_mesh_glb({'part': mesh}, {'part': transform},
                             tmp_path / 'scene.glb', mm_per_unit)
    exporter._write_mesh_glb({'part': mesh}, {'part': exploded},
                             tmp_path / 'exploded.glb', mm_per_unit)
    length_tol = 16 * np.finfo(np.float32).eps * max(1., abs(mesh.vertices).max())
    manifest = dict(mm_per_unit=mm_per_unit, failures=[], numeric_tolerance=dict(
        length_mm=length_tol, volume_mm3=mesh.area * length_tol), parts=[dict(
            id='part', stl='part.stl', glb='part.glb',
            print_transform_mm=print_transform.tolist(),
            assembly_transform=transform.tolist(), exploded_transform=exploded.tolist())])
    assert np.array_equal(mesh.vertices, original_vertices)
    assert np.array_equal(mesh.faces, original_faces)
    assert np.array_equal(mesh.face_attributes['material'], original_materials)
    return mesh, manifest, {'part': exporter.mesh_solid(mesh)}


def test_saved_exports_preserve_geometry_units_transforms_and_materials(tmp_path):
    mesh, manifest, solids = _files(tmp_path)
    exporter._verify_written_exports(tmp_path, manifest, solids)
    assert not manifest['failures'], manifest
    assert manifest['export_consistency']
    for filename in ('part.glb', 'scene.glb', 'exploded.glb'):
        loaded = trimesh.load(tmp_path / filename, process=False, force='scene')
        materials = {geometry.visual.material.name: geometry.visual.material
                     for geometry in loaded.geometry.values()}
        assert set(materials) == {'red_body', 'blue_detail'}
        for expected in mesh.metadata['materials']:
            actual = materials[expected['name']]
            rgba = np.asarray(actual.baseColorFactor, dtype=float)
            if rgba.max() > 1:
                rgba /= 255.
            assert np.allclose(rgba, expected['base_color'], rtol=0, atol=1/255)
            assert actual.metallicFactor == pytest.approx(expected['metallic'])
            assert actual.roughnessFactor == pytest.approx(expected['roughness'])


@pytest.mark.parametrize('filename', ['scene.glb', 'exploded.glb', 'part.stl'])
def test_changed_final_file_is_rejected(tmp_path, filename):
    mesh, manifest, solids = _files(tmp_path)
    if filename.endswith('.stl'):
        changed = mesh.copy()
        changed.apply_scale([1.1, 1., 1.])
        changed.apply_transform(np.asarray(manifest['parts'][0]['print_transform_mm']))
        changed.export(tmp_path / filename)
    else:
        key = 'assembly_transform' if filename == 'scene.glb' else 'exploded_transform'
        transform = np.asarray(manifest['parts'][0][key]).copy()
        transform[0, 3] += .5
        exporter._write_mesh_glb({'part': mesh}, {'part': transform},
                                 tmp_path / filename, manifest['mm_per_unit'])
    exporter._verify_written_exports(tmp_path, manifest, solids)
    assert any(f['code'] in {'EXPORTED_FILE_GEOMETRY_MISMATCH', 'EXPORTED_FILE_INVALID'}
               for f in manifest['failures']), manifest


def test_export_mismatch_blocks_approval_even_when_image_critic_passes(tmp_path, monkeypatch):
    f = mock_flow(tmp_path, monkeypatch, [('PASS', True)])
    f = (f[0], replace(f[1], checker_specs=(), max_rounds=1), *f[2:])
    execute = flow.execute_asset_source
    def rejected_export(*args, **kwargs):
        result = execute(*args, **kwargs)
        path = result.output_root / 'assembly' / 'assembly_manifest.json'
        report = json.loads(path.read_text())
        report.update(status='FAIL', failures=[dict(code='EXPORTED_FILE_GEOMETRY_MISMATCH',
                                                   part_id='part', file='scene.glb')])
        path.write_text(json.dumps(report))
        return result
    monkeypatch.setattr(flow, 'execute_asset_source', rejected_export)
    result, book = run_flow(f)
    assert not result.approved and book['retained'] == 'original'
    assert f[-1] == []
    selected = book['versions'][book['retained']]
    assert selected['reviews']['appearance_approved']
    assert not selected['reviews']['accepted']
    assert selected['reviews']['geometry']['failures'][0]['code'] == 'EXPORTED_FILE_GEOMETRY_MISMATCH'
    assert (tmp_path / 'source.py').read_text() == (tmp_path / 'scene.glb').read_text() == 'original'
