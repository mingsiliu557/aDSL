import numpy as np
import pytest
import manifold3d as mf
from adsl.core.assembly_topology import solid_mesh
from adsl.agents.partition_score import (OBJECTIVE, objective_config, occupied_voxels,
    vertical_gap_count, rotations24, best_print_pose, score_partition, compare_partition_scores)


def vox(solid, h=1):
    return occupied_voxels(solid_mesh(solid), h, [0,0,0])


def test_solid_box_interior_grid_planes_and_cavity():
    box = mf.Manifold.cube((3,3,3))
    assert len(vox(box)) == 27  # Interior cell included, face contact adds no layer.
    hollow = box-mf.Manifold.cube((1,1,1)).translate((1,1,1))
    cells=vox(hollow)
    assert len(cells)==26 and (1,1,1) not in set(map(tuple,cells))
    assert vertical_gap_count(cells)==1
    assert vertical_gap_count(vox(box))==0


@pytest.mark.parametrize('height', [1,2,4])
def test_bridge_gap_is_empty_cells_below_material(height):
    legs = mf.Manifold.cube((1,1,height)) + mf.Manifold.cube((1,1,height)).translate((3,0,0))
    beam = mf.Manifold.cube((4,1,1)).translate((0,0,height))
    assert vertical_gap_count(vox(legs+beam))==2*height


def test_crossing_without_cell_center_and_bed_pose():
    thin = mf.Manifold.cube((2,.1,.1)).rotate((0,0,45)).translate((1,0,0))
    cells=vox(thin)
    assert len(cells)>0  # No cell center is within its 0.1 mm height.
    rotations=rotations24()
    assert len({tuple(r.ravel()) for r in rotations})==24
    assert all(round(np.linalg.det(r))==1 for r in rotations)
    mesh=solid_mesh(mf.Manifold.cube((2,3,4)).translate((5,-8,9)))
    row=best_print_pose(mesh,dict(voxel_pitch_mm=1))
    assert row['gap_voxels']==0 and row['selected_rotation_id']==0 and len(row['orientations'])==24
    for pose in row['orientations']:
        placed=mesh.copy();placed.apply_transform(pose['print_transform_mm'])
        np.testing.assert_allclose(placed.bounds[0],0,atol=0)
    assert row==best_print_pose(mesh,dict(voxel_pitch_mm=1))


def score(v,g,n,alpha=None):
    reference=dict(reference_sha256='same',reference_voxels=v,voxel_pitch_mm=1)
    rows=[dict(status='PASS',gap_voxels=g if i==0 else 0) for i in range(n)]
    return dict(status='PASS', metrics={'partition_objective':score_partition(rows,reference,part_count=n,config={**OBJECTIVE,'alpha':OBJECTIVE['alpha'] if alpha is None else alpha})})


def test_formula_and_negative_numerator():
    assert score(1000,200,3)['metrics']['partition_objective']['score']==pytest.approx(800/3**.2)
    assert compare_partition_scores(score(100,0,3),score(100,0,2))['conclusion']=='IMPROVED'
    assert compare_partition_scores(score(100,150,2),score(100,150,4))['conclusion']=='IMPROVED'
    assert score(100,150,2)['metrics']['partition_objective']['numerator_nonpositive']
    assert compare_partition_scores(score(100,0,2),score(100,0,2))['conclusion']=='UNCHANGED'


def test_missing_parts_and_incomparable_references():
    reference=dict(reference_sha256='same',reference_voxels=100,voxel_pitch_mm=1)
    row=score_partition([dict(status='PASS',gap_voxels=1)],reference,part_count=2)
    assert row['score'] is None and row['gap_voxels'] is None and row['print_part_count']==2
    for key,value in [('reference_sha256','other'),('voxel_pitch_mm',2),('evaluation_config_sha256','other')]:
        other=score(100,0,2);other['metrics']['partition_objective'][key]=value
        assert compare_partition_scores(score(100,0,2),other)['conclusion']=='NOT_COMPARABLE'
    with pytest.raises(ValueError):objective_config({**OBJECTIVE,'alpha':.4})


def reference_fixture(tmp_path, *, scale=2., split=False):
    import json
    import hashlib
    mesh=solid_mesh(mf.Manifold.cube((4,3,2)))
    rows=[];declarations=[]
    solids=[mf.Manifold.cube((2,3,2)),mf.Manifold.cube((2,3,2)).translate((2,0,0))] if split else [mf.Manifold.cube((4,3,2))]
    for i,solid in enumerate(solids):
        body=solid_mesh(solid);path=tmp_path/f'part{i}.body.npz'
        np.savez(path,vertices=body.vertices,faces=body.faces)
        rows.append(dict(part_id=f'part{i}',status='PASS',frame='part_local_mm',npz=path.name,
            sha256=hashlib.sha256(path.read_bytes()).hexdigest(),source_sha256='source'))
        transform=np.eye(4);transform[:3,3]=[5,2,1]
        declarations.append(dict(id=f'part{i}',components=[['left','right'][i]] if split else ['left','right'],assembly_transform=transform.tolist()))
    path=tmp_path/'manifest.json'
    path.write_text(json.dumps(dict(source_sha256='source',mm_per_unit=scale,root_id='part0',
        part_declarations=declarations,partition_reference_inputs=rows)))
    return path


def test_reference_units_immutability_and_regrouped_shape(tmp_path):
    from adsl.agents.partition_score import create_reference,load_reference,reference_shape_comparison
    manifest=reference_fixture(tmp_path)
    path=create_reference(manifest,tmp_path/'references')
    ref=load_reference(path)
    assert ref['voxel_pitch_mm']==pytest.approx(.2)
    np.testing.assert_allclose(ref['bounds_mm'],[[10,4,2],[14,7,4]])
    assert ref['reference_voxels']==20*15*10
    reference_fixture(tmp_path,split=True)
    comparison=reference_shape_comparison(manifest,path)
    assert comparison['status']=='MATCH'
    assert comparison['symmetric_difference_mm3']==0
    (path.parent/'occupied.npz').write_bytes(b'changed')
    with pytest.raises(ValueError,match='changed'):load_reference(path)


def test_partition_guidance_bounds_and_original_negative_formula():
    from adsl.agents.partition_score import partition_score_guidance
    def guide(v,g,n):return partition_score_guidance(score(v,g,n,.3)['metrics']['partition_objective'])
    a=guide(216,6,2)
    assert a['split_one_more']['zero_gap_score_upper_bound']==pytest.approx(155.35218815817)
    assert not a['split_one_more']['improvement_possible']
    b=guide(616,0,5)['merge_one_fewer']
    assert b['gap_voxels_exclusive_upper_bound']==pytest.approx(39.886956141265)
    assert b['allowed_gap_increase_exclusive']==b['gap_voxels_exclusive_upper_bound']
    assert compare_partition_scores(score(616,0,5,.3),score(616,39,4,.3))['conclusion']=='IMPROVED'
    assert compare_partition_scores(score(616,0,5,.3),score(616,40,4,.3))['conclusion']=='WORSE'
    assert 'merge_one_fewer' not in guide(100,0,1)
    assert partition_score_guidance({})['status']=='UNAVAILABLE'
    missing=score(100,0,2)['metrics']['partition_objective'];missing['gap_voxels']=None
    assert partition_score_guidance(missing)['status']=='UNAVAILABLE'
    neg=guide(100,150,2)
    assert neg['numerator_nonpositive'] and neg['current']['score'] < 0
    assert neg['merge_one_fewer']['gap_voxels_exclusive_upper_bound'] > 100


@pytest.mark.parametrize('alpha',[.1,.2,.3])
def test_supported_alpha_and_strict_split_threshold(alpha):
    from adsl.agents.partition_score import partition_score_guidance
    current=score(2056,476,1,alpha)
    objective=current['metrics']['partition_objective']
    guidance=partition_score_guidance(objective)['split_one_more']
    bound=2056-1580*2**alpha
    assert guidance['gap_voxels_exclusive_upper_bound']==pytest.approx(bound)
    assert guidance['required_gap_reduction_exclusive']==pytest.approx(476-bound)
    assert compare_partition_scores(current,score(2056,int(np.floor(bound)),2,alpha))['conclusion']=='IMPROVED'
    assert compare_partition_scores(current,score(2056,int(np.ceil(bound)),2,alpha))['conclusion']=='WORSE'
    assert objective_config({**OBJECTIVE,'alpha':alpha})['alpha']==alpha
    assert compare_partition_scores(current,score(2056,0,2,.3 if alpha==.2 else .2))['conclusion']=='NOT_COMPARABLE'


@pytest.mark.parametrize('alpha',[.1,.3])
def test_new_default_and_legacy_reference_files(tmp_path,monkeypatch,alpha):
    from types import SimpleNamespace
    from adsl.agents import partition_score as module
    assert OBJECTIVE['alpha']==.2
    mesh=SimpleNamespace(vertices=np.array([[0.,0,0],[10,20,30]]),faces=np.array([[0,1,1]]),
                         bounds=np.array([[0.,0,0],[10,20,30]]),extents=np.array([10,20,30]))
    manifest=dict(source_sha256='source',partition_reference_inputs=[],root_id='body',
                  part_declarations=[dict(id='body',assembly_transform=np.eye(4).tolist())])
    pitches=[]
    monkeypatch.setattr(module,'body_union',lambda *a:(mesh,manifest))
    def occupied(mesh,h,origin):pitches.append(h);return np.array([[0,0,0]])
    monkeypatch.setattr(module,'occupied_voxels',occupied)
    legacy={**OBJECTIVE,'alpha':alpha}
    path=module.create_reference('unused',tmp_path,legacy)
    assert pitches==[1.]
    assert module.load_reference(path,legacy)['reference_voxels']==1
    with pytest.raises(ValueError,match='configuration changed'):module.load_reference(path)
    def checked_config(path,config):
        assert config==legacy
        raise RuntimeError('validated frozen config passed')
    monkeypatch.setattr(module,'load_reference',checked_config)
    with pytest.raises(RuntimeError,match='frozen config passed'):
        module.reference_shape_comparison('unused',path,config=legacy)
