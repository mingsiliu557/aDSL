"""Targeted metadata and review invariants; native adapter checks live in smoke.py."""
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/'scripts'))
from collect_candidates import category, family
from build_review_pack import shortlist, build
from common import dump, rows, write_rows


def test_seed_metadata_categories_do_not_infer_mesh_parts():
    assert category({'product_type':[{'value':'CHAIR'}]})=='chair_stool'
    assert category({'item_name':[{'value':'pendant ceiling lamp'}]}) is None
    assert category({'item_name':[{'value':'Table lamp'}]})=='lamp'
    assert category({'item_name':[{'value':'Dining table'}]})=='table'
    a=dict(item_id='one',item_name=[dict(language_tag='en_US',value='Rivet chair, Blue')])
    b=dict(item_id='two',item_name=[dict(language_tag='en_US',value='Rivet chair, Red')])
    assert family(a)==family(b)


def test_shortlist_round_robin_uses_measurable_references():
    values=[dict(case_id=str(i),category=cat,preflight_status='PASS',geometry_status='PASS') for i,cat in enumerate(['lamp','lamp','table','chair'])]
    values.append(dict(case_id='bad',category='cabinet',preflight_status='PASS',geometry_status='INDETERMINATE'))
    result=shortlist(dict(shortlist_count=3),values)
    assert result==['3','0','2'] and 'bad' not in result


def test_unknown_measurements_and_missing_assets_are_not_recommendations(tmp_path):
    (tmp_path/'review').mkdir();(tmp_path/'manifests').mkdir()
    values=[dict(case_id='ABO_bad',source='ABO',source_id='bad',category='lamp',status='needs_review',tags=[],selection_note='invalid'),
            dict(case_id='Toys4K_missing',source='Toys4K',source_id='missing',category='dog',status='needs_review',tags=[],selection_note='archive absent')]
    write_rows(tmp_path/'manifests/candidates.jsonl',values)
    dump(tmp_path/'measurements/ABO_bad/reference_measurement.json',dict(standing=None,overhang=None))
    build(dict(root=str(tmp_path),shortlist_count=12,recommend_per_source=10))
    assert rows(tmp_path/'manifests/recommended_dev20.jsonl')==[]
    assert all(r['status']=='needs_review' for r in rows(tmp_path/'manifests/candidates.jsonl'))
    assert (tmp_path/'selection_v1_review.zip').is_file()


def test_wall_mount_measurement_pass_does_not_imply_free_standing_use(tmp_path, monkeypatch):
    import build_review_pack
    from common import sha
    monkeypatch.setattr(build_review_pack,'cache_key',lambda *args:'fixture')
    raw=tmp_path/'original.glb';raw.write_bytes(b'original fixture')
    values=[dict(case_id='ABO_wall',source='ABO',source_id='wall',category='cabinet_shelf',status='needs_review',tags=[],selection_note='Wall mounted; standing use not applicable.',preflight_status='PASS',geometry_status='PASS',raw_mesh='original.glb',raw_sha256=sha(raw),manual_review=dict(standing_applicable=False))]
    values.append(dict(case_id='Toys4K_missing',source='Toys4K',source_id='missing',category='dog',status='needs_review',tags=[],selection_note='archive absent'))
    write_rows(tmp_path/'manifests/candidates.jsonl',values)
    dump(tmp_path/'measurements/ABO_wall/reference_measurement.json',dict(cache_key='fixture',files_sha256={str(raw):sha(raw)},standing=dict(status='PASS'),overhang=dict(status='PASS')))
    build(dict(root=str(tmp_path),shortlist_count=12,recommend_per_source=10))
    assert rows(tmp_path/'manifests/recommended_dev20.jsonl')==[]
    assert rows(tmp_path/'manifests/candidates.jsonl')[0]['status']=='needs_review'


def test_native_import_mm_and_glb_m_roundtrip(tmp_path):
    import os
    if os.environ.get('ADSL_TEST_BENCHMARK_REAL')!='1':
        __import__('pytest').skip('explicit native import smoke')
    import numpy as np
    import trimesh
    from common import process, sha, load
    root=tmp_path/'data/selection_v1';raw=root/'raw/ABO/fixture/original.glb';raw.parent.mkdir(parents=True)
    trimesh.creation.box((.02,.04,.06)).export(raw)
    original=sha(raw);output=root/'derived/fixture';output.mkdir(parents=True)
    row=tmp_path/'row.json';dump(row,dict(raw_mesh=str(raw.relative_to(root))))
    c=dict(root=str(root),data_root=str(tmp_path/'data'),longest_extent_mm=150,
           render=dict(width=512,height=512,samples=32,threads=4,timeout_seconds=300))
    cfg=tmp_path/'config.json';dump(cfg,c)
    worker=Path(__file__).parents[1]/'scripts/reference_worker.py'
    result=process([sys.executable,str(worker),'--config',str(cfg),'--row',str(row),'--output',str(output),'--stage','import'],tmp_path/'logs',300,c)
    assert result['status']=='PASS'
    basic=load(output/'basic.json');assert basic['status']=='PASS' and basic['standing_eligible']
    assert basic['volume_mm3']==__import__('pytest').approx(750000,rel=1e-6)
    with np.load(output/'reference_whole.body.npz') as data:
        extents=np.ptp(data['vertices'],axis=0)
    np.testing.assert_allclose(sorted(extents),[50,100,150],atol=1e-4)
    scene=trimesh.load(output/'reference.glb',force='scene')
    np.testing.assert_allclose(sorted(scene.extents),[.05,.1,.15],atol=1e-7)
    assert sha(raw)==original


def test_native_overlapping_shells_union_and_cavity_is_one_material_island(tmp_path):
    import os
    if os.environ.get('ADSL_TEST_BENCHMARK_REAL')!='1': __import__('pytest').skip('explicit native import smoke')
    import numpy as np
    import trimesh
    import manifold3d as mf
    from adsl.core.assembly_topology import solid_mesh
    from common import process, load
    shapes=[('overlap',trimesh.util.concatenate([trimesh.creation.box((2,2,2)),trimesh.creation.box((2,2,2),transform=trimesh.transformations.translation_matrix([1,0,0]))]),12.,3.),
            ('cavity',solid_mesh(mf.Manifold.cube((3,3,3))-mf.Manifold.cube((1,1,1)).translate((1,1,1))),26.,3.)]
    for name,mesh,volume,extent in shapes:
        root=tmp_path/name/'selection_v1';raw=root/'raw/model.glb';raw.parent.mkdir(parents=True);mesh.export(raw)
        row=tmp_path/(name+'.json');dump(row,dict(raw_mesh=str(raw.relative_to(root))))
        cfg=tmp_path/(name+'.config.json');c=dict(data_root=str(root.parent),longest_extent_mm=150,render=dict(timeout_seconds=300));dump(cfg,c)
        output=root/'derived';worker=Path(__file__).parents[1]/'scripts/reference_worker.py'
        result=process([sys.executable,str(worker),'--config',str(cfg),'--row',str(row),'--output',str(output),'--stage','import'],root/'logs',300,c)
        assert result['status']=='PASS'
        basic=load(output/'basic.json')
        assert basic['status']=='PASS' and basic['connected_components']==1 and basic['standing_eligible']
        assert basic['volume_mm3']==__import__('pytest').approx(volume*(150/extent)**3,rel=1e-6)
