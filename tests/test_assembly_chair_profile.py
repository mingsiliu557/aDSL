"""SF03 configuration and existing region/deck entry points; no API or meshing."""
import json
from pathlib import Path

import numpy as np
import pytest

from experiments.load_bearing_structural_performance.assembly import (
    boundary_faces, select_region, write_assembly_deck,
)

PROFILE = Path(__file__).resolve().parents[1]/'examples/fixed_assembly/physics_sf03_chair.json'


def test_complete_chair_profile_reaches_geometry_not_needs_spec(monkeypatch):
    from adsl.agents import assembly_fea
    config = json.loads(PROFILE.read_text())
    class GeometryReached(Exception):
        pass
    def load(_):
        raise GeometryReached
    monkeypatch.setattr(assembly_fea, 'load_parts', load)
    with pytest.raises(GeometryReached):
        assembly_fea.analyze(None, config)
    assert config['fea']['include_gravity'] is True
    assert config['fea']['allowable_stress_pa'] == config['material']['yield_strength_pa']/2
    assert config['fea']['allowable_displacement_m'] == .005


def test_chair_semantic_regions_and_total_loads(tmp_path):
    # Analytic C3D10 fixtures verify explicit region mapping and load sums only;
    # these disconnected tetrahedra are NOT a mechanics-success test.
    config = json.loads(PROFILE.read_text());fea = config['fea']
    nodes, elements, mapping = {}, {}, {}
    fixtures = [
        ('arbitrary_a', 'FrontLeftLeg', [[0,0,0],[.01,0,0],[0,.01,0],[0,0,.01]]),
        ('arbitrary_b', 'RectangularSeat', [[0,0,.45],[.01,0,.45],[0,.01,.45],[0,0,.44]]),
        ('arbitrary_c', 'HighBackrest', [[0,.18,.7],[.01,.18,.7],[0,.18,.71],[0,.17,.7]]),
    ]
    for eid,(name,component,corners) in enumerate(fixtures,1):
        points = [np.array(v) for v in corners]
        points += [(points[a]+points[b])/2 for a,b in ((0,1),(1,2),(2,0),(0,3),(1,3),(2,3))]
        ids = list(range(len(nodes)+1,len(nodes)+11))
        nodes.update(zip(ids,points));elements[eid]=ids
        mapping[name] = dict(node_ids=ids,element_ids=[eid],components=[component],
            assembly_transform_m=np.eye(4).tolist(),faces=boundary_faces(nodes,{eid:ids}))
    for spec,expected in zip([*fea['supports'],*fea['loads']],mapping):
        assert {name for name,_ in select_region(spec,nodes,mapping)} == {expected}
    deck = tmp_path/'assembly.inp'
    result = write_assembly_deck(deck,nodes,elements,mapping,config['material'],[],
                                fea['supports'],fea['loads'],fea['include_gravity'])
    np.testing.assert_allclose(np.sum(list(result['nodal_forces'].values()),axis=0),[0,300,-1000])
    assert set(n for n,_ in result['support_node_dofs']) <= set(mapping['arbitrary_a']['node_ids'])
    text=deck.read_text()
    assert 'EALL, GRAV, 9.81' in text and '*ELASTIC' in text and '*BOUNDARY' in text
    bad=dict(fea['loads'][0],semantic_pattern='no_such_component')
    with pytest.raises(ValueError,match='REGION_UNAVAILABLE'):
        select_region(bad,nodes,mapping)


def test_chair_profile_frozen_in_prompt_input(tmp_path):
    from experiments.fixed_assembly_prompt.run import prepare
    physics=json.loads(PROFILE.read_text())
    prepare(tmp_path,cases=('SF03',),max_rounds=5,sizes={'SF03':[450.,400.,900.]},
        tools=('assembly_topology','assembly_standing','assembly_overhang','assembly_fea'),physics=physics)
    inputs=json.loads((tmp_path/'SF03/input.json').read_text())
    assert inputs['fixed_assembly']['physics']==physics
    assert inputs['fixed_assembly']['final_size_mm']==[450.,400.,900.]
    assert inputs['source_repair_limit']==4
    assert inputs['repair_policy']['print_orientation_editable'] is True
    assert next(s for s in inputs['checker_specs'] if s['name']=='assembly_fea')['required'] is True
    assert 'not normalized toy geometry' in inputs['manufacturing_requirements']
