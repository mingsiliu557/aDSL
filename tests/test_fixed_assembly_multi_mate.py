"""Ordered multi-interface API contracts; real CSG checks are explicitly opt-in."""
from dataclasses import asdict, replace
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


def test_standing_xml_consumes_four_parts_as_independent_bodies():
    # Manifest/body wiring only: XML builder does not read connections and these
    # simple box proxies do not certify physical collision geometry or stability.
    import xml.etree.ElementTree as ET
    import trimesh
    from adsl.agents.assembly_standing import xml_model
    a = shelves()
    report = dict(mm_per_unit=1, root_id=a.root_id,
        parts=[dict(id=n, assembly_transform=a.transforms[n].tolist()) for n in PARTS],
        connections=[{k:v for k,v in c.items() if k not in ('tab_solid','slot_cutter')} for c in a.connections])
    meshes = {n:trimesh.creation.box((12,60,100) if n.endswith('side') else (108,50,8)) for n in PARTS}
    xml = xml_model(report, meshes, {n:[m] for n,m in meshes.items()},
        dict(timestep_seconds=.002, friction=[.5,.005,.0001]), 700)
    model = ET.fromstring(xml)
    assert {b.get('name') for b in model.findall('./worldbody/body')} == set(PARTS)
    assert {j.get('name') for j in model.findall('./worldbody/body/freejoint')} == {f'free_{n}' for n in PARTS}
    assert not model.findall('.//weld')


real_geometry = pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_FIXED_REAL') != '1',
    reason='explicit real Boolean export and topology validation only')


def export_and_check_topology(tmp_path, source_text, config, expected_ids, expected_parts):
    import json
    from adsl.agents.utils.execution import execute_asset_source
    from adsl.agents.assembly_topology import checker_spec, run_assembly_topology
    source = tmp_path/'source.py'; source.write_text(source_text)
    execution = execute_asset_source(source, tmp_path/'output', render=False, export_urdf=False, fixed_assembly=config)
    assembly_dir = execution.output_root/'assembly'
    report = json.loads((assembly_dir/'assembly_manifest.json').read_text())
    assert len(report['parts']) == len(expected_parts) and {p['id'] for p in report['parts']} == set(expected_parts)
    assert [c['id'] for c in report['connections']] == expected_ids
    for part in report['parts']:
        for field in ('stl', 'glb'):
            assert (assembly_dir/part[field]).is_file()
    assert (assembly_dir/'scene.glb').is_file() and (assembly_dir/'exploded.glb').is_file()
    visual_only = config.get('validation_mode') == 'visual_only'
    if visual_only:
        assert report['status'] == report['geometry_validation'] == 'NOT_EVALUATED'
        assert report['export_status'] == 'PASS'
    else:
        assert report['status'] == 'PASS', report['failures']
        np.testing.assert_allclose(report['assembled_size_mm'], config['final_size_mm'], rtol=0, atol=1e-5)
        assert [r['id'] for r in report['interfaces']] == expected_ids
        # Actual evaluated geometry, including each supplemental tab and cutter.
        for row in report['interfaces']:
            assert min(row[k] for k in ('added_tab_mm3','removed_slot_mm3','embedded_root_mm3')) > 0
        assert not report['failures']  # Includes final file/scene geometry consistency.
    # Same asset_dir selection as iterate_fixed_assembly's checker invocation.
    topology_execution = replace(execution, glb_path=assembly_dir/'scene.glb')
    run = run_assembly_topology(checker_spec(), execution=topology_execution, source=source, root=tmp_path/'topology')
    assert run.result.status == 'PASS', run.result.model_dump()
    measurements = json.loads((run.output_dir/'report.json').read_text())
    parts = [r for r in measurements['items'] if r['kind'] == 'part']
    interfaces = [r for r in measurements['items'] if r['kind'] == 'interface']
    assert len(parts) == len(expected_parts) and {r['part_id'] for r in parts} == set(expected_parts)
    assert all(r['status'] == 'PASS' and r['component_count'] == 1 for r in parts)
    pairs = [r for r in measurements['items'] if r['kind'] == 'pair']
    from itertools import combinations
    assert len(pairs) == len(expected_parts) * (len(expected_parts) - 1) // 2
    assert {tuple(r['part_ids']) for r in pairs} == set(combinations(sorted(expected_parts), 2))
    assert all(r['status'] == 'PASS' and r['undeclared_interference_mm3'] <= r['volume_tolerance_mm3'] for r in pairs)
    assert [r['connection_id'] for r in interfaces] == expected_ids
    for row in interfaces:
        assert row['status'] == 'PASS' and not row['failures']
        assert row['missing_tab_mm3'] <= row['volume_tolerance_mm3']
        assert row['occupied_cavity_mm3'] <= row['volume_tolerance_mm3']
        assert row['effective_insertion_interval_mm'][1] == pytest.approx(row['expected_insertion_mm'], abs=row['length_tolerance_mm'])
        assert row['root_connection'] == 'CONNECTED_TO_SAME_MATERIAL_COMPONENT'
    # Independent topology does not rewrite visual_only into geometric PASS.
    assert json.loads((assembly_dir/'assembly_manifest.json').read_text()) == report
    return report


@real_geometry
def test_real_four_part_cycle_geometry_and_topology(tmp_path):
    export_and_check_topology(tmp_path, (REPO/'examples/fixed_assembly/two_shelf_frame.py').read_text(),
        {**CONFIG, 'validation_mode':'geometry', 'assembly_plan':FixedAssemblyPlan.model_validate(shelf_plan_data()).model_dump()},
        [c[0] for c in CONNECTIONS], PARTS)


@real_geometry
def test_real_same_pair_two_interfaces_geometry_and_topology(tmp_path):
    import inspect
    source = 'from adsl.core import *\n\n' + inspect.getsource(pair) + '\nassembly = pair()\nscene = assembly.scene()\n'
    export_and_check_topology(tmp_path, source,
        dict(mm_per_unit=1., fit_offset_mm=.2, final_size_mm=[60.,20.,62.], validation_mode='geometry'),
        ['left','right'], ['crossbar','stem'])


@real_geometry
def test_real_four_part_cycle_visual_only_and_topology(tmp_path):
    export_and_check_topology(tmp_path, (REPO/'examples/fixed_assembly/two_shelf_frame.py').read_text(),
        {**CONFIG, 'validation_mode':'visual_only', 'assembly_plan':FixedAssemblyPlan.model_validate(shelf_plan_data()).model_dump()},
        [c[0] for c in CONNECTIONS], PARTS)


@real_geometry
@pytest.mark.parametrize('frame', ['((-5,0,30), (0,1,0), (1,0,0))','((-6,0,30), (0,0,1), (1,0,0))'])
def test_real_four_part_inconsistent_closure_rejected_before_export(tmp_path, frame):
    from adsl.agents.utils.execution import execute_asset_source, AssetExecutionError
    source = tmp_path/'source.py'
    source.write_text((REPO/'examples/fixed_assembly/two_shelf_frame.py').read_text().replace(
        '((-6,0,30), (0,1,0), (1,0,0))', frame))
    with pytest.raises(AssetExecutionError, match='MATE_FRAME_MISMATCH: interface=lower_right'):
        execute_asset_source(source, tmp_path/'output', render=False, export_urdf=False, fixed_assembly=CONFIG)
    assert not list((tmp_path/'output').rglob('*.stl'))
    assert not list((tmp_path/'output').rglob('assembly_manifest.json'))
