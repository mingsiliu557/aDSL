from pathlib import Path
import numpy as np
import pytest
import trimesh
import yaml
from snap3d.contact import contact_graph,material_distance
from snap3d.connector import rebuild
from snap3d.rollout import rollout,relative_score,select_edges
from snap3d.vendor import mesh_solid,checked
CFG=yaml.safe_load(Path('configs/default.yaml').read_text())
CFG['rollout']['collision_backend']='zone_proxy'

def box(extents,center):
    m=trimesh.creation.box(extents);m.apply_translation(center);return m

def test_contact_anchor_and_rebuild_two_connectors():
    base={'receiver':box([30,12,10],[0,0,5]),'left':box([5,5,4],[-6,0,-2]),'right':box([5,5,4],[6,0,-2])}
    edges=[]
    for name,x in [('left',-6),('right',6)]:
        edges.append(dict(id=name,a=name,b='receiver',donor=name,receiver='receiver',anchor=[x,0,0],inward=[0,0,1]))
    original={'left':[0,0,0,0,.6,1.5],'right':[0,0,0,0,.4,1.5]}
    before,_=rebuild(base,edges,original,D=50,config=CFG['connector'])
    current=dict(original,left=[0,0,0,0,.3,1.5])
    after,_=rebuild(base,edges,current,D=50,config=CFG['connector'])
    fresh,_=rebuild(base,edges,current,D=50,config=CFG['connector'])
    assert checked(mesh_solid(after['receiver'])-mesh_solid(fresh['receiver'])).is_empty()
    assert checked(mesh_solid(after['receiver'])-mesh_solid(before['receiver'])).volume()>0 # old enlarged slot refilled
    np.testing.assert_array_equal(after['right'].vertices,before['right'].vertices)
    np.testing.assert_array_equal(base['receiver'].vertices,box([30,12,10],[0,0,5]).vertices)

def test_fcl_true_distance():
    a=box([10,10,10],[0,0,0]);b=box([10,10,10],[11,0,0])
    assert material_distance(a,b)[0]==pytest.approx(1)

def test_contact_area_and_anchor():
    parts={'a':box([10,10,10],[0,0,0]),'b':box([10,10,10],[0,0,10])}
    edges,theta,report=contact_graph(parts,D=25,config=CFG['contact'],connector_config=CFG['connector'])
    assert len(edges)==1 and abs(edges[0]['anchor'][2]-5)<1e-5
    assert np.linalg.norm(np.array(edges[0]['anchor'])[:2])<.2
    assert report[0]['area']['edge']

def test_real_mujoco_ground_fixed_free_falls():
    parts={'grounded':box([10,10,10],[0,0,5]),'free':box([2,2,2],[20,0,12])}
    result=rollout(parts,D=30,config=dict(CFG['rollout'],duration=.03),coacd_config=CFG['coacd'],clearance_mm=.03,
                   proxy_cache={i:[m] for i,m in parts.items()})
    assert result['status']=='PASS'
    assert result['fixed_ids']==['grounded']
    np.testing.assert_allclose(result['frames'][0]['free']['position_mm'],[20,0,12])
    assert result['metrics']['grounded']['max_displacement_mm']==0
    assert result['frames'][-1]['free']['position_mm'][2]<12
    assert 'last_contact_pairs' in result

def test_missing_ground_and_no_free():
    part=box([2,2,2],[0,0,1])
    kwargs=dict(D=4,config=CFG['rollout'],coacd_config=CFG['coacd'],clearance_mm=.01)
    assert rollout({'x':part},fixed_ids=[],**kwargs)['reason']=='NO_GROUND_PARTS'
    assert rollout({'x':part},**kwargs)['reason']=='NO_FREE_PARTS'
