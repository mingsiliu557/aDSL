from pathlib import Path
import copy

import manifold3d as mf
import numpy as np
import pytest
import trimesh
import yaml

from snap3d.pge import pge
from snap3d.common import component_count,union
from snap3d.vendor import mesh_solid,solid_mesh,checked

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def box(size,center):
    m=trimesh.creation.box(size);m.apply_translation(center);return m


def cut_fixture(center=9.4):
    return {'a':box([10,2,2],[5,0,0]),'b':box([1,40,40],[center,0,0])}


def same_material(a,b):
    a,b=mesh_solid(a),mesh_solid(b)
    return checked(a-b).is_empty() and checked(b-a).is_empty()


def test_small_new_sliver_below_one_cell_is_deleted_and_cut_retained():
    source=cut_fixture();out,report=pge(source,config=CFG['pge'],D=256.)
    event=report['cuts'][0]
    assert event['trim']=='a' and event['retained'] and event['rollback_reasons']==[]
    assert report['cell_volume_mm3']==1.
    assert event['slivers_removed_count']==1
    assert event['slivers_removed_volume_mm3']==pytest.approx(.4)
    assert out['a'].volume==pytest.approx(35.6)
    assert out['a'].is_volume and component_count(out['a'])==1
    assert event['overlap_before_mm3']==pytest.approx(4.)
    assert event['overlap_after_mm3']==pytest.approx(0.)
    assert report['residual_overlap_volume_cells']==pytest.approx(0.)


def test_two_large_blocks_roll_back_exactly_and_later_pair_continues():
    source=cut_fixture(center=5.)
    source.update(c=box([2,2,2],[100,0,0]),d=box([2,2,2],[101.5,0,0]))
    out,report=pge(source,config=CFG['pge'],D=25.6)
    rejected,later=report['cuts']
    assert rejected['pair']==['a','b'] and not rejected['retained']
    assert rejected['rollback_reasons']==['COMPONENT_COUNT_CHANGED']
    assert rejected['candidate_component_count']==2 and rejected['component_count_after']==1
    assert rejected['slivers_removed_count']==0
    assert rejected['overlap_after_mm3']==rejected['overlap_before_mm3']==pytest.approx(4.)
    assert same_material(out['a'],source['a']) and same_material(out['b'],source['b'])
    assert later['pair']==['c','d'] and later['retained']
    assert report['rolled_back_cut_count']==1
    assert report['residual_overlap_volume_mm3']==pytest.approx(4.)
    assert all(c['overlap_after_mm3']<=c['overlap_before_mm3'] for c in report['cuts'])


def test_thin_fragment_above_one_volume_cell_is_still_removed():
    out,report=pge(cut_fixture(center=8.),config=CFG['pge'],D=256.)
    event=report['cuts'][0]
    assert event['retained'] and event['slivers_removed_volume_mm3']>1.
    assert event['slivers_removed_thicknesses_mm'][0]<1.
    assert component_count(out['a'])==1


def test_existing_tiny_material_is_not_misclassified_as_a_new_sliver():
    source=cut_fixture()
    tiny=box([.1,.1,.1],[100,0,0])
    source['a']=trimesh.util.concatenate([source['a'],tiny])
    out,report=pge(source,config=CFG['pge'],D=256.)
    assert report['cuts'][0]['retained']
    assert report['slivers_removed_volume_mm3']==pytest.approx(.4)
    assert component_count(out['a'])==2
    assert checked(mesh_solid(out['a'])^mesh_solid(tiny)).volume()==pytest.approx(tiny.volume)





def test_volume_cap_is_checked_after_all_cuts_then_min_margin_flip_replays():
    source={'a':box([10,10,10],[0,0,0]),'b':box([6,20,20],[-2,0,0])}
    out,report=pge(source,config=CFG['pge'],D=256.)
    assert len(report['history'])==2
    first,last=report['history']
    assert first['assignments']==['a'] and first['losses']['a']==pytest.approx(.6)
    assert first['cuts'][0]['retained'] and not first['volume_cap_satisfied']
    assert last['assignments']==['b'] and last['volume_cap_satisfied']
    assert last['losses']['b']==pytest.approx(.25) and last['losses']['a']==0.
    assert out['a'].volume==pytest.approx(1000.) and out['b'].volume==pytest.approx(1800.)


def test_pair_cut_is_deterministic_and_input_geometry_is_unchanged():
    source=cut_fixture();before={key:(m.vertices.copy(),m.faces.copy()) for key,m in source.items()}
    first,r1=pge(source,config=CFG['pge'],D=256.)
    second,r2=pge(source,config=CFG['pge'],D=256.)
    assert r1==r2
    for key,m in source.items():
        np.testing.assert_array_equal(m.vertices,before[key][0]);np.testing.assert_array_equal(m.faces,before[key][1])
        assert same_material(first[key],second[key])


def test_invalid_input_remains_an_explicit_error():
    opened=box([1,1,1],[0,0,0]);opened.update_faces(np.arange(len(opened.faces))!=0)
    with pytest.raises(ValueError,match='INPUT_NOT_CLOSED_VOLUME'):pge({'a':opened},config=CFG['pge'],D=256.)
