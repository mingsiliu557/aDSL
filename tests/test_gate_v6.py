"""Zero-gravity gate threshold and hash-bound continuation; no native rollout."""
from pathlib import Path
import copy
import importlib.util
import json

import pytest
import yaml

from snap3d.common import digest, sha, write_json

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def gate_module():
    spec=importlib.util.spec_from_file_location('gate_v6',Path('scripts/lock_gate.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def result(motion=.14224122955839383,drop=-.06297823981171291,fixed=0.):
    return dict(status='PASS',gate_pass=False,frames=[{'peg':{'position_mm':[0.,0.,0.]}},
        {'peg':{'position_mm':[0.,0.,drop]}}],metrics={'receiver':{'max_displacement_mm':fixed},
        'peg':{'max_displacement_mm':motion}},rollout_wall_seconds=2.59,
        max_linear_speed_m_s=.00042,initial_contact_count=50,final_contact_count=0)


def test_zero_gravity_threshold_is_configured_half_cell_and_fixed_bound_unchanged():
    gate=gate_module();D=150.
    assert CFG['gate']['zero_gravity_displacement_ratio']==.5/256==.001953125
    assert gate.gate_verdict('zero_gravity',result(),D,3.,.15,CFG)[0]
    stricter=copy.deepcopy(CFG);stricter['gate']['zero_gravity_displacement_ratio']=1e-4
    assert not gate.gate_verdict('zero_gravity',result(),D,3.,.15,stricter)[0]
    assert not gate.gate_verdict('zero_gravity',result(fixed=.01500001),D,3.,.15,CFG)[0]
    assert gate.gate_verdict('zero_gravity',result(fixed=.015),D,3.,.15,CFG)[0]
    assert not gate.gate_verdict('zero_gravity',result(motion=.5*D/256),D,3.,.15,CFG)[0]


def frozen_run(tmp_path,monkeypatch,gate):
    root=tmp_path/'project';folder=root/'runs/v5';(folder/'zero_gravity').mkdir(parents=True)
    (root/'scripts').mkdir();(root/'snap3d').mkdir()
    (root/'snap3d/rollout.py').write_text('# frozen simulator\n')
    monkeypatch.setattr(gate,'__file__',str(root/'scripts/lock_gate.py'))
    parts,_,record,D,_=gate.fixture(True)
    for name,mesh in parts.items():mesh.export(folder/f'zero_gravity/{name}.stl')
    write_json(folder/'zero_gravity/connector.json',record)
    write_json(folder/'zero_gravity/result.json',result())
    (folder/'zero_gravity/model.xml').write_text('<mujoco/>')
    previous={k:v for k,v in CFG.items() if k!='gate'}
    write_json(folder/'gate.json',dict(config_sha256=digest(previous),fixture_diagonal_mm=D,
        coplanar_control={'diagnostic_only':True},normal_only_control=None))
    files={str(p.relative_to(root)):sha(p) for p in folder.rglob('*') if p.is_file()}
    files['snap3d/rollout.py']=sha(root/'snap3d/rollout.py')
    manifest=root/'identity.json'
    write_json(manifest,dict(config_yaml=yaml.safe_dump(previous),config_sha256=digest(previous),
        run_directory='runs/v5',files=files))
    return root,folder,manifest


def test_reuse_checks_identity_preserves_saved_result_and_does_not_simulate(tmp_path,monkeypatch):
    gate=gate_module();root,folder,manifest=frozen_run(tmp_path,monkeypatch,gate)
    before={p:sha(p) for p in folder.rglob('*') if p.is_file()}
    saved,_,reference=gate.reuse_zero_gravity(manifest,CFG)
    assert not saved['gate_pass'] and reference['simulation_rerun'] is False
    assert reference['result_sha256']==sha(folder/'zero_gravity/result.json')
    assert before=={p:sha(p) for p in before}


@pytest.mark.parametrize('filename',['runs/v5/zero_gravity/result.json','runs/v5/zero_gravity/model.xml',
    'runs/v5/zero_gravity/peg.stl','snap3d/rollout.py'])
def test_reuse_refuses_modified_result_mesh_xml_or_simulator(tmp_path,monkeypatch,filename):
    gate=gate_module();root,_,manifest=frozen_run(tmp_path,monkeypatch,gate)
    path=root/filename;path.write_bytes(path.read_bytes()+b' ')
    with pytest.raises(ValueError,match='REUSE_HASH_MISMATCH'):
        gate.reuse_zero_gravity(manifest,CFG)


def test_reuse_refuses_simulation_config_change(tmp_path,monkeypatch):
    gate=gate_module();_,_,manifest=frozen_run(tmp_path,monkeypatch,gate)
    cfg=copy.deepcopy(CFG);cfg['rollout']['dt']=.001
    with pytest.raises(ValueError,match='REUSE_SIMULATION_CONFIG_MISMATCH'):
        gate.reuse_zero_gravity(manifest,cfg)


@pytest.mark.parametrize('no_head_pass',[True,False])
def test_continuation_runs_only_remaining_gates_and_stops_at_failure(tmp_path,monkeypatch,no_head_pass):
    gate=gate_module();_,folder,manifest=frozen_run(tmp_path,monkeypatch,gate)
    original_sha=sha(folder/'zero_gravity/result.json');calls=[]
    def fake(parts,**kwargs):
        calls.append(kwargs)
        assert kwargs['config']['gravity']==9.81
        return result(motion=10.,drop=-10.) if len(calls)==1 and no_head_pass else result(motion=0.,drop=0.)
    monkeypatch.setattr(gate,'rollout',fake)
    out=tmp_path/'v6';out.mkdir()
    report=gate.rigid_flex_gate(out,CFG,reuse_manifest=manifest)
    assert len(calls)==(2 if no_head_pass else 1)
    assert report['status']==('PASS' if no_head_pass else 'FAIL')
    assert report['not_run']==([] if no_head_pass else ['headed'])
    assert report['tests'][0]['pass_'] and report['tests'][0]['reused']
    assert report['controls_reused'] and not (out/'coplanar_boxes_zero_gravity').exists()
    assert sha(folder/'zero_gravity/result.json')==original_sha
    assert json.loads((out/'zero_gravity/result.json').read_text())['gate_pass']
    assert report['reused_zero_gravity']['previous_gate_pass'] is False
