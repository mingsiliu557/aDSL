"""Current Mesh64 export completeness and retained historical regression."""
import importlib
import os
from pathlib import Path
import runpy

import numpy as np
import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL') != '1',
                                reason='explicit native Blender geometry validation')
g = importlib.import_module('adsl.core.export.export_glb')


@pytest.fixture(autouse=True)
def blender():
    bpy = pytest.importorskip('bpy')
    bpy.ops.wm.read_factory_settings(use_empty=True)
    yield bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)


def test_export_rejects_silently_omitted_mesh(tmp_path, monkeypatch):
    from adsl.core import Cube
    good = tmp_path/'good.glb'
    g.export_glb(Cube((1, 1, 1)), good)
    original = g._verify_glb_meshes
    def omitted(path, expected):
        original(path, {**expected, 'silently_omitted_trunk': 42})
    monkeypatch.setattr(g, '_verify_glb_meshes', omitted)
    with pytest.raises(ValueError, match='GLB_EXPORT_INCOMPLETE.*silently_omitted_trunk'):
        g.export_glb(Cube((1, 1, 1)), tmp_path/'bad.glb')


def test_original_elephant_trunk_local_precision_repair_and_manufacturing(tmp_path, blender):
    """Fixed original source survives actual GLB and independent ASCII STL."""
    import trimesh
    from adsl.core.assembly_topology import read_print_mesh, union_print_mesh
    from adsl.core.export.mesh64 import evaluate_shape
    from adsl.core.export.mesh_validity import mesh_metrics
    source = Path(__file__).parent/'fixtures/elephant_trunk.py'
    scene = runpy.run_path(str(source))['scene']
    result = evaluate_shape(scene)
    mesh = result.pieces[0].world_mesh()
    assert mesh_metrics(mesh.vertices, mesh.faces)['valid']
    path = tmp_path/'trunk.glb'
    g.export_glb(scene, path)
    assert path.is_file()
    saved = trimesh.load(path, force='scene', process=False)
    actual = trimesh.util.concatenate(tuple(saved.geometry.values()))
    assert mesh_metrics(actual.vertices, actual.faces)['valid']
    stl = tmp_path/'trunk_mesh64_diagnostic.stl'
    mesh.export(stl, file_type='stl_ascii')
    solid, components, _ = union_print_mesh(read_print_mesh(stl, np.eye(4)))
    assert len(components) == 1 and solid.volume() > 0
    # Manufacturing retains the original Mesh64 surface and has its own
    # independently verified display; repairs never become checker geometry.
    from adsl.core.export.export_assembly import evaluated
    canonical, manufactured, diagnostics = evaluated(scene, tmp_path/'assembly_input.glb', 1.)
    assert diagnostics['manufacturing_status'] == 'PASS'
    assert diagnostics['display_status'] == 'PASS'
    assert manufactured.volume() == pytest.approx(result.pieces[0].solid.volume(), abs=1e-12)
    assert mesh_metrics(canonical.vertices, canonical.faces)['valid']
