from pathlib import Path
import hashlib
import xml.etree.ElementTree as ET

import pytest
import trimesh
import yaml

from snap3d.connector import rebuild,ConnectorInfeasible
from snap3d.rollout import rollout,_rigid_flex_xml

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def box(size,center):
    mesh=trimesh.creation.box(size);mesh.apply_translation(center);return mesh


def test_sub_noise_third_part_and_assembly_intersections_are_ignored():
    base={'donor':box([4,4,4],[0,0,-2]),'receiver':box([10,10,10],[0,0,5]),
          'third':box([.01,.01,.01],[0,0,1])}
    e=dict(id='edge',a='donor',b='receiver',donor='donor',receiver='receiver',anchor=[0,0,0],inward=[0,0,1])
    params={'edge':[0,0,0,0,.9,3.]}
    meshes,records=rebuild(base,[e],params,D=150,config=CFG['connector'],strict_edge='edge')
    assert records[0]['status']=='PLACED'
    assert 'ASSEMBLY_INTERSECTION' not in {r['code'] for r in records[0]['warnings']}
    base['third']=box([.2,.2,.2],[0,0,1])
    with pytest.raises(ConnectorInfeasible,match='THIRD_PART_INTERFERENCE'):
        rebuild(base,[e],params,D=150,config=CFG['connector'],strict_edge='edge')


def test_xml_saved_once_and_candidate_hash_does_not_impersonate_saved_model(tmp_path):
    parts={'receiver':box([10,10,2],[0,0,1]),'peg':box([2,2,2],[0,0,5])}
    path=tmp_path/'model.xml'
    kwargs=dict(D=150.,clearance_mm=.1,fixed_ids=['receiver'],initial_only=True,model_path=path)
    first=rollout(parts,config=CFG['rollout'],**kwargs)
    before=path.read_bytes();mtime=path.stat().st_mtime_ns
    second=rollout(parts,config=dict(CFG['rollout'],gravity=0.),**kwargs)
    assert first['status']==second['status']=='PASS'
    assert 'xml' not in first and 'xml' not in second
    assert first['xml_ref']['matches_current_model'] is True
    assert second['xml_ref']['matches_current_model'] is False
    assert first['model_sha256']!=second['model_sha256']
    assert first['xml_ref']['sha256']==second['xml_ref']['sha256']==hashlib.sha256(before).hexdigest()
    assert path.read_bytes()==before and path.stat().st_mtime_ns==mtime
    assert len(list(tmp_path.glob('*.xml')))==1
    assert ET.fromstring(before).find('size').get('memory')=='1G'
    cfg=dict(CFG['rollout'],memory='2G')
    xml,_=_rigid_flex_xml(parts,sorted(parts),['receiver'],cfg,.1)
    assert ET.fromstring(xml).find('size').get('memory')=='2G'
