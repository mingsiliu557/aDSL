"""Ordered multi-interface API contracts; real CSG checks are explicitly opt-in."""
from copy import deepcopy
from dataclasses import asdict
import importlib
import math
from pathlib import Path

import numpy as np
import pytest

from adsl.core import Cube, FixedAssembly, InterfaceFrame, TabSlot
from adsl.agents.models import FixedAssemblyPlan

REPO = Path(__file__).resolve().parents[1]
PARTS = ('left_side', 'right_side', 'upper_shelf', 'lower_shelf')
CONNECTIONS = (
    ('upper_left', 'upper_shelf', 'left_tab', 'left_side', 'upper_slot', 70, -1),
    ('upper_right', 'upper_shelf', 'right_tab', 'right_side', 'upper_slot', 70, 1),
    ('lower_left', 'lower_shelf', 'left_tab', 'left_side', 'lower_slot', 30, -1),
    ('lower_right', 'lower_shelf', 'right_tab', 'right_side', 'lower_slot', 30, 1),
)
CONFIG = dict(mm_per_unit=1., fit_offset_mm=.2, final_size_mm=[132., 60., 100.])


def shelf_plan_data():
    return dict(object_name='Two shelf frame', components=[dict(name=n, description=n) for n in PARTS],
        relations=['Both shelves connect to both side panels'], critic_checklist=['Four separate parts and four interfaces'],
        print_parts=[dict(id=n, components=[n]) for n in PARTS], root_part='left_side',
        connections=[dict(id=cid, tab_part=tab, tab_port=tp, slot_part=slot, slot_port=sp,
            parameter_name='shelf_joint', interface_type='tab_slot', insertion_direction='-X' if sign < 0 else '+X',
            fit_intent='Frozen positive clearance of 0.2 mm') for cid,tab,tp,slot,sp,z,sign in CONNECTIONS], **CONFIG)


def shelf_parts(**kwargs):
    a = FixedAssembly(root_id='left_side', mm_per_unit=kwargs.pop('mm_per_unit', 1), **kwargs)
    for name in PARTS:
        body = Cube((12,60,100), center=(0,0,50)) if name.endswith('side') else Cube((108,50,8))
        a.add_part(name, body, components=(name,))
    return a


def shelf_kwargs(index):
    cid, tab, tp, slot, sp, z, sign = CONNECTIONS[index]
    return dict(tab_part=tab, slot_part=slot, tab_port=tp, slot_port=sp, parameter_name='shelf_joint',
        parameters=TabSlot(width_mm=16, thickness_mm=4, insertion_mm=4, slot_depth_mm=5, fit_offset_mm=.2),
        tab_frame=InterfaceFrame((54*sign,0,0),(0,1,0),(sign,0,0)),
        slot_frame=InterfaceFrame((-6*sign,0,z),(0,1,0),(sign,0,0)))


def shelves(count=4, **kwargs):
    a = shelf_parts(**kwargs)
    for i in range(count):
        a.connect(CONNECTIONS[i][0], **shelf_kwargs(i))
    return a


def pair():
    a = FixedAssembly(root_id='crossbar', mm_per_unit=1)
    a.add_part('crossbar', Cube((60,20,12), center=(0,0,6)), components=('crossbar',))
    a.add_part('stem', Cube((40,12,50), center=(0,0,-25)), components=('stem',))
    for suffix, x in [('left',-10), ('right',10)]:
        a.connect(suffix, tab_part='stem', slot_part='crossbar',
            tab_port=f'{suffix}_tab', slot_port=f'{suffix}_slot',
            tab_frame=InterfaceFrame((x,0,0)), slot_frame=InterfaceFrame((x,0,0)), parameter_name='pair_joint',
            parameters=TabSlot(width_mm=8, thickness_mm=6, insertion_mm=6, slot_depth_mm=7, fit_offset_mm=.2))
    return a


def snapshot(a):
    nodes = []
    def walk(node):
        nodes.append((node, node._parent, dict(node._children)))
        for child in node._children.values():
            walk(child)
    for part in a.parts.values():
        walk(part)
    return dict(parts=a.parts.copy(), transforms={n:m.copy() for n,m in a.transforms.items()},
                connections=list(a.connections), nodes=nodes)


def assert_unchanged(a, before):
    assert a.parts == before['parts']
    assert a.connections == before['connections']
    assert set(a.transforms) == set(before['transforms'])
    for name, matrix in before['transforms'].items():
        np.testing.assert_array_equal(a.transforms[name], matrix)
    for node, parent, children in before['nodes']:
        assert node._parent is parent and node._children == children


def test_four_parts_four_connections_and_fixed_closure_poses():
    assert len(FixedAssemblyPlan.model_validate(shelf_plan_data()).connections) == 4
    a = shelves(3)
    before = {n:m.copy() for n,m in a.transforms.items()}
    a.connect('lower_right', **shelf_kwargs(3))
    a.validate()
    for name, xyz in zip(PARTS, [(0,0,0),(120,0,0),(60,0,70),(60,0,30)]):
        expected = np.eye(4); expected[:3,3] = xyz
        np.testing.assert_allclose(a.transforms[name], expected, rtol=0, atol=1e-12)
        np.testing.assert_array_equal(a.transforms[name], before[name])


def test_reverse_placement_with_rotated_root_and_nonunit_scale():
    phi = math.pi / 5
    root = InterfaceFrame((2,3,5), (math.cos(phi),math.sin(phi),0))
    a = shelves(root_frame=root, mm_per_unit=2.5)
    a.validate()
    for name, xyz in zip(PARTS, [(0,0,0),(120,0,0),(60,0,70),(60,0,30)]):
        local = np.eye(4); local[:3,3] = xyz
        np.testing.assert_allclose(a.transforms[name], root.matrix() @ local, rtol=0, atol=1e-12)
    from test_fixed_assembly import plan_data
    data = plan_data(); data['connections'][0].update(tab_part='crossbar', slot_part='stem')
    assert FixedAssemblyPlan.model_validate(data).connections[0].slot_part == 'stem'


def test_same_pair_and_interleaved_extra_connection():
    a = pair(); a.validate()
    assert [c['id'] for c in a.connections] == ['left', 'right']
    # Both existing interfaces precede placement of the final new part.
    a.add_part('extra', Cube(10), components=('extra',))
    a.connect('last', tab_part='extra', slot_part='stem', tab_frame=InterfaceFrame(),
        slot_frame=InterfaceFrame(), slot_port='extra_slot', parameter_name='pair_joint',
        parameters=TabSlot(**a.connections[0]['parameters']))
    a.validate()
    d = dict(object_name='interleaved', components=[dict(name=n,description=n) for n in a.parts],
        relations=[], critic_checklist=[], print_parts=[dict(id=n,components=[n]) for n in a.parts],
        root_part=a.root_id, **CONFIG, connections=[{k:c[k] for k in
        ('id','tab_part','slot_part','tab_port','slot_port','parameter_name')} | dict(
            interface_type='tab_slot', insertion_direction='+Z', fit_intent='clearance') for c in a.connections])
    assert len(FixedAssemblyPlan.model_validate(d).connections) == 3


@pytest.mark.parametrize('bad', ['unplaced','disconnected','unknown','self','duplicate','tab_port','slot_port'])
def test_plan_rejects_invalid_graph_or_occupied_ports(bad):
    d = shelf_plan_data()
    if bad == 'unplaced': d['connections'] = [d['connections'][1], *d['connections'][:1], *d['connections'][2:]]
    if bad == 'disconnected': d['connections'] = d['connections'][:2]
    if bad == 'unknown': d['connections'][0]['tab_part'] = 'missing'
    if bad == 'self': d['connections'][0]['tab_part'] = 'left_side'
    if bad == 'duplicate': d['connections'][3]['id'] = 'upper_left'
    if bad == 'tab_port': d['connections'][1]['tab_port'] = 'left_tab'
    if bad == 'slot_port': d['connections'][2]['slot_port'] = 'upper_slot'
    with pytest.raises(ValueError): FixedAssemblyPlan.model_validate(d)


@pytest.mark.parametrize('bad', ['unplaced','unknown','self','duplicate','tab_port','slot_port','parameters','frame'])
def test_rejected_connect_is_atomic_and_identifies_interface(bad):
    a = shelves(1); cid = 'upper_right'; kw = shelf_kwargs(1)
    if bad == 'unplaced': cid = 'lower_right'; kw = shelf_kwargs(3)
    if bad == 'unknown': kw['slot_part'] = 'missing'
    if bad == 'self': kw['slot_part'] = kw['tab_part']
    if bad == 'duplicate': cid = 'upper_left'
    if bad == 'tab_port': kw['tab_port'] = 'left_tab'
    if bad == 'slot_port': kw = shelf_kwargs(2); kw['slot_port'] = 'upper_slot'
    if bad == 'parameters': kw['parameters'] = TabSlot(**(asdict(kw['parameters']) | {'width_mm':17}))
    if bad == 'frame': kw['tab_frame'] = InterfaceFrame(x_axis=(2,0,0))
    before = snapshot(a)
    with pytest.raises(ValueError, match=None if bad == 'frame' else cid): a.connect(cid, **kw)
    assert_unchanged(a, before)


@pytest.mark.parametrize('kind', ['translation','rotation'])
def test_inconsistent_closure_diagnostics_and_no_mutation(kind):
    a = shelves(3); kw = shelf_kwargs(3)
    if kind == 'translation': kw['slot_frame'] = InterfaceFrame((-5,0,30),(0,1,0),(1,0,0))
    else: kw['slot_frame'] = InterfaceFrame((-6,0,30),(0,0,1),(1,0,0))
    before = snapshot(a)
    with pytest.raises(ValueError, match=r'MATE_FRAME_MISMATCH: interface=lower_right tab=lower_shelf slot=right_side translation_error_mm=.* rotation_error_deg='):
        a.connect('lower_right', **kw)
    assert_unchanged(a, before)


@pytest.mark.parametrize('index', [0,1,3])
def test_second_boolean_failure_preserves_saved_geometry_parents_and_state(monkeypatch, index):
    a = shelves(index); before = snapshot(a)
    module = importlib.import_module('adsl.core.assembly')
    original = module.boolean_difference
    def fail_after_attach(*args):
        original(*args)
        raise RuntimeError('second Boolean failed after attaching children')
    monkeypatch.setattr(module, 'boolean_difference', fail_after_attach)
    with pytest.raises(RuntimeError, match='second Boolean'):
        a.connect(CONNECTIONS[index][0], **shelf_kwargs(index))
    assert_unchanged(a, before)


@pytest.mark.parametrize('bad', ['frame','missing_transform','extra_transform','missing_root','order','disconnected','duplicate','unknown','self','tab_port','slot_port','parameters'])
def test_validate_replays_graph_and_rechecks_saved_constraints(bad):
    a = shelves()
    if bad == 'frame': a.transforms['right_side'][0,3] += .1
    if bad == 'missing_transform': del a.transforms['right_side']
    if bad == 'extra_transform': a.transforms['extra'] = np.eye(4)
    if bad == 'missing_root': a.root_id = 'missing'
    if bad == 'order': a.connections[0], a.connections[1] = a.connections[1], a.connections[0]
    if bad == 'disconnected': a.connections = a.connections[:2]
    if bad == 'duplicate': a.connections[3]['id'] = 'upper_left'
    if bad == 'unknown': a.connections[3]['tab_part'] = 'missing'
    if bad == 'self': a.connections[3]['tab_part'] = 'right_side'
    if bad == 'tab_port': a.connections[3]['tab_port'] = 'left_tab'
    if bad == 'slot_port': a.connections[3]['slot_port'] = 'upper_slot'
    if bad == 'parameters': a.connections[3]['parameters']['width_mm'] += 1
    with pytest.raises(ValueError): a.validate()


def test_ports_are_scoped_by_role_and_single_part_remains_valid():
    a = FixedAssembly(root_id='a', mm_per_unit=1)
    a.add_part('a', Cube(10), components=('a',)); a.validate()
    a.add_part('b', Cube(10), components=('b',))
    p = TabSlot(width_mm=2, thickness_mm=2, insertion_mm=2, slot_depth_mm=3, fit_offset_mm=.2)
    for cid, tab, slot in [('first','b','a'), ('second','a','b')]:
        a.connect(cid, tab_part=tab, slot_part=slot, tab_port='same', slot_port='same',
            tab_frame=InterfaceFrame(), slot_frame=InterfaceFrame(), parameters=p, parameter_name='joint')
    a.validate()


def test_shared_frame_tolerance_is_scene_matrix_tolerance_with_mm_diagnostics():
    from adsl.core.assembly import _check_world_frames
    tab = np.eye(4); slot = np.eye(4); slot[0,3] = 5e-9
    r = _check_world_frames(tab, slot, 1000)
    assert r['matched'] and r['translation_error_mm'] == pytest.approx(5e-6)
    slot[0,3] = 2e-8
    assert not _check_world_frames(tab, slot, .001)['matched']
    slot = InterfaceFrame(x_axis=(0,1,0)).matrix()
    assert _check_world_frames(tab, slot, 1)['rotation_error_deg'] == pytest.approx(90)
    tab[0,0] += 1e-15
    assert math.isfinite(_check_world_frames(tab, tab, 1)['rotation_error_deg'])
