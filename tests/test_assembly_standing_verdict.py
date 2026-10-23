"""Standing verdict routing: real fixture geometry, mocked physics measurements."""
import json
import sys
from copy import deepcopy

import pytest

from adsl.agents import assembly_physics, assembly_standing as standing
from adsl.core.assembly_topology import mesh_solid
from test_assembly_physics import fixture, STANDING, MATERIAL


@pytest.fixture
def measured_run(tmp_path, monkeypatch):
    args, _ = fixture(tmp_path)
    # Avoid optional physics dependencies here: this tests the decision made
    # from measurements, not CoACD accuracy or native contact behavior.
    monkeypatch.setattr(standing, 'collision_proxies', lambda mesh, config: [mesh.copy()])
    monkeypatch.setattr(standing, 'verify_proxies',
                        lambda mesh, proxies, clearance: ({'accepted': True}, mesh_solid(mesh)))
    monkeypatch.setattr(standing, 'save_frames', lambda *args: [])
    monkeypatch.setattr(standing.importlib.metadata, 'version', lambda name: 'mock-verdict-test')
    result = dict(tipped=False, exits={}, settled=False, interface_retention_verified=True,
                  assessment_time_seconds=5., final_tilt_deg=.005, peak_tilt_deg=.16,
                  final_exited_interfaces=[],
                  final_interfaces=[{'connection_id': 'joint', 'exited': False}],
                  final_linear_speed_m_s=.003, final_angular_speed_rad_s=.02)
    monkeypatch.setattr(standing, 'simulate', lambda *args: (deepcopy(result), []))
    return args, result


@pytest.mark.parametrize('changes, expected, rule', [
    ({}, 'PASS', None),
    ({'settled': True}, 'PASS', None),
    # A recovered final pose must not erase a prior tipping or exit event.
    ({'tipped': True, 'peak_tilt_deg': 26.}, 'FAIL', 'SELF_WEIGHT_TIPPING'),
    ({'exits': {'joint': .4}}, 'FAIL', 'SELF_WEIGHT_INTERFACE_EXIT'),
    ({'interface_retention_verified': False}, 'INDETERMINATE', None),
])
def test_standing_uses_full_observation_not_settling(measured_run, changes, expected, rule):
    args, measurement = measured_run
    measurement.update(changes)
    result = standing.analyze(args, {'standing': STANDING, 'material': MATERIAL})
    assert result.status == expected
    assert result.metrics['settled'] == measurement['settled']
    assert result.metrics['final_linear_speed_m_s'] == .003
    assert result.metrics['final_angular_speed_rad_s'] == .02
    if expected == 'INDETERMINATE':
        observation = result.metrics['standing_observation']
        assert observation['status'] == 'UNAVAILABLE'
        assert all(v is None for k,v in observation.items() if k != 'status')
    else:
        assert 'settling diagnostic:' in result.summary
        assert 'not a pass requirement' in result.summary
        assert f'standing observation {expected}' in result.summary
    assert [f.rule_id for f in result.findings] == ([rule] if rule else [])
    if rule == 'SELF_WEIGHT_INTERFACE_EXIT':
        assert 'back inside' in result.findings[0].message


def test_unverified_collision_proxy_still_blocks_pass(measured_run, monkeypatch):
    args, _ = measured_run
    monkeypatch.setattr(standing, 'verify_proxies',
                        lambda mesh, proxies, clearance: ({'accepted': False}, mesh_solid(mesh)))
    monkeypatch.setattr(standing, 'simulate', lambda *args: pytest.fail('invalid proxy simulated'))
    result = standing.analyze(args, {'standing': STANDING, 'material': MATERIAL})
    assert result.status == 'INDETERMINATE'
    assert result.summary == 'COLLISION_PROXY_UNVERIFIED'


def test_simulation_error_remains_unverified_through_cli(measured_run, monkeypatch, tmp_path):
    args, _ = measured_run
    def failed_simulation(*args):
        raise ValueError('SIMULATION_UNVERIFIED: diagnostic failure')
    monkeypatch.setattr(standing, 'simulate', failed_simulation)
    physics = tmp_path / 'physics.json'
    physics.write_text(json.dumps({'standing': STANDING, 'material': MATERIAL}))
    monkeypatch.setattr(sys, 'argv', ['standing', '--source', str(args.source),
        '--manifest', str(args.manifest), '--output', str(args.output), '--physics', str(physics)])
    assembly_physics.cli(standing.NAME, standing.analyze)
    result = json.loads((args.output / 'report.json').read_text())
    assert result['status'] == 'INDETERMINATE'
    assert 'SIMULATION_UNVERIFIED' in result['summary']
    assert result['findings'] == []
    assert result['assumptions']['standing_protocol'] == standing.STANDING_PROTOCOL
    assert result['metrics']['standing_observation']['status'] == 'UNAVAILABLE'
    assert result['metrics']['standing_observation']['observed_tipping'] is None


@pytest.mark.parametrize('backend', ['coacd', 'rigid_flex'])
def test_interface_preacceptance_does_not_gate_simulation(measured_run, monkeypatch, backend):
    from adsl.core import assembly_topology
    args, measurement = measured_run
    # A real manifest with a misaligned declared mating frame. Do not edit its
    # mesh to make a compliant interface; verify the observation is still run.
    manifest = json.loads(args.manifest.read_text())
    manifest['connections'][0]['slot_frame'][0][3] += 1.
    args.manifest.write_text(json.dumps(manifest))
    def forbidden(*args, **kwargs):
        pytest.fail('Standing repeated interface geometry acceptance')
    monkeypatch.setattr(assembly_topology, 'interface_measurement', forbidden)
    measurement['initial_minimum_contact_distance_mm'] = -.02
    result = standing.analyze(args, {'standing':dict(STANDING, collision_backend=backend), 'material':MATERIAL})
    assert result.status == 'PASS'
    assert result.metrics['standing_observation']['status'] == 'PASS'
    if backend == 'rigid_flex':
        assert result.metrics['contact_diagnostic']['status'] == 'OK'


def test_partial_observation_is_not_a_standing_pass(measured_run):
    args, measurement = measured_run
    measurement['assessment_time_seconds'] = 2.
    result = standing.analyze(args, {'standing':STANDING, 'material':MATERIAL})
    assert result.status == 'INDETERMINATE'
    assert result.metrics['standing_observation']['duration_s'] is None
    assert result.metrics['standing_observation']['observed_interface_exit'] is None


def test_invalid_body_input_keeps_unavailable_without_simulating(measured_run, monkeypatch):
    args, _ = measured_run
    monkeypatch.setattr(standing, 'load_parts', lambda args: ({},[{'status':'FAIL'}],{}))
    monkeypatch.setattr(standing, 'simulate', lambda *args: pytest.fail('disconnected body simulated'))
    result = standing.analyze(args, {'standing':STANDING, 'material':MATERIAL})
    assert result.status == 'INDETERMINATE'
    assert 'one connected volume' in result.summary
    assert result.metrics['standing_observation']['status'] == 'UNAVAILABLE'


def test_nonpositive_inertia_rejected_before_simulation(measured_run, monkeypatch):
    import numpy as np
    from types import SimpleNamespace
    args, _ = measured_run
    report, _, meshes = standing.load_parts(args)
    class InvalidMass:
        def __init__(self, mesh):
            self.vertices = mesh.vertices
        def copy(self):
            return SimpleNamespace(apply_scale=lambda scale:None, mass=1.,
                center_mass=np.zeros(3), moment_inertia=np.zeros((3,3)))
    invalid={name:InvalidMass(mesh) for name,mesh in meshes.items()}
    with pytest.raises(ValueError,match='INVALID_PHYSICAL_INPUT: invalid mass/inertia'):
        standing.xml_model(report,invalid,{},dict(STANDING,collision_backend='rigid_flex'),1240.)


def test_standing_timeout_has_unknown_observation_and_preserves_reason(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from adsl.agents.models import CheckerResult
    from adsl.agents.checkers import CheckerRun
    source=tmp_path/'source.py';source.write_text('scene=None')
    spec=standing.checker_spec()
    out=tmp_path/'standing';out.mkdir()
    raw=CheckerResult(checker=standing.NAME,status='ERROR',summary='timeout',
        violations=[{'code':'CHECKER_TIMEOUT'}])
    monkeypatch.setattr(assembly_physics,'run_checker',
        lambda *a,**kw:CheckerRun(spec,raw,out,()))
    execution=SimpleNamespace(output_root=tmp_path,glb_path=None,source_index_path=None)
    run=assembly_physics.run_assembly_checks([spec],execution=execution,source=source,
        root=tmp_path,physics={})[0]
    assert run.result.status=='INDETERMINATE'
    assert run.result.violations[0]['code']=='CHECKER_TIMEOUT'
    assert run.result.metrics['standing_observation']['observed_tipping'] is None
    assert run.result.metrics['contact_diagnostic']['status']=='UNAVAILABLE'


@pytest.mark.parametrize('topology_status', ['PASS', 'FAIL', 'INDETERMINATE', 'ERROR'])
def test_batch_calls_standing_independently_and_preserves_other_checkers(tmp_path, monkeypatch, topology_status):
    from types import SimpleNamespace
    from adsl.agents import assembly_topology
    from adsl.agents.models import CheckerSpec, CheckerResult
    from adsl.agents.checkers import CheckerRun
    source=tmp_path/'source.py';source.write_text('scene=None')
    specs=[CheckerSpec(name=n, command=['stub']) for n in
           ('assembly_topology','assembly_standing','assembly_overhang','assembly_fea')]
    called=[]
    def run(spec, **kwargs):
        called.append(spec.name)
        path=tmp_path/'checkers'/spec.name;path.mkdir(parents=True,exist_ok=True)
        status=topology_status if spec.name=='assembly_topology' else 'ERROR' if spec.name=='assembly_standing' else 'PASS'
        result=CheckerResult(checker=spec.name,status=status,summary='stub',
            violations=[{'code':'CHECKER_INFRASTRUCTURE_ERROR'}] if status=='ERROR' else [])
        return CheckerRun(spec,result,path,())
    monkeypatch.setattr(assembly_topology,'run_assembly_topology',run)
    monkeypatch.setattr(assembly_physics,'run_checker',run)
    execution=SimpleNamespace(output_root=tmp_path,glb_path=None,source_index_path=None)
    runs=assembly_physics.run_assembly_checks(specs,execution=execution,source=source,root=tmp_path,physics={})
    assert called==['assembly_topology','assembly_overhang','assembly_standing','assembly_fea']
    result=next(r.result for r in runs if r.spec.name=='assembly_standing')
    assert result.status=='ERROR' and result.metrics['standing_observation']['observed_tipping'] is None
    for run in runs:
        if run.spec.name!='assembly_standing':
            assert 'standing_protocol' not in run.result.assumptions
            assert 'standing_observation' not in run.result.metrics


@pytest.mark.parametrize('variant', [
    'legacy', 'ranked', 'reference_unavailable', 'no_positive',
    'invalid_target', 'duplicate_target', 'too_many_targets',
])
def test_tipping_localization_preserves_identity_and_verdict(measured_run, monkeypatch, variant):
    """Localization only changes valid target hints, including the CLI source path."""
    from adsl.agents.feedback_schema import sha256_file
    args, measurement = measured_run
    measurement.update(tipped=True, peak_tilt_deg=26., initial_minimum_contact_distance_mm=-.04)
    physics = {'standing':dict(STANDING, collision_backend='rigid_flex'), 'material':MATERIAL}
    before = standing.analyze(args, physics)
    original = before.findings[0].model_dump()
    localization = dict(method='initial_directional_gravity_moment_v1', status='RANKED',
        reason_code=None, reason=None, causal_claim=False, root_part_id='base',
        first_tipping_time_s=.24, direction_xy=[1., 0.], support_boundary_m=.1,
        net_moment_nm=.03924, positive_moment_sum_nm=.07848,
        candidates=[dict(part_id='stem', moment_nm=.07848, positive_share=1.)],
        part_contributions=[dict(part_id='stem', mass_kg=.2, com_world_m=[.14,0.,.1],
                                 lever_m=.04, moment_nm=.07848)])
    if variant in ('reference_unavailable', 'no_positive'):
        reference = dict(root_part_id='base', initial_root_rotation=[[1.,0.,0.],[0.,1.,0.],[0.,0.,1.]],
            gravity_m_s2=9.81, parts=[dict(part_id='base', mass_kg=1., com_world_m=[0.,0.,.1])],
            floor_contacts_world_m=[[.1,0.,0.]],
            reference_error='No active initial floor contact was available.' if variant=='reference_unavailable' else None)
        localization = standing._localize_tipping(reference=reference,
            first_tipping=dict(time_seconds=.24,root_rotation=[[0.,0.,1.],[0.,1.,0.],[-1.,0.,0.]]), exits={})
    elif variant == 'invalid_target':
        localization['candidates'][0]['part_id'] = 'not_a_print_part'
    elif variant == 'duplicate_target':
        localization['candidates'] *= 2
    elif variant == 'too_many_targets':
        localization['candidates'] *= 4
    if variant != 'legacy':
        measurement['tipping_localization'] = deepcopy(localization)
    result = standing.analyze(args, physics)
    found = result.findings[0]
    assert result.status == before.status == 'FAIL'
    assert result.summary == before.summary
    assert result.metrics['standing_observation'] == before.metrics['standing_observation']
    assert result.metrics['contact_diagnostic']['status'] == 'WARNING'
    assert found.finding_id == original['finding_id'] == 'assembly_standing:SELF_WEIGHT_TIPPING:base'
    for key in ('rule_id','required','repairability','category','message'):
        assert getattr(found,key) == original[key]
    assert found.region.part_names == (['stem'] if variant=='ranked' else ['base'])
    if variant == 'legacy':
        assert 'tipping_localization' not in result.metrics
        assert 'tipping_localization' not in found.domain
    else:
        diagnostic = result.metrics['tipping_localization']
        assert diagnostic == found.domain['tipping_localization']
        assert diagnostic['causal_claim'] is False
        if variant == 'ranked':
            assert diagnostic == localization
        else:
            assert diagnostic['status'] == 'FALLBACK' and diagnostic['candidates'] == []
            assert diagnostic['reason_code'] == ('NO_POSITIVE_CONTRIBUTION' if variant=='no_positive'
                                                  else 'REFERENCE_UNAVAILABLE')
        json.dumps(diagnostic,allow_nan=False)
    if variant == 'ranked':
        # Synthetic SourceIndex data, consumed by the unchanged real CLI matcher.
        index=args.output/'source_index.json'
        index.write_text(json.dumps(dict(source_sha256=sha256_file(args.source),features=[
            dict(feature_id='stem_hint',name='stem',source_ids=['stem:s0'],
                 source_locations=['source.py:1'],resolution='complete')])))
        configuration=args.output/'physics.json';configuration.write_text(json.dumps(physics))
        monkeypatch.setattr(sys,'argv',['standing','--source',str(args.source),'--manifest',str(args.manifest),
            '--output',str(args.output),'--physics',str(configuration),'--source-index',str(index)])
        assembly_physics.cli(standing.NAME,standing.analyze)
        saved=json.loads((args.output/'report.json').read_text())
        tip=saved['findings'][0]
        assert saved['status']=='FAIL' and tip['region']['part_names']==['stem']
        assert tip['finding_id']==original['finding_id']
        assert [c['feature_id'] for c in tip['source_candidates']]==['stem_hint']
        assert tip['source_candidates'][0]['source_locations']==['source.py:1']
