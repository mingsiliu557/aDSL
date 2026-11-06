"""First-real-case wiring uses stubs; no model or native simulation calls."""
from pathlib import Path
import copy
import json
from types import SimpleNamespace

import numpy as np
import pytest
import trimesh
import yaml

from snap3d import pipeline
from snap3d.common import digest

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def parts():
    a=trimesh.creation.box([10,10,2]);a.apply_translation([0,0,1])
    b=trimesh.creation.box([2,2,2]);b.apply_translation([0,0,5])
    return {'receiver':a,'peg':b}


def observation(meshes, displacement=0.):
    start={key:dict(position_mm=mesh.center_mass.tolist(),rotation=np.eye(3).tolist()) for key,mesh in meshes.items()}
    return dict(status='PASS',reason=None,stable=True,global_score=0.,fixed_ids=['receiver'],
        metrics={key:dict(max_displacement_mm=0. if key=='receiver' else displacement,max_rotation_rad=0.) for key in meshes},
        frames=[copy.deepcopy(start),copy.deepcopy(start)],last_contact_pairs=[],
        final_interpart_contact={'maximum_penetration_mm':0.},wall_seconds=.1)


def test_paper_half_cell_zero_gravity_boundary_and_unavailable():
    p=parts();D=150.;value=.5*D/256
    assert pipeline.zero_gravity_review(observation(p,value-1e-8),D=D,config=CFG)['valid']
    assert not pipeline.zero_gravity_review(observation(p,value),D=D,config=CFG)['valid']
    missing=pipeline.zero_gravity_review(dict(status='UNAVAILABLE',reason='INVALID_INPUT'),D=D,config=CFG)
    assert missing['status']=='UNAVAILABLE' and missing['valid'] is None


def test_objective_calls_gravity_once_without_zero_gravity_gate(monkeypatch):
    calls=[];records=[]
    def fake(p,**kwargs):calls.append(kwargs);return observation(p,1.)
    monkeypatch.setattr(pipeline,'rollout',fake)
    r=pipeline.paper_rollout(parts(),D=150,config=CFG,record=lambda phase,result:records.append(phase))
    assert len(calls)==1 and calls[0]['config']['gravity']==9.81
    assert records==['gravity'] and 'zero_gravity' not in r


def test_final_material_volume_is_diagnostic_and_final_depth_is_the_gate(monkeypatch):
    def fake(p,**kwargs):
        r=observation(p);r['frames'][-1]['peg']['position_mm']=[0.,0.,1.]
        r['final_interpart_contact']['maximum_penetration_mm']=150/256
        return r
    monkeypatch.setattr(pipeline,'rollout',fake)
    r=pipeline.paper_rollout(parts(),D=150,config=CFG)
    assert r['stable'] and r['intersection_free']
    assert 'final_material_check' not in r
    assert pipeline.final_material_review(parts(),r)['total_intersection_volume_mm3']>0
    original=fake
    def deeper(p,**kwargs):
        r=original(p,**kwargs);r['final_interpart_contact']['maximum_penetration_mm']+=1e-8;return r
    monkeypatch.setattr(pipeline,'rollout',deeper)
    assert not pipeline.paper_rollout(parts(),D=150,config=CFG)['stable']


def test_pinned_sonata_redirects_only_model_location_and_restores_on_failure(monkeypatch,tmp_path):
    from snap3d import decompose
    calls=[]
    def original(name,*args,**kwargs):calls.append((name,args,kwargs));return 'model'
    modules={name:SimpleNamespace(load=original) for name in ['models.sonata','partgen.models.sonata']}
    monkeypatch.setattr(decompose.importlib,'import_module',lambda name:modules[name])
    checkpoint=tmp_path/'sonata.pth'
    with pytest.raises(RuntimeError):
        with decompose.pinned_sonata(checkpoint):
            assert modules['models.sonata'].load('sonata',repo_id='facebook/sonata')=='model'
            assert calls[-1][0]==str(checkpoint)
            assert calls[-1][2]=={'repo_id':'facebook/sonata'}
            raise RuntimeError('probe')
    assert all(module.load is original for module in modules.values())





def test_pge_rolls_back_an_invalid_cut_without_stopping_the_case():
    from snap3d.pge import pge
    large=trimesh.creation.box([10,10,10])
    small=trimesh.creation.box([1,1,1]);small.apply_translation([20,0,0])
    two_islands=trimesh.util.concatenate([large,small])
    output,report=pge({'A':two_islands,'B':small},config=CFG['pge'])
    assert report['rolled_back_cut_count']==1
    assert report['cuts'][0]['rollback_reasons']==['NOT_CLOSED_VOLUME','COMPONENT_COUNT_CHANGED']
    assert output['A'].volume==pytest.approx(two_islands.volume)
    assert output['B'].volume==pytest.approx(small.volume)


@pytest.mark.parametrize('wrapper',['generate','decompose'])
def test_upstream_local_snapshot_is_absolute_instead_of_cache_relative(tmp_path,monkeypatch,wrapper):
    import sys
    from PIL import Image
    from snap3d import generate,decompose
    module=generate if wrapper=='generate' else decompose
    monkeypatch.chdir(tmp_path)
    weights=Path('models/frozen');weights.mkdir(parents=True)
    source=Path('input.png');Image.new('RGB',(8,8)).save(source)
    auxiliary=tmp_path/'auxiliary';auxiliary.mkdir()
    monkeypatch.setattr(module,'verify_snapshot',lambda path,name:{})
    monkeypatch.setattr(module,'verify_auxiliary',lambda name:(auxiliary/'weight',{}))
    seen=[]
    def capture(path,**kwargs):
        seen.append(path)
        raise RuntimeError('CAPTURED_LOCAL_PATH')
    model=SimpleNamespace(from_pretrained=capture)
    monkeypatch.setitem(sys.modules,'torch',SimpleNamespace(manual_seed=lambda seed:None))
    if wrapper=='generate':
        monkeypatch.setitem(sys.modules,'hy3dgen.shapegen',SimpleNamespace(Hunyuan3DDiTFlowMatchingPipeline=model))
        monkeypatch.setitem(sys.modules,'hy3dgen.rembg',SimpleNamespace(BackgroundRemover=lambda:lambda image:image))
    else:
        monkeypatch.setitem(sys.modules,'partgen.partformer_pipeline',SimpleNamespace(PartFormerPipeline=model))
        fake_modules={name:SimpleNamespace(load=lambda *a,**k:None) for name in ['models.sonata','partgen.models.sonata']}
        original=decompose.importlib.import_module
        monkeypatch.setattr(decompose.importlib,'import_module',lambda name:fake_modules[name] if name in fake_modules else original(name))
    with pytest.raises(RuntimeError,match='CAPTURED_LOCAL_PATH'):
        getattr(module,wrapper)(source,weights=weights,output='output')
    assert seen==[str(weights.resolve())]


def test_initialize_freezes_actual_assembly_bounds_not_upstream_whole_bounds(tmp_path):
    from snap3d.export import export_parts,load_parts
    whole=trimesh.creation.box([2,2,2]);whole_path=tmp_path/'whole.ply';whole.export(whole_path)
    a=trimesh.creation.box([2,2,2]);a.apply_translation([-5,0,-3])
    b=trimesh.creation.box([1,1,1]);b.apply_translation([5,2,0])
    raw=tmp_path/'raw';export_parts({'A':a,'B':b},raw)
    run=tmp_path/'case';pipeline.initialize(run,whole_path,raw,CFG)
    record=json.loads((run/'case.json').read_text())
    actual=trimesh.util.concatenate(list(load_parts(run/'normalized_parts').values()))
    assert record['normalization_basis']=='raw_parts_assembly_bounds'
    assert max(actual.extents)==pytest.approx(150.)
    assert actual.bounds[0,2]==pytest.approx(0.)
    assert record['D_mm']==pytest.approx(float(np.linalg.norm(actual.extents)))
