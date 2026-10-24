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
        flex_vertadr=np.array([0]), flex_vertbodyid=np.array([2]),
        body_jntadr=np.array([-1,0,1]), jnt_qposadr=np.array([0,7]), jnt_dofadr=np.array([0,6]))
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
    layout=dict(bodies={n:dict(part_id=n) for n in ('body','head')},
        part_to_components={n:[n] for n in ('body','head')},root_part_id='body',
        root_component_id='body',interfaces={},D_mm=1.)
    result, trajectory = standing.simulate('<fake/>', report, config, tmp_path,body_layout=layout)
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


def _material_trajectory(tmp_path, monkeypatch, angles, *, fail_after=None, time_bias=0., reordered_joints=False,
                         connections=(), unbound=(), update_step=None):
    """A fake MuJoCo driver; optional interface probes use small CPU geometry."""
    from adsl.core import assembly_topology  # Load before substituting the solver.
    model,data=fake_model_data()
    data.ncon=1;data.contact=[contact((0,1))]
    if reordered_joints:
        model.body_jntadr=np.array([-1,1,0])
    calls={'step':0}
    def rotation(value):
        return rotation_y(value) if np.isscalar(value) else np.asarray(value)
    for i,value in enumerate(angles[0],1): data.xmat[i]=rotation(value).ravel()
    def step(_model,_data):
        calls['step']+=1
        if fail_after is not None and calls['step']>fail_after:
            raise RuntimeError('deliberate solver failure after observed event')
        data.time=calls['step']*.02+time_bias
        for i,value in enumerate(angles[calls['step']],1): data.xmat[i]=rotation(value).ravel()
        if update_step is not None: update_step(calls['step'],data)
    fake=SimpleNamespace(MjModel=SimpleNamespace(from_xml_string=lambda _:model),
        MjData=lambda _:data,mj_forward=lambda *a:None,mj_step=step,FatalError=RuntimeError,
        mjtObj=SimpleNamespace(mjOBJ_BODY='body',mjOBJ_GEOM='geom'),
        mj_name2id=lambda _m,kind,name:0 if kind=='geom' else {'body':1,'head':2}[name],
        mj_id2name=lambda _m,_kind,body_id:{1:'body',2:'head'}[body_id])
    monkeypatch.setitem(sys.modules,'mujoco',fake)
    groups={n:[n] for n in ('body','head')} if connections else {'whole':['body','head']}
    root_part='body' if connections else 'whole'
    report=dict(root_id=root_part,parts=[dict(id=n) for n in groups],connections=list(connections),mm_per_unit=1.)
    layout=dict(bodies={n:dict(part_id=p) for p,names in groups.items() for n in names},
        part_to_components=groups,root_part_id=root_part,root_component_id='body',
        interfaces={c['id']:dict(status='UNAVAILABLE' if c['id'] in unbound else 'BOUND',
            tab_component_id='head',slot_component_id='body') for c in connections},D_mm=1.)
    config=dict(duration_seconds=.02*(len(angles)-1),tilt_threshold_deg=25.,
        settle_window_seconds=.02,settle_linear_m_s=.001,settle_angular_rad_s=.001)
    result,trajectory=standing.simulate('<fake/>',report,config,tmp_path,body_layout=layout)
    return result,trajectory,layout


@pytest.mark.parametrize('angles,root_tipped,component_tipped,maximum',[
    ([(0,0),(0,90),(0,0)],False,True,90.),
    ([(0,0),(15,15)],False,False,15.),
    ([(0,0),(24.999999,24.999999)],False,False,24.999999),
    ([(0,0),(25.01,0)],True,True,25.01),
])
def test_all_materials_share_existing_tilt_threshold(tmp_path,monkeypatch,angles,root_tipped,component_tipped,maximum):
    result,trajectory,layout=_material_trajectory(tmp_path,monkeypatch,angles)
    assert result['tipped'] is root_tipped
    assert result['observed_component_tipping'] is component_tipped
    assert result['component_tilt_verified'] is True
    assert result['max_component_tilt_deg']==pytest.approx(maximum)
    assert {r['component_id'] for r in result['component_tilts']}=={'body','head'}
    assert all(r['part_id']=='whole' for r in result['component_tilts'])
    assert len(trajectory)==len(angles)
    assert layout['bodies']['head']['qpos_address']==7
    if component_tipped and not root_tipped:
        findings=standing._component_tipping_findings(result,layout,25.)
        assert len(findings)==1
        assert findings[0].region.part_names==['whole']
        assert findings[0].domain['event_order']=='ROOT_NOT_OBSERVED'
        assert findings[0].domain['first_tipping_seconds']==.02
        assert findings[0].domain['peak_tilt_deg']==pytest.approx(90.)
    json.dumps(result,allow_nan=False)


def test_component_pure_yaw_or_translation_is_not_a_tilt(tmp_path,monkeypatch):
    yaw=np.array([[0.,-1.,0.],[1.,0.,0.],[0.,0.,1.]])
    result,_,_=_material_trajectory(tmp_path,monkeypatch,[(0,0),(0,yaw)])
    assert result['observed_component_tipping'] is False
    assert result['max_component_tilt_deg']==0.
    # The protocol deliberately has no displacement-only failure condition.


def test_known_component_failure_survives_later_solver_error(tmp_path,monkeypatch):
    result,trajectory,layout=_material_trajectory(tmp_path,monkeypatch,
        [(0,0),(0,90),(0,0)],fail_after=1)
    assert result['component_tilt_verified'] is False
    assert result['observed_component_tipping'] is True
    assert result['max_component_tilt_deg']==pytest.approx(90.)
    assert result['assessment_time_seconds']==.02
    assert len(trajectory)==2
    assert result['observation_errors'][0]['reason']=='SIMULATION_UNVERIFIED'
    assert standing._component_tipping_findings(result,layout,25.)[0].rule_id=='SELF_WEIGHT_COMPONENT_TIPPING'


@pytest.mark.parametrize('root_time,when,expected_order',[
    (.1,.02,'BEFORE_ROOT'),(.1,.1,None),(.1,.2,None),
    (.1,None,'UNORDERED'),(None,.02,'UNORDERED'),
])
def test_component_events_filter_before_per_print_part_merge(root_time,when,expected_order):
    from copy import deepcopy
    layout=dict(root_component_id='root',bodies={
        'root':dict(part_id='whole'),'early':dict(part_id='whole'),'late':dict(part_id='whole')})
    result=dict(tipped=True,first_tipping_seconds=root_time,component_tilt_verified=True,
        observed_component_tipping=True,max_component_tilt_deg=90.,component_tilts=[
            dict(component_id='root',part_id='whole',peak_tilt_deg=60.,first_tipping_seconds=root_time),
            dict(component_id='early',part_id='whole',peak_tilt_deg=30.,first_tipping_seconds=when),
            dict(component_id='late',part_id='whole',peak_tilt_deg=90.,first_tipping_seconds=.2)])
    before=deepcopy(result)
    findings=standing._component_tipping_findings(result,layout,25.)
    assert result==before
    if expected_order is None:
        assert findings==[]
    else:
        assert len(findings)==1
        selected=findings[0].domain
        # Unknown root time retains every unorderable component; otherwise the
        # later, higher peak must not replace the earlier retained component.
        assert selected['component_id']==('late' if root_time is None else 'early')
        assert selected['peak_tilt_deg']==(90. if root_time is None else 30.)
        assert selected['event_order']==expected_order


def test_component_completion_uses_existing_time_rounding(tmp_path,monkeypatch):
    result,_,_=_material_trajectory(tmp_path,monkeypatch,[(0,0),(0,0)],time_bias=-1e-14)
    assert result['assessment_time_seconds']==.02
    assert result['component_tilt_verified'] is True
    assert result['observed_component_tipping'] is False


def test_material_joint_addresses_do_not_follow_body_iteration_order(tmp_path,monkeypatch):
    result,_,layout=_material_trajectory(tmp_path,monkeypatch,[(0,0),(0,90)],reordered_joints=True)
    assert layout['bodies']['body']['qpos_address']==7
    assert layout['bodies']['head']['qpos_address']==0
    assert layout['bodies']['body']['dof_address']==6
    assert layout['bodies']['head']['dof_address']==0
    assert result['peak_tilt_deg']==0.
    assert result['observed_component_tipping'] is True



def _observation_connection(connection_id='joint'):
    return dict(id=connection_id,tab_part='head',slot_part='body',
        tab_frame=np.eye(4).tolist(),slot_frame=np.eye(4).tolist(),
        parameters=dict(width_mm=1.,thickness_mm=1.,insertion_mm=1.,slot_depth_mm=2.,fit_offset_mm=.1))


def _consume_fake_observation(result,trajectory,layout,connections,tmp_path,monkeypatch):
    """Use the actual verdict consumer for measurements from the fake solver loop."""
    from test_assembly_physics import STANDING, MATERIAL
    report=dict(root_id=layout['root_part_id'],mm_per_unit=1.,
        parts=[dict(id=p) for p in layout['part_to_components']],connections=connections)
    meshes={p:object() for p in layout['part_to_components']}
    monkeypatch.setattr(standing,'load_parts',lambda args:(report,
        [dict(part_id=p,status='PASS') for p in meshes],meshes))
    monkeypatch.setattr(standing,'_validate_physical_inputs',lambda *a:None)
    monkeypatch.setattr(standing,'_prepare_simulation_bodies',lambda *a:layout)
    monkeypatch.setattr(standing,'flex_contact_settings',lambda *a:None)
    monkeypatch.setattr(standing,'xml_model',lambda *a,**kw:'<fake/>')
    monkeypatch.setattr(standing,'simulate',lambda *a,**kw:(result,trajectory))
    monkeypatch.setattr(standing,'save_frames',lambda *a,**kw:[])
    monkeypatch.setattr(standing.importlib.metadata,'version',lambda name:'fake-observation-test')
    config=dict(STANDING,collision_backend='rigid_flex',
        duration_seconds=result['observation_duration_seconds'],settle_window_seconds=.02)
    return standing.analyze(SimpleNamespace(output=tmp_path),{'standing':config,'material':MATERIAL})


@pytest.mark.parametrize('error_type',[ValueError,RuntimeError,np.linalg.LinAlgError])
@pytest.mark.parametrize('tipped',[False,True])
def test_interface_initial_query_failure_keeps_other_observations(tmp_path,monkeypatch,error_type,tipped):
    from adsl.core import assembly_topology
    original=assembly_topology.query_solids
    calls=[]
    def query(parameters):
        calls.append(parameters)
        if len(calls)==1: raise error_type('first interface initialization unavailable')
        return original(parameters)
    monkeypatch.setattr(assembly_topology,'query_solids',query)
    connections=[_observation_connection('broken'),_observation_connection('working')]
    result,trajectory,layout=_material_trajectory(tmp_path,monkeypatch,
        [(0,0),(0,90 if tipped else 0),(0,0)],connections=connections)
    assert len(calls)==2
    assert len(trajectory)==3
    assert result['component_tilt_verified'] is True
    assert result['interface_retention_verified'] is False
    assert result['observation_errors']==[dict(connection_id='broken',
        reason='INTERFACE_OBSERVATION_UNAVAILABLE',occurrences=1,
        detail='first interface initialization unavailable')]
    for row in trajectory:
        assert row['interfaces'][0]==dict(connection_id='broken',status='UNAVAILABLE',exited=None)
        assert row['interfaces'][1]['status']=='PASS'
        assert row['component_tilts']['body']==0.
    checked=_consume_fake_observation(result,trajectory,layout,connections,tmp_path,monkeypatch)
    if tipped:
        assert checked.status=='FAIL'
        assert 'SELF_WEIGHT_COMPONENT_TIPPING' in [f.rule_id for f in checked.findings]
    else:
        assert checked.status=='INDETERMINATE'
        assert checked.findings==[]


def test_interface_initial_cavity_failure_does_not_register_partial_query(tmp_path,monkeypatch):
    from adsl.core import assembly_topology
    # simulate() imports this local factory; the topology query helper retains
    # its real module, so failure occurs after frames and the tab were built.
    actual=assembly_topology.mf.Manifold
    calls=[]
    def cube(*args,**kwargs):
        calls.append(args)
        if len(calls)==1: raise RuntimeError('cavity construction unavailable')
        return actual.cube(*args,**kwargs)
    monkeypatch.setitem(sys.modules,'manifold3d',SimpleNamespace(**(vars(assembly_topology.mf) |
        {'Manifold':SimpleNamespace(cube=cube)})))
    connections=[_observation_connection('broken'),_observation_connection('working')]
    result,trajectory,_=_material_trajectory(tmp_path,monkeypatch,[(0,0),(0,0)],connections=connections)
    assert len(calls)==2
    assert len(trajectory)==2
    assert result['observation_errors']==[dict(connection_id='broken',
        reason='INTERFACE_OBSERVATION_UNAVAILABLE',occurrences=1,detail='cavity construction unavailable')]
    assert all(row['interfaces'][0]['status']=='UNAVAILABLE' for row in trajectory)
    assert all(row['interfaces'][1]['status']=='PASS' for row in trajectory)
    assert result['interface_retention_verified'] is False


def test_mid_observation_error_preserves_known_events_and_recovery(tmp_path,monkeypatch):
    from adsl.core import assembly_topology
    original=assembly_topology.solid_mesh
    state={'step':0}
    def update(step,data):
        state['step']=step
        if step==1: data.xpos[2,2]+=.1  # A real declared query exits before the missing sample.
        if step==3: data.xpos[2,2]-=.1
    def solid_mesh(value):
        if state['step']==2: raise RuntimeError('mid-observation query unavailable')
        return original(value)
    monkeypatch.setattr(assembly_topology,'solid_mesh',solid_mesh)
    connections=[_observation_connection()]
    result,trajectory,layout=_material_trajectory(tmp_path,monkeypatch,
        [(0,0),(0,90),(0,0),(0,0)],connections=connections,update_step=update)
    assert len(trajectory)==4
    assert result['exits']=={'joint':.02}
    assert result['max_component_tilt_deg']==pytest.approx(90.)
    assert trajectory[1]['interfaces'][0]['exited'] is True
    assert trajectory[2]['interfaces'][0]['status']=='UNAVAILABLE'
    assert trajectory[3]['interfaces'][0]['status']=='PASS'
    assert trajectory[3]['interfaces'][0]['exited'] is False
    assert result['final_exited_interfaces']==[]
    assert result['interface_retention_verified'] is False
    assert result['observation_errors']==[dict(connection_id='joint',
        reason='INTERFACE_OBSERVATION_UNAVAILABLE',occurrences=1,detail='mid-observation query unavailable')]
    stored=json.loads((tmp_path/'trajectory.json').read_text())
    assert stored==trajectory
    checked=_consume_fake_observation(result,trajectory,layout,connections,tmp_path,monkeypatch)
    assert checked.status=='FAIL'
    assert {f.rule_id for f in checked.findings}=={'SELF_WEIGHT_COMPONENT_TIPPING','SELF_WEIGHT_INTERFACE_EXIT'}


@pytest.mark.parametrize('error_type',[ValueError,RuntimeError,np.linalg.LinAlgError])
def test_repeated_interface_errors_are_counted_and_never_verify_recovered_observation(tmp_path,monkeypatch,error_type):
    from adsl.core import assembly_topology
    original=assembly_topology.solid_mesh
    state={'step':0,'errors':0}
    first='first detail '+('x'*300)
    def update(step,data): state['step']=step
    def solid_mesh(value):
        if state['step'] in (1,2):
            state['errors']+=1
            raise error_type(first if state['errors']==1 else 'later detail must not replace first')
        return original(value)
    monkeypatch.setattr(assembly_topology,'solid_mesh',solid_mesh)
    connections=[_observation_connection()]
    result,trajectory,layout=_material_trajectory(tmp_path,monkeypatch,
        [(0,0)]*4,connections=connections,update_step=update)
    assert state['errors']==2
    assert [row['interfaces'][0]['status'] for row in trajectory]==['PASS','UNAVAILABLE','UNAVAILABLE','PASS']
    assert result['observation_errors']==[dict(connection_id='joint',
        reason='INTERFACE_OBSERVATION_UNAVAILABLE',occurrences=2,detail=first[:240])]
    assert result['interface_retention_verified'] is False
    assert result['component_tilt_verified'] is True
    assert result['final_interfaces'][0]['status']=='PASS'
    checked=_consume_fake_observation(result,trajectory,layout,connections,tmp_path,monkeypatch)
    assert checked.status=='INDETERMINATE'
    assert checked.findings==[]


def test_repeated_component_rotation_unavailable_is_counted_and_stays_incomplete(tmp_path,monkeypatch):
    invalid=np.full((3,3),np.nan)
    result,trajectory,_=_material_trajectory(tmp_path,monkeypatch,
        [(0,0),(0,invalid),(0,invalid),(0,0)])
    assert result['observation_errors']==[dict(component_id='head',
        reason='COMPONENT_ROTATION_UNAVAILABLE',occurrences=2)]
    assert [row['component_tilts']['head'] for row in trajectory]==[0.,None,None,0.]
    assert result['component_tilt_verified'] is False
    assert result['observed_component_tipping'] is None
    assert result['interface_retention_verified'] is False
    assert result['component_tilts'][1]['final_tilt_deg']==0.


def test_unbound_interface_and_same_named_component_have_separate_error_counts(tmp_path,monkeypatch):
    invalid=np.full((3,3),np.nan)
    result,trajectory,_=_material_trajectory(tmp_path,monkeypatch,
        [(0,0),(0,invalid),(0,invalid)],connections=[_observation_connection('head')],unbound=('head',))
    assert result['observation_errors']==[
        dict(connection_id='head',reason='INTERFACE_COMPONENT_UNAVAILABLE',occurrences=1),
        dict(component_id='head',reason='COMPONENT_ROTATION_UNAVAILABLE',occurrences=2)]
    assert all(row['interfaces'][0]['status']=='UNAVAILABLE' for row in trajectory)
    assert result['interface_retention_verified'] is False
    assert result['component_tilt_verified'] is False


@pytest.mark.parametrize('stage',['initial','frame'])
@pytest.mark.parametrize('error_type',[TypeError,KeyError])
def test_interface_query_program_errors_propagate(tmp_path,monkeypatch,stage,error_type):
    from adsl.core import assembly_topology
    def error(*args): raise error_type('deliberate program error')
    monkeypatch.setattr(assembly_topology,'query_solids' if stage=='initial' else 'solid_mesh',error)
    with pytest.raises(error_type,match='deliberate program error'):
        _material_trajectory(tmp_path,monkeypatch,[(0,0),(0,0)],connections=[_observation_connection()])
