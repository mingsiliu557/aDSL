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


def score(v,g,n):
    reference=dict(reference_sha256='same',reference_voxels=v,voxel_pitch_mm=1)
    rows=[dict(status='PASS',gap_voxels=g if i==0 else 0) for i in range(n)]
    return dict(status='PASS', metrics={'partition_objective':score_partition(rows,reference,part_count=n)})


def test_formula_and_negative_numerator():
    assert score(1000,200,3)['metrics']['partition_objective']['score']==pytest.approx(800/3**.3)
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
