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
