"""Paper constraints and requested local-failure continuation; no model calls."""
import copy
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh
import yaml

from snap3d import contact, connector, pipeline
from snap3d.common import code_identity, digest, write_json
from snap3d.export import export_parts, load_parts
from snap3d.rollout import rollout
from test_first_real import observation

CFG = yaml.safe_load(Path('configs/default.yaml').read_text())


def box(size, center):
    m=trimesh.creation.box(size);m.apply_translation(center);return m


def test_anchor_failure_skips_one_edge_and_continues(monkeypatch):
    parts={str(i):box([1,1,1],[i,0,0]) for i in range(3)}
    monkeypatch.setattr(contact,'material_distance',lambda *a:(0.,'fixture'))
    monkeypatch.setattr(contact,'facing_area',lambda *a,**k:{'edge':True})
    calls=[]
    def anchor(*a,**k):
        calls.append(1)
        if len(calls)==1:raise contact.AnchorUnavailable('NO_CONTACT_PATCH')
        return np.zeros(3),np.array([0.,0.,1.]),1.
    monkeypatch.setattr(contact,'anchor',anchor)
    edges,theta,pairs=contact.contact_graph(parts,D=150,config=CFG['contact'],connector_config=CFG['connector'])
    assert len(calls)==3 and len(edges)==2 and len(theta)==2
    assert pairs[0]['connector_status']=='SKIPPED' and pairs[0]['reason']=='NO_CONTACT_PATCH'
    def broken(*a,**k):raise RuntimeError('numeric backend error')
    monkeypatch.setattr(contact,'anchor',broken)
    with pytest.raises(RuntimeError,match='numeric'):contact.contact_graph(parts,D=150,config=CFG['contact'],connector_config=CFG['connector'])


def edge(name='edge'):
    return dict(id=name,a='donor',b='receiver',donor='donor',receiver='receiver',anchor=[0,0,0],inward=[0,0,1])


def test_receiver_material_and_existing_overlap_are_warnings():
    # Tiny receiver cannot contain the requested pocket; geometry still exports.
    base={'donor':box([5,5,4],[0,0,-1.9]),'receiver':box([3,3,.4],[0,0,.2])}
    output,records=connector.rebuild(base,[edge()],{'edge':[0,0,0,0,.9,3.]},D=150,config=CFG['connector'])
    assert records[0]['status']=='PLACED' and set(output)==set(base)
    assert 'INSUFFICIENT_RECEIVER_MATERIAL' in {w['code'] for w in records[0]['warnings']}


def test_local_infeasible_initial_edge_skips_but_cem_redraws(monkeypatch):
    base={'donor':box([3,3,3],[0,0,-1.5]),'receiver':box([10,10,10],[0,0,5])}
    edges=[edge('bad'),edge('good')]
    parameters={e['id']:[0,0,0,0,.9,3.] for e in edges}
    original=connector._edge_geometry
    def geometry(base,solids,e,*a,**k):
        if e['id']=='bad':raise connector.ConnectorInfeasible('AXIS_CONE_VIOLATION')
        return original(base,solids,e,*a,**k)
    monkeypatch.setattr(connector,'_edge_geometry',geometry)
    output,records=connector.rebuild(base,edges,parameters,D=150,config=CFG['connector'])
    assert [r['status'] for r in records]==['SKIPPED','PLACED']
    assert set(output)==set(base)
    with pytest.raises(connector.ConnectorInfeasible):
        connector.rebuild(base,edges,parameters,D=150,config=CFG['connector'],strict_edge='bad')
    parameters['good'][4]=float('nan')
    with pytest.raises(ValueError,match='NONFINITE_THETA'):
        connector.rebuild(base,[edge('good')],parameters,D=150,config=CFG['connector'])


def test_ground_pinning_uses_contact_delta_including_below_ground():
    parts={'low':box([1,1,1],[0,0,-.1]),'near':box([1,1,1],[3,0,1.]),
           'free':box([1,1,1],[6,0,2.])}
    r=rollout(parts,D=150,config=CFG['rollout'],clearance_mm=.1,initial_only=True)
    assert r['status']=='PASS' and set(r['fixed_ids'])=={'low','near'}
    assert CFG['rollout']['ground_ratio']==CFG['contact']['delta_ratio']==.005


def test_end_to_end_only_final_export_gets_zero_gravity_and_identity_warnings(tmp_path,monkeypatch):
    meshes={'receiver':box([10,10,2],[0,0,1]),'peg':box([2,2,2],[0,0,5])}
    write_json(tmp_path/'case.json',dict(D_mm=150.,config_sha256='old',code_identity={'old':'old'}))
    export_parts(meshes,tmp_path/'repair')
    monkeypatch.setattr(pipeline,'contact_graph',lambda *a,**k:([],{},[]))
    calls=[];reviews=[]
    original=pipeline.final_material_review
    def material(*a,**k):reviews.append(1);return original(*a,**k)
    monkeypatch.setattr(pipeline,'final_material_review',material)
    def simulate(parts,**kwargs):
        calls.append(kwargs['config']['gravity'])
        return observation(parts,1. if kwargs['config']['gravity']==0 else 0.)
    monkeypatch.setattr(pipeline,'rollout',simulate)
    for stage in ['pge','cgr','rollout','cem','export']:
        pipeline.evaluate_geometry(tmp_path,CFG,stage=stage)
    assert calls==[9.81,0.] and len(reviews)==1
    result=json.loads((tmp_path/'export/result.json').read_text())
    assert result['stable'] and result['zero_gravity_validity']['validity']=='invalid'
    assert set(load_parts(tmp_path/'export'))==set(meshes)
    stage=json.loads((tmp_path/'export/stage.json').read_text())
    assert stage['status']=='PASS' and stage['warnings']==['CONFIG_CHANGED','CODE_CHANGED']
    pipeline.evaluate_geometry(tmp_path,CFG,stage='pge')  # Explicit stage overwrite.
    assert json.loads((tmp_path/'pge/stage.json').read_text())['status']=='PASS'


def test_simulation_failure_is_not_swallowed_as_geometric_infeasibility(tmp_path,monkeypatch):
    write_json(tmp_path/'case.json',dict(D_mm=150.,config_sha256=digest(CFG),code_identity=code_identity()))
    write_json(tmp_path/'cgr/contact.json',dict(edges=[],parameters={}))
    monkeypatch.setattr(pipeline,'load_parts',lambda p:{})
    monkeypatch.setattr(pipeline,'rollout',lambda *a,**k:dict(status='UNAVAILABLE',reason='SIMULATION_ERROR'))
    with pytest.raises(RuntimeError,match='SIMULATION_ERROR'):
        pipeline.evaluate_geometry(tmp_path,CFG,stage='rollout')
    assert json.loads((tmp_path/'rollout/stage.json').read_text())['status']=='FAIL'


def test_manifold_vertex_touch_cut_is_retained_without_mesh_roundtrip(monkeypatch):
    import manifold3d as mf
    from snap3d import pge as module
    from snap3d.vendor import solid_mesh,mesh_solid
    from snap3d.vendor.mesh_validity import MeshEvaluationError
    before=mf.Manifold.cube([1,1,1])+mf.Manifold.cube([1,1,1]).translate([1,1,1])
    cutter=mf.Manifold.cube([.2,2,2]).translate([0,-.5,-.5])
    def forbidden(*a,**k):raise AssertionError('PGE cut must remain in Manifold')
    monkeypatch.setattr(module,'solid_mesh',forbidden)
    result,record=module._cut(before,cutter,sliver_thickness_mm=.001)
    assert result.status()==mf.Error.NoError and record['retained']
    assert record['component_count_before']==record['component_count_after']==2
    with pytest.raises(MeshEvaluationError):mesh_solid(solid_mesh(result))


def test_cem_exhausted_edge_continues_to_next_edge(tmp_path,monkeypatch):
    meshes={'receiver':box([10,10,2],[0,0,1]),'peg':box([2,2,2],[0,0,5])}
    edges=[edge('first'),edge('second')]
    write_json(tmp_path/'case.json',dict(D_mm=150.,config_sha256=digest(CFG),code_identity=code_identity()))
    write_json(tmp_path/'cgr/contact.json',dict(edges=edges,parameters={e['id']:[0,0,0,0,1,3] for e in edges}))
    baseline=observation(meshes);baseline.update(stable=False,global_score=2.)
    write_json(tmp_path/'rollout/result.json',baseline)
    monkeypatch.setattr(pipeline,'load_parts',lambda _:meshes)
    monkeypatch.setattr(pipeline,'relative_score',lambda *a,**k:2.)
    monkeypatch.setattr(pipeline,'select_edges',lambda *a,attempted=(),**k:[e['id'] for e in edges if e['id'] not in attempted])
    tried=[]
    def reject(base,edges,parameters,**kwargs):
        tried.append(kwargs['strict_edge'])
        raise connector.ConnectorInfeasible('AXIS_CONE_VIOLATION')
    monkeypatch.setattr(pipeline,'rebuild',reject)
    def optimize(theta,*,evaluate,**kwargs):
        assert evaluate(theta)['status']=='FAIL'
        return dict(best=None,history=[],evaluations=1)
    monkeypatch.setattr(pipeline,'optimize',optimize)
    pipeline.evaluate_geometry(tmp_path,CFG,stage='cem')
    assert tried==['first','second']
    result=json.loads((tmp_path/'cem/result.json').read_text())
    assert result==baseline
    assert [r['edge'] for r in json.loads((tmp_path/'cem/optimization.json').read_text())]==tried


def test_meshfix_fallback_preserves_all_components_and_uses_current_api(monkeypatch):
    import sys
    from types import SimpleNamespace
    from snap3d.repair import repair
    closed=box([10,10,10],[0,0,0]);opened=closed.copy()
    opened.update_faces(np.arange(len(opened.faces))>1)
    calls=[]
    monkeypatch.setattr(trimesh.repair,'fill_holes',lambda m:False)
    class MeshFix:
        def __init__(self,vertices,faces):self.points=closed.vertices;self.faces=closed.faces
        def repair(self,*,joincomp,remove_smallest_components):calls.append((joincomp,remove_smallest_components))
    monkeypatch.setitem(sys.modules,'pymeshfix',SimpleNamespace(MeshFix=MeshFix))
    output,record=repair(opened,D=150.,config=CFG['repair'])
    assert output.is_watertight and calls==[(False,False)]
    assert record['methods']==['trimesh.fill_holes','pymeshfix.repair']
