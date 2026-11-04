from pathlib import Path
import json
import numpy as np
import pytest
import trimesh
from snap3d.common import normalize,scene_parts
from snap3d.export import export_parts,load_parts
from snap3d.rollout import relative_score,select_edges

def test_normalization_single_transform_and_scene_nodes():
    box=trimesh.creation.box([1,2,3]);mesh,T,D=normalize(box)
    assert max(mesh.extents)==pytest.approx(150)
    assert mesh.bounds[0,2]==pytest.approx(0)
    np.testing.assert_allclose(mesh.vertices,trimesh.transformations.transform_points(box.vertices,T))
    scene=trimesh.Scene();scene.add_geometry(box,node_name='one')
    shift=np.eye(4);shift[0,3]=10;scene.add_geometry(box,node_name='two',transform=shift)
    parts=scene_parts(scene)
    assert len(parts)==2
    assert abs(parts['part_000'].centroid[0]-parts['part_001'].centroid[0])==pytest.approx(10)

def test_saved_asset_conflict_rejected(tmp_path):
    export_parts({'part':trimesh.creation.box()},tmp_path)
    assert len(load_parts(tmp_path))==1
    with (tmp_path/'part.stl').open('ab') as f:f.write(b'changed')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):load_parts(tmp_path)

def test_moving_group_internal_edge_not_selected():
    cfg=dict(displacement_ratio=.05,rotation_deg=10)
    def frame(offset):
        return {i:dict(position_mm=[offset if i in ['a','b'] else 0,0,0],rotation=np.eye(3).tolist()) for i in ['a','b','fixed']}
    result=dict(status='PASS',fixed_ids=['fixed'],frames=[frame(0),frame(10)],last_contact_pairs=[['a','b']],
        metrics={'a':dict(max_displacement_mm=10,max_rotation_rad=0),'b':dict(max_displacement_mm=10,max_rotation_rad=0),'fixed':dict(max_displacement_mm=0,max_rotation_rad=0)})
    edges=[dict(id='internal',a='a',b='b'),dict(id='external',a='a',b='fixed')]
    assert select_edges(result,edges,D=100,config=cfg)==['external']
    assert relative_score(result,'a','b',D=100,config=cfg)==0

def test_weight_identity_rejects_arbitrary_directory(tmp_path):
    from snap3d.common import verify_snapshot
    with pytest.raises(FileNotFoundError):verify_snapshot(tmp_path,'Hunyuan3D-2')
