"""Initial tipping diagnostics with hand data and a fake solver, never native steps."""
import json
import math
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from adsl.agents import assembly_standing as standing


def rotation_y(degrees):
    c, s = math.cos(math.radians(degrees)), math.sin(math.radians(degrees))
    return np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]])


def reference():
    return dict(root_part_id='body', initial_root_rotation=np.eye(3), gravity_m_s2=9.81,
        parts=[dict(part_id='head', mass_kg=.2, com_world_m=np.array([.140, 0., .2])),
               dict(part_id='body', mass_kg=.8, com_world_m=np.array([.095, 0., .1]))],
        floor_contacts_world_m=[np.array([.100, -.01, 0.]), np.array([.100, .01, 0.])],
        reference_error=None)


def first_tip():
    return dict(time_seconds=.24, root_rotation=rotation_y(30))


def test_two_part_moment_ranking():
    initial, event = reference(), first_tip()
    result = standing._localize_tipping(reference=initial, first_tipping=event, exits={})
    assert result['status'] == 'RANKED'
    assert result['reason_code'] is result['reason'] is None
    assert result['causal_claim'] is False
    assert result['direction_xy'] == pytest.approx([1., 0.])
    assert result['support_boundary_m'] == pytest.approx(.100)
    assert [p['moment_nm'] for p in result['part_contributions']] == pytest.approx([.07848, -.03924])
    assert result['net_moment_nm'] == pytest.approx(.03924)
    assert result['positive_moment_sum_nm'] == pytest.approx(.07848)
    assert result['candidates'] == [dict(part_id='head', moment_nm=pytest.approx(.07848), positive_share=1.)]
    total_mass = sum(p['mass_kg'] for p in initial['parts'])
    com_x = sum(p['mass_kg']*p['com_world_m'][0] for p in initial['parts'])/total_mass
    assert result['net_moment_nm'] == pytest.approx(total_mass*9.81*(com_x-.100))
    assert initial['parts'][0]['com_world_m'][0] == .140
    assert event['root_rotation'] == pytest.approx(rotation_y(30))
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('tiny', [False, True])
def test_two_part_moment_ranking_keeps_strict_positive_and_all_positive_denominator(tiny):
    initial = reference()
    initial['floor_contacts_world_m'] = [np.zeros(3)]
    amount = 1e-30 if tiny else .1
    initial['parts'] = [dict(part_id=name, mass_kg=1., com_world_m=[amount, 0., 0.])
                        for name in ('d', 'c', 'b', 'a')]
    # A large restoring root leaves a negative net moment; it does not veto ranking.
    initial['parts'].append(dict(part_id='body', mass_kg=1., com_world_m=[-1., 0., 0.]))
    result = standing._localize_tipping(reference=initial, first_tipping=first_tip(), exits={})
    assert result['status'] == 'RANKED'
    assert [p['part_id'] for p in result['candidates']] == ['a', 'b', 'c']
    assert [p['positive_share'] for p in result['candidates']] == [.25]*3
    assert result['net_moment_nm'] < 0
    assert len(result['part_contributions']) == 5
    json.dumps(result, allow_nan=False)


def contact(geom, flex=(-1, -1), *, active=True, pos=(.1, 0., 0.)):
    return SimpleNamespace(geom=np.array(geom), flex=np.array(flex), elem=np.array([-1, -1]),
        efc_address=0 if active else -1, pos=np.array(pos, dtype=float), dist=0.)


def fake_model_data():
    model = SimpleNamespace(body_mass=np.array([0., .8, .2]), geom_bodyid=np.array([0, 1, 2]),
        opt=SimpleNamespace(gravity=np.array([0., 0., -9.81]), timestep=.02), nflex=1,
        flex_vertadr=np.array([0]), flex_vertbodyid=np.array([2]))
    data = SimpleNamespace(xmat=np.tile(np.eye(3).ravel(), (3, 1)),
        xipos=np.array([[0., 0., 0.], [.095, 0., .1], [.140, 0., .2]]),
        # Distinct body origins must never replace the inertial world centres.
        xpos=np.full((3, 3), 42.), contact=[], time=0., qpos=np.zeros(14), qvel=np.zeros(12))
    return model, data


@pytest.mark.parametrize('floor_on_right', [False, True])
def test_initial_reference_uses_floor_and_world_com(floor_on_right):
    model, data = fake_model_data()
    ordinary = contact((0, 1), pos=(.1, .01, 0.))
    flex = contact((0, -1), (-1, 0), pos=(.1, -.01, 0.))
    if floor_on_right:
        for c in (ordinary, flex):
            c.geom = c.geom[::-1].copy()
            c.flex = c.flex[::-1].copy()
    data.contact = [ordinary, flex,
        contact((1, 2), pos=(10., 0., 0.)),  # Parts, even at z=0, are not the floor.
        contact((0, 2), active=False, pos=(20., 0., 0.)),
        contact((0, -1), (-1, 99), pos=(30., 0., 0.))]  # Unknown flex is not a print part.
    result = standing._initial_tipping_reference(model, data, part_body_ids={'body':1, 'head':2},
        root_part_id='body', floor_geom_id=0, flex_part_ids={0:'head'})
    assert result['reference_error'] is None
    assert result['gravity_m_s2'] == 9.81
    assert [p['mass_kg'] for p in result['parts']] == [.8, .2]
    assert result['parts'][0]['com_world_m'] == pytest.approx([.095, 0., .1])
    assert np.asarray(result['floor_contacts_world_m']) == pytest.approx(np.array([[.1, .01, 0.], [.1, -.01, 0.]]))
    data.xipos[:] = 99.
    data.xmat[:] = 99.
    ordinary.pos[:] = 99.
    model.body_mass[:] = 99.
    assert result['parts'][0]['com_world_m'] == pytest.approx([.095, 0., .1])
    assert result['parts'][0]['mass_kg'] == .8
    assert result['initial_root_rotation'] == pytest.approx(np.eye(3))
    assert result['floor_contacts_world_m'][0] == pytest.approx([.1, .01, 0.])


@pytest.mark.parametrize('invalid', ['missing_mass', 'nonfinite_com', 'horizontal_gravity', 'no_floor'])
def test_initial_reference_uses_floor_and_world_com_unavailable(invalid):
    model, data = fake_model_data()
    data.contact = [contact((0, 1))]
    if invalid == 'missing_mass':
        model.body_mass = np.array([0., .8])
    elif invalid == 'nonfinite_com':
        data.xipos[2, 0] = float('nan')
    elif invalid == 'horizontal_gravity':
        model.opt.gravity[0] = 1.
    else:
        data.contact = []
    initial = standing._initial_tipping_reference(model, data, part_body_ids={'body':1, 'head':2},
        root_part_id='body', floor_geom_id=0, flex_part_ids={0:'head'})
    assert initial['reference_error']
    result = standing._localize_tipping(reference=initial, first_tipping=first_tip(), exits={})
    assert result['reason_code'] == 'REFERENCE_UNAVAILABLE'
    assert result['candidates'] == result['part_contributions'] == []
    json.dumps(result, allow_nan=False)


def test_first_tip_direction_is_not_overwritten(tmp_path, monkeypatch):
    # Import the existing geometry module before replacing its dependency. No
    # connection queries are used by this fake-loop fixture.
    from adsl.core import assembly_topology
    monkeypatch.setitem(sys.modules, 'manifold3d', SimpleNamespace())
    model, data = fake_model_data()
    initial_rotation = np.array([[0., -1., 0.], [1., 0., 0.], [0., 0., 1.]])
    data.xmat[1] = initial_rotation.ravel()
    data.contact = [contact((0, 1))]
    data.ncon = 1
    calls = dict(forward=0, step=0)
    def forward(_model, _data):
        calls['forward'] += 1
    def step(_model, _data):
        calls['step'] += 1
        data.time = calls['step']*.02
        # The larger second sample tips in the opposite direction. Rotations
        # update the same array so storing a view would lose the first event.
        angle = 30. if calls['step'] == 1 else -60.
        data.xmat[1] = (rotation_y(angle) @ initial_rotation).ravel()
        data.xipos[:, 0] = -10.
        data.contact[0].pos[0] = 10.
    fake = SimpleNamespace(MjModel=SimpleNamespace(from_xml_string=lambda _:model),
        MjData=lambda _:data, mj_forward=forward, mj_step=step, FatalError=RuntimeError,
        mjtObj=SimpleNamespace(mjOBJ_BODY='body', mjOBJ_GEOM='geom'),
        mj_name2id=lambda _m, kind, name:0 if kind == 'geom' else {'body':1, 'head':2}[name],
        mj_id2name=lambda _m, _kind, body_id:{1:'body', 2:'head'}[body_id])
    monkeypatch.setitem(sys.modules, 'mujoco', fake)
    config = dict(duration_seconds=.04, tilt_threshold_deg=20., settle_window_seconds=.02,
        settle_linear_m_s=.001, settle_angular_rad_s=.001)
    report = dict(root_id='body', parts=[dict(id='body'), dict(id='head')], connections=[], mm_per_unit=1.)
    result, trajectory = standing.simulate('<fake/>', report, config, tmp_path)
    assert calls == dict(forward=1, step=2)
    assert len(trajectory) == 3
    assert result['tipped'] and result['peak_tilt_deg'] == pytest.approx(60.)
    localization = result['tipping_localization']
    assert localization['first_tipping_time_s'] == .02
    assert localization['direction_xy'] == pytest.approx([1., 0.])
    assert localization['support_boundary_m'] == .1
    assert localization['net_moment_nm'] == pytest.approx(.03924)
    assert [p['part_id'] for p in localization['candidates']] == ['head']
    assert localization['part_contributions'][0]['com_world_m'][0] == .095
    json.dumps(result, allow_nan=False)


@pytest.mark.parametrize('exit_time, ranked', [(.20, False), (.24, False), (.28, True)])
def test_interface_exit_fallback(exit_time, ranked):
    result = standing._localize_tipping(reference=reference(), first_tipping=first_tip(),
        exits={'late':.5, 'joint_1':exit_time})
    assert result['causal_claim'] is False
    if ranked:
        assert result['status'] == 'RANKED'
    else:
        assert result['status'] == 'FALLBACK'
        assert result['reason_code'] == 'INTERFACE_EXIT_FIRST'
        assert 'joint_1' in result['reason']
        assert result['candidates'] == result['part_contributions'] == []
        assert result['net_moment_nm'] is None
    json.dumps(result, allow_nan=False)
