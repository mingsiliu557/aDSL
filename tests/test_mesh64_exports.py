"""Offline native exports: canonical solids, precision boundary and actual files."""
import importlib
import json
from pathlib import Path
import runpy
import time

import numpy as np
import pytest
import trimesh

bpy = pytest.importorskip('bpy')
g = importlib.import_module('adsl.core.export.export_glb')
a = importlib.import_module('adsl.core.export.export_assembly')
from adsl.core import (Asset, Cube, Cylinder, FixedAssembly, boolean_difference,
                       boolean_intersection, boolean_union, translate_shape)
from adsl.core.export.mesh64 import evaluate_shape
from adsl.core.export.mesh_validity import MeshEvaluationError, mesh_metrics


def saved_world(path):
    scene = trimesh.load(path, force='scene', process=False)
    z_up = np.array([[1.,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]])
    pieces = []
    for node in scene.graph.nodes_geometry:
        tf, key = scene.graph[node]
        m = scene.geometry[key].copy()
        m.apply_transform(z_up @ tf)
        pieces.append(m)
    return scene, trimesh.util.concatenate(pieces)


@pytest.mark.parametrize('translation', [(0.,0,0), (0.,0,146.5857544), (1e7,2e7,-3e7)])
def test_nested_boolean_files_keep_local_precision_and_world_placement(tmp_path, translation, monkeypatch):
    shape = boolean_intersection(boolean_difference(boolean_union(
        Cube((2,2,2), color=(1,0,0)), Cube((2,2,2), center=(1,0,0), color=(0,0,1))),
        Cube((1,4,4), center=(-.5,0,0))), Cube((4,1,4), center=(.5,0,0)))
    shape = translate_shape(shape, translation)
    path = tmp_path/'nested.glb'
    g.export_glb(shape, path)
    _, actual = saved_world(path)
    # Actual GLB transform JSON preserves large translation, independent of bpy matrices.
    assert actual.volume == pytest.approx(4., abs=1e-6)
    assert np.allclose(actual.bounds, np.array([[0,-.5,-1],[2,.5,1]])+translation, atol=1e-7, rtol=0)
    assert mesh_metrics(actual.vertices, actual.faces)['valid']


def test_evaluated_cavity_stl_glb_and_checker_agree(tmp_path, monkeypatch):
    from adsl.core.assembly_topology import union_print_mesh, read_print_mesh
    shape = boolean_difference(Cube((4,4,4), color=(.8,.1,.1)), Cube((2,2,2)))
    monkeypatch.setattr(g, 'export_glb', lambda *a,**k: pytest.fail('evaluated must not export before evaluation'))
    mesh, solid, row = a.evaluated(shape, tmp_path/'cavity.glb', 2.5, keep_materials=True)
    assert solid.volume() == pytest.approx(56*2.5**3)
    assert row['canonical_mesh']['shell_components'] == 2
    assert row['canonical_mesh']['material_components'] == 1
    assert row['internal_evaluation']['self_intersection'] == 'NOT_EVALUATED'
    _, glb = saved_world(tmp_path/'cavity.glb')
    assert glb.volume*2.5**3 == pytest.approx(solid.volume())
    stl_path = tmp_path/'cavity.stl'
    mesh.export(stl_path, file_type='stl_ascii')
    checker_solid, components, _ = union_print_mesh(read_print_mesh(stl_path, np.eye(4)))
    assert len(components) == 1 and checker_solid.volume() == pytest.approx(solid.volume())
    assert np.array_equal(mesh.face_attributes['material'], mesh.face_attributes['source_face_id'])


def test_independent_objects_materials_and_joint_hierarchy_survive_glb(tmp_path):
    from adsl.core import Joint
    scene = Asset('root')
    scene.attach_part('red', Cube((2,2,2), color=(1,0,0)))
    scene.attach_part('blue', Cube((2,2,2), center=(5,0,0), color=(0,0,1)))
    scene.attach_joint('slider', Cube((1,1,1), center=(0,0,5)),
                       joint_type='prismatic', axis=(1,0,0), initial=.25, limit=(-1,1))
    g.export_glb(scene, tmp_path/'hierarchy.glb')
    loaded, mesh = saved_world(tmp_path/'hierarchy.glb')
    assert len(loaded.geometry) == 3
    assert mesh.volume == pytest.approx(17)
    vertices, inverse = np.unique(mesh.vertices, axis=0, return_inverse=True)
    welded = trimesh.Trimesh(vertices, inverse[mesh.faces], process=False)
    assert len(welded.split(only_watertight=False)) == 3
    import struct
    data=(tmp_path/'hierarchy.glb').read_bytes()
    doc=json.loads(data[20:20+struct.unpack_from('<I', data,12)[0]])
    node=next(n for n in doc['nodes'] if n['name']=='root/slider')
    assert node['extras']['adsl_attach_mode']=='joint'
    assert node['extras']['adsl_joint_type']=='prismatic'
    colors=[np.asarray(m.visual.material.baseColorFactor if m.visual.material.baseColorFactor is not None else [255,255,255,255])[:3] for m in loaded.geometry.values()]
    assert any(np.allclose(c,[255,0,0]) or np.allclose(c,[1,0,0]) for c in colors)
    assert any(np.allclose(c,[0,0,255]) or np.allclose(c,[0,0,1]) for c in colors)


def test_two_print_parts_are_not_unioned_and_output_files_verified(tmp_path):
    assembly=FixedAssembly(root_id='left', mm_per_unit=2.)
    # Two independent declarations may occupy the same location. Serialization
    # must preserve them and must not silently unite them.
    meshes={}
    for name,color in [('left',(1,0,0)),('right',(0,0,1))]:
        meshes[name], _, _ = a.evaluated(Cube((2,2,2),color=color),tmp_path/f'{name}.glb',2.,keep_materials=True)
    a._write_mesh_glb(meshes, {'left':np.eye(4),'right':np.eye(4)},tmp_path/'both.glb',2.)
    loaded,_=saved_world(tmp_path/'both.glb')
    assert {'left','right'}.issubset(loaded.graph.nodes)
    assert len(loaded.geometry)==2


def test_original_lamp_repairs_flipped_display_faces_and_preserves_manufacturing(tmp_path):
    from adsl.core.export.mesh_validity import validate_written_mesh
    fixture=Path(__file__).parents[1]/'reports/benchmark_six_boolean_failure_20261007/source.py'
    ns=runpy.run_path(str(fixture))
    arm=ns['CurvedArm'](ns['UpperStructure']().offset_lamp_head)
    result=evaluate_shape(arm)
    piece=result.pieces[0]
    original=piece.world_mesh()
    metrics=mesh_metrics(original.vertices,original.faces)
    assert metrics['valid'] and metrics['shell_components']==1
    mesh,solid,diagnostics=a.evaluated(arm,tmp_path/'arm.glb',1.,keep_materials=True)
    assert diagnostics['manufacturing_geometry_valid']
    assert diagnostics['manufacturing_status']==diagnostics['display_status']=='PASS'
    assert not diagnostics['display_failures']
    conversion=next(iter(diagnostics['target_precision'].values()))
    repair=conversion['local_precision_repair']
    assert repair['status']=='APPLIED' and repair['defect_counts_before']['flipped_faces']>0
    assert repair['bad_face_count_after']==0 and repair['operations']
    assert conversion['face_orientation']['valid']
    assert conversion['face_orientation']['flipped_faces']==conversion['face_orientation']['target_zero_normals']==0
    assert conversion['surface_displacement_upper_bound_mm']<=conversion['displacement_budget_mm']
    assert (tmp_path/'arm.glb').exists()
    _,display=saved_world(tmp_path/'arm.glb')
    assert mesh_metrics(display.vertices,display.faces)['valid']
    assert conversion['face_orientation']['triangle_count']==len(display.faces)
    assert display.volume==pytest.approx(solid.volume(),abs=conversion['volume_budget_mm3'],rel=0)
    # Display-only local repair cannot alter canonical manufacturing vertices.
    assert len(mesh.faces)==len(original.faces)
    np.testing.assert_allclose(np.unique(mesh.vertices,axis=0),np.unique(original.vertices,axis=0),
                               atol=1e-12,rtol=0)
    json.dumps(conversion,allow_nan=False)
    mesh.export(tmp_path/'arm.stl',file_type='stl_ascii')
    actual,restored,readback=validate_written_mesh(tmp_path/'arm.stl')
    assert readback['status']=='PASS'
    assert mesh_metrics(actual.vertices,actual.faces)['valid']
    assert restored.volume()==pytest.approx(solid.volume(),abs=1e-10)
    assert np.allclose(actual.bounds,piece.world_mesh().bounds,atol=1e-12,rtol=0)
