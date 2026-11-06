from pathlib import Path
import copy
import numpy as np
import pytest
import trimesh
import yaml
from snap3d.vendor import mesh_solid,solid_mesh,checked
from snap3d.common import component_count
from snap3d.pge import pge
from snap3d.repair import repair
from snap3d.connector import shapes

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())
def box(extents=(10,10,10),center=(0,0,0)):
    m=trimesh.creation.box(extents);m.apply_translation(center);return m

def test_vendor_roundtrip_and_cavity():
    outer=mesh_solid(box());inner=mesh_solid(box((5,5,5)))
    mesh=solid_mesh(checked(outer-inner))
    assert mesh.is_volume and mesh.volume==pytest.approx(875)
    assert component_count(mesh)==1

def test_pge_cumulative_actual_loss():
    parts={'a':box(),'b':box(center=(8,0,0))}
    out,report=pge(parts,config=CFG['pge'])
    assert checked(mesh_solid(out['a'])^mesh_solid(out['b'])).volume()==pytest.approx(0)
    assert all(m.is_volume and component_count(m)==1 for m in out.values())
    assert sum(report['volumes_after'].values())==pytest.approx(1800)
    assert all(v<=.35 for v in report['history'][-1]['losses'].values())

def test_pge_records_exhausted_volume_cap_and_continues():
    out,report=pge({'a':box(),'b':box(center=(1,0,0))},config=CFG['pge'])
    assert set(out)=={'a','b'}
    assert not report['volume_cap_satisfied']
    assert 'PGE_VOLUME_LOSS_CAP_UNSATISFIED' in report['warnings']

def test_small_hole_and_whole_missing_face():
    # Icosahedron tiny facet removed: closed global body, local triangle hole.
    mesh=trimesh.creation.icosphere(subdivisions=3,radius=1)
    opened=mesh.copy();opened.update_faces(np.arange(len(mesh.faces))!=0)
    fixed,record=repair(opened,D=100.,config=CFG['repair'])
    assert fixed.is_volume and record['repaired'] and record['original_volume_mm3'] is None
    large=box();large.update_faces(np.arange(12)>1)
    closed,record=repair(large,D=20.,config=CFG['repair'])
    assert closed.is_watertight and record['repaired']

def test_closed_separated_parts_not_joined():
    original=box();out,report=repair(original,D=20.,config=CFG['repair'])
    assert np.array_equal(out.vertices,original.vertices) and not report['repaired']

def test_head_wider_than_throat_and_no_overlap():
    import manifold3d as mf
    peg,slot=shapes(radius=.9,length=3,D=150)
    receiver=mf.Manifold.cube([10,10,10]).translate([-5,-5,0])-slot
    assert checked(peg^receiver).volume()==pytest.approx(0)
    # Withdrawing the head 1 mm must collide with the remaining throat material.
    assert checked(peg.translate([0,0,-1])^receiver).volume()>0
    plain,_=shapes(radius=.9,length=3,D=150,headed=False)
    assert checked(plain.translate([0,0,-1])^receiver).volume()==pytest.approx(0)
