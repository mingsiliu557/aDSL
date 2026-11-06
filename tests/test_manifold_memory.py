from pathlib import Path
import hashlib
import xml.etree.ElementTree as ET

import pytest
import trimesh
import yaml

from snap3d.connector import rebuild,ConnectorInfeasible
from snap3d.pipeline import rollout_cost
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


def test_probe_projection_includes_initialization_and_nominal_96_per_edge():
    probe=dict(status='PASS',step_count=50,step_wall_seconds=.5,initialization_wall_seconds=1.,
               wall_seconds=1.5,flex_element_count=100)
    result=rollout_cost(probe,config=CFG,edge_count=3,rebuild_seconds=.25)
    assert result['mean_step_wall_seconds']==pytest.approx(.01)
    assert result['projected_single_rollout_seconds']==pytest.approx(11.)
    assert result['nominal_evaluations']==96*3
    assert result['projected_cem_with_rebuild_seconds']==pytest.approx(11.25*96*3)
    with pytest.raises(ValueError,match='INCOMPLETE'):
        rollout_cost(dict(probe,status='UNAVAILABLE'),config=CFG,edge_count=3)


@pytest.mark.parametrize('over_limit',[False,True])
def test_cost_guard_always_finishes_baseline_before_stopping(tmp_path,monkeypatch,over_limit):
    import importlib.util,json
    from snap3d.common import write_json
    spec=importlib.util.spec_from_file_location('continue_case',Path('scripts/continue_case.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    calls=[]
    write_json(tmp_path/'case.json',dict(D_mm=150.))
    def evaluate(run,config,*,stage):
        calls.append(stage)
        if stage=='cgr':write_json(tmp_path/'cgr/contact.json',dict(edges=[{'id':'edge'},{'id':'other'},{'id':'third'}],timing={'rebuild_seconds':.01}))
        if stage=='rollout':write_json(tmp_path/'rollout/result.json',dict(stable=False,wall_seconds=1.))
        if stage=='export':write_json(tmp_path/'export/result.json',dict(stable=True,zero_gravity_validity={'valid':True}))
    monkeypatch.setattr(module,'evaluate_geometry',evaluate)
    monkeypatch.setattr(module,'load_parts',lambda _: {})
    monkeypatch.setattr(module,'select_edges',lambda *a,**k:['edge'])
    def probe(*args,**kwargs):
        assert kwargs['config']['duration']==.1
        step=5000. if over_limit else .05
        return dict(status='PASS',step_count=50,step_wall_seconds=step,
                    initialization_wall_seconds=.1,wall_seconds=step+.1,flex_element_count=100)
    monkeypatch.setattr(module,'rollout',probe)
    result=module.continue_case(tmp_path,CFG)
    assert calls[:3]==['pge','cgr','rollout']
    if over_limit:assert result=='STOPPED_COST_LIMIT' and calls==['pge','cgr','rollout']
    else:assert result=='COMPLETE' and calls==['pge','cgr','rollout','cem','export']
    guard=json.loads((tmp_path/'speed_probe/cost_guard.json').read_text())
    assert guard['stop_after_baseline'] is over_limit
    assert guard['nominal_evaluations']==96 and guard['optimization_edge_count']==1
    assert guard['placed_edge_count']==3
