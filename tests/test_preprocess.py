from pathlib import Path
import numpy as np
import trimesh
from snap3d import preprocess,pipeline
from snap3d.common import write_json,code_identity,digest
from snap3d.export import export_parts,load_parts
import yaml
CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def test_below_budget_is_exactly_unchanged_without_backend(monkeypatch):
    m=trimesh.creation.box();before=m.vertices.copy();faces=m.faces.copy()
    def forbidden(*a):raise AssertionError('under-budget mesh must not be processed')
    monkeypatch.setattr(preprocess,'_quadric',forbidden);monkeypatch.setattr(preprocess,'_close',forbidden)
    result,record=preprocess.simplify_parts({'body':m},max_faces_per_case=12)
    assert result['body'] is m and record['total_faces_after']==12
    np.testing.assert_array_equal(m.vertices,before);np.testing.assert_array_equal(m.faces,faces)


def test_proportional_quadric_is_volume_and_within_budget():
    parts={'a':trimesh.creation.icosphere(subdivisions=3),'b':trimesh.creation.icosphere(subdivisions=2)}
    before={k:(m.vertices.copy(),m.faces.copy()) for k,m in parts.items()}
    result,record=preprocess.simplify_parts(parts,max_faces_per_case=1200)
    assert record['total_faces_after']<=1200 and not record['fallback_parts']
    assert [r['target_faces'] for r in record['parts']]==[960,240]
    assert all(m.is_volume for m in result.values())
    assert all(r['volume_change_ratio'] is not None for r in record['parts'])
    for k,m in parts.items():
        np.testing.assert_array_equal(m.vertices,before[k][0]);np.testing.assert_array_equal(m.faces,before[k][1])


def test_open_simplification_is_meshfixed(monkeypatch):
    original=trimesh.creation.icosphere(subdivisions=1);closed=trimesh.creation.box();opened=closed.copy();opened.update_faces(np.arange(12)>1)
    monkeypatch.setattr(preprocess,'_quadric',lambda *a:opened)
    calls=[]
    def repair(mesh):calls.append(mesh);return closed
    monkeypatch.setattr(preprocess,'_close',repair)
    output,record=preprocess.simplify_parts({'a':original},max_faces_per_case=20)
    assert len(calls)==1 and output['a'].is_volume
    assert record['parts'][0]['pymeshfix_applied'] and not record['fallback_parts']


def test_unrepairable_mesh_falls_back_to_original_and_records_overage(monkeypatch):
    original=trimesh.creation.icosphere(subdivisions=1);opened=trimesh.creation.box();opened.update_faces(np.arange(12)>1)
    monkeypatch.setattr(preprocess,'_quadric',lambda *a:opened)
    monkeypatch.setattr(preprocess,'_close',lambda m:m)
    output,record=preprocess.simplify_parts({'a':original},max_faces_per_case=20)
    assert output['a'] is original and output['a'].is_volume
    assert record['fallback_parts']==['a'] and not record['within_budget']
    assert record['parts'][0]['volume_change_ratio']==0


def test_quadric_error_falls_back_without_new_geometry(monkeypatch):
    original=trimesh.creation.icosphere(subdivisions=1)
    def fail(*a):raise RuntimeError('decimation backend failed')
    monkeypatch.setattr(preprocess,'_quadric',fail)
    output,record=preprocess.simplify_parts({'a':original},max_faces_per_case=20)
    assert output['a'] is original and 'backend failed' in record['parts'][0]['reason']


def test_pipeline_pge_consumes_simplified_mesh(tmp_path,monkeypatch):
    write_json(tmp_path/'case.json',dict(D_mm=150,config_sha256=digest(CFG),code_identity=code_identity()))
    export_parts({'body':trimesh.creation.icosphere(subdivisions=1)},tmp_path/'repair')
    cfg=dict(CFG,preprocess={'max_faces_per_case':40})
    pipeline.evaluate_geometry(tmp_path,cfg,stage='simplify')
    calls=[]
    def pge(parts,**kwargs):calls.append(sum(len(m.faces) for m in parts.values()));return parts,{}
    monkeypatch.setattr(pipeline,'pge',pge)
    pipeline.evaluate_geometry(tmp_path,cfg,stage='pge')
    assert calls[0]<=40 and load_parts(tmp_path/'pge')['body'].is_volume
