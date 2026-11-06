import copy
import importlib.util
import json
from pathlib import Path
import pytest
import yaml
from snap3d.common import write_json,sha

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())
spec=importlib.util.spec_from_file_location('continue_case',Path('scripts/continue_case.py'))
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def prepare(run):
    write_json(run/'case.json',{'D_mm':150.})
    baseline=dict(status='PASS',step_count=1000,initial_contact={'contact_count':2},flex_element_count=24,
                  fixed_ids=['base'],initialization_wall_seconds=3.,stable=False)
    write_json(run/'rollout/result.json',baseline)
    old=copy.deepcopy(CFG);old['cem'].update(population=32,elite=8,generations=3);old['cem'].pop('workers')
    write_json(run/'reuse_bindings.json',dict(original_config=old,baseline_xml={'path':'old.xml','sha256':'old'},
        files={'case.json':sha(run/'case.json'),'rollout/result.json':sha(run/'rollout/result.json')}))
    return baseline


def test_continuation_reuses_hash_bound_cgr_and_full_baseline_only(tmp_path,monkeypatch):
    baseline=prepare(tmp_path);calls=[]
    def evaluate(run,config,*,stage):
        calls.append(stage)
        if stage=='export':write_json(tmp_path/'export/result.json',{'stable':True,'zero_gravity_validity':{'valid':True}})
    monkeypatch.setattr(module,'evaluate_geometry',evaluate)
    monkeypatch.setattr(module,'load_parts',lambda _: {})
    def initialize(*a,**kwargs):
        assert kwargs['initial_only'] is True and kwargs['config']['duration']==2.
        return dict(status='PASS',initial_contact=baseline['initial_contact'],flex_element_count=24,
                    xml_ref=None,wall_seconds=1.)
    monkeypatch.setattr(module,'rollout',initialize)
    assert module.continue_case(tmp_path,CFG)=='COMPLETE'
    assert calls==['cem','export']
    assert json.loads((tmp_path/'rollout/result.json').read_text())==baseline
    changed=copy.deepcopy(CFG);changed['rollout']['dt']=.001
    with pytest.raises(ValueError,match='PHYSICS_CONFIG'):module.verify_reuse(tmp_path,changed)
    (tmp_path/'case.json').write_text('{}')
    with pytest.raises(ValueError,match='HASH_MISMATCH'):module.verify_reuse(tmp_path,CFG)


def test_worker_counts_distinguish_terminated_inflight_and_completed(tmp_path):
    from snap3d.pipeline import _worker_counts
    for i,status in enumerate(['COMPLETE','RUNNING','FAILED']):write_json(tmp_path/f'{i}.json',{'status':status})
    (tmp_path/'incomplete.tmp').write_text('{')
    assert _worker_counts(tmp_path)==dict(started=3,completed=1,failed=1,terminated_inflight=1)
