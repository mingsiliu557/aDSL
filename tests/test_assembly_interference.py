"""Actual Manifold material intersections; no physics or LLM calls."""
from copy import deepcopy
from dataclasses import asdict

import manifold3d as mf
import numpy as np
import pytest

from adsl.core.assembly import TabSlot
from adsl.core.assembly_topology import (checked, material_interference, pair_interference_measurement,
    part_measurement, solid_mesh, query_solids, mm_matrix, interface_measurement)
from test_assembly_topology import fixture_pair


def measure_pair(parts, solids, connections=(), mm_per_unit=1.):
    rows=[part_measurement(solid_mesh(s),n)[0] for n,s in solids.items()]
    length=max(r['length_tolerance_mm'] for r in rows)
    volume=max(r['surface_area_mm2'] for r in rows)*length
    a,b=sorted(parts)
    return pair_interference_measurement(a,b,parts,solids,connections,mm_per_unit,
        length_tolerance_mm=length,volume_tolerance_mm3=volume)


def collision_pair():
    c,parts,solids=fixture_pair()
    # Connected to stem, outside the complete local tab/slot query region.
    tab,_=query_solids(TabSlot(**c['parameters']))
    # Keep the parent body 0.25 mm below the stop plane to isolate the
    # collision block's bounds from numerical remnants of coplanar shoulders.
    body=mf.Manifold.cube((20,12,50),True).translate((0,0,-25.25))
    solids['stem']=checked(body+tab+mf.Manifold.cube((3,4,4),True).translate((8.5,0,0)))
    return c,parts,solids


@pytest.mark.parametrize('offset,method',[(3,'aabb_disjoint'),(2,'aabb_disjoint'),(0,'manifold_intersection')])
def test_interference_separation_touch_and_empty_material(offset,method):
    a=mf.Manifold.cube((2,2,2),True)
    b=a.translate((offset,0,0)) if offset else mf.Manifold.cube((4,4,4),True)-a
    row=material_interference(a,b,volume_tolerance_mm3=.0001)
    assert row['status']=='PASS' and row['undeclared_interference_mm3']==0
    assert row['bounds_mm'] is None and row['method']==method


@pytest.mark.parametrize('fraction,status',[(.2,'PASS'),(5.,'FAIL')])
def test_interference_numeric_volume_bound(fraction,status):
    a=mf.Manifold.cube((2,2,2),True)
    measured,_=part_measurement(solid_mesh(a),'a')
    tolerance=measured['surface_area_mm2']*measured['length_tolerance_mm']
    b=a.translate((2-fraction*tolerance/4,0,0))
    row=material_interference(a,b,volume_tolerance_mm3=tolerance)
    assert row['status']==status
    assert row['raw_intersection_mm3']==pytest.approx(fraction*tolerance,rel=1e-5)


@pytest.mark.parametrize('fit',[.2,-.1])
def test_interference_tab_slot_fit_exemption(fit):
    c,parts,solids=fixture_pair(fit)
    before={n:s.volume() for n,s in solids.items()}
    row=measure_pair(parts,solids,[c])
    assert row['status']=='PASS' and row['undeclared_interference_mm3']==pytest.approx(0)
    assert row['allowed_fit_connection_ids']==(['joint'] if fit<0 else [])
    assert {n:s.volume() for n,s in solids.items()}==before
    if fit<0: assert row['raw_intersection_mm3']>0


def test_interference_multiple_negative_fits_are_pair_local():
    p=TabSlot(8,6,6,7,-.1);tab,slot=query_solids(p)
    solids={'stem':mf.Manifold.cube((40,12,50),True).translate((0,0,-25)),
            'bar':mf.Manifold.cube((60,20,12),True).translate((0,0,6))}
    parts={n:dict(assembly_transform=np.eye(4).tolist()) for n in solids}
    connections=[]
    for name,x in [('left',-10),('right',10)]:
        frame=np.eye(4);frame[0,3]=x
        solids['stem']+=tab.translate((x,0,0));solids['bar']-=slot.translate((x,0,0))
        connections.append(dict(id=name,tab_part='stem',slot_part='bar',parameters=asdict(p),
            tab_frame=frame.tolist(),slot_frame=frame.tolist()))
    row=measure_pair(parts,solids,connections)
    assert row['status']=='PASS' and row['allowed_fit_connection_ids']==['left','right']
    assert measure_pair(parts,solids,connections[:1])['status']=='FAIL'
    solids['stem']+=mf.Manifold.cube((3,4,4),True).translate((18,0,0))
    row=measure_pair(parts,solids,connections)
    assert row['status']=='FAIL' and row['undeclared_interference_mm3']==pytest.approx(24)
    unrelated=deepcopy(connections);unrelated[0]['slot_part']='other';unrelated[1]['slot_part']='other'
    row=measure_pair(parts,solids,unrelated)
    assert row['allowed_fit_connection_ids']==[] and row['connection_ids']==[]
    assert row['related_connection_ids']==['left','right']


def test_interference_outside_declared_interface_and_source_bounds():
    c,parts,solids=collision_pair()
    assert interface_measurement(c,parts,solids,1)['status']=='PASS'
    row=measure_pair(parts,solids,[c])
    assert row['status']=='FAIL' and row['part_ids']==['bar','stem']
    assert row['pair_id']=='bar:stem' and row['frame']=='assembly'
    assert row['undeclared_interference_mm3']==pytest.approx(24)
    np.testing.assert_allclose(row['bounds_mm'],[[7,-2,0],[10,2,2]])
    np.testing.assert_allclose(row['bounds_center_mm'],[8.5,0,1])


def test_interference_non_axis_scale_and_shared_export_query():
    c,parts,solids=collision_pair()
    angle=.3;matrix=np.eye(4)
    matrix[:2,:2]=[[np.cos(angle),-np.sin(angle)],[np.sin(angle),np.cos(angle)]]
    matrix[:3,3]=[3,4,5]
    for part in parts.values():part['assembly_transform']=matrix.tolist()
    row=measure_pair(parts,solids,[c],10.)
    tf=mm_matrix(matrix,10.)
    direct=material_interference(solids['bar'].transform(tf[:3,:]),solids['stem'].transform(tf[:3,:]),
        volume_tolerance_mm3=row['volume_tolerance_mm3'])
    assert direct['status']==row['status']=='FAIL'
    assert direct['undeclared_interference_mm3']==pytest.approx(row['undeclared_interference_mm3'])
    np.testing.assert_allclose(row['bounds_center_mm'],tf[:3,:3]@[8.5,0,1]+[30,40,50])
    assert row['undeclared_interference_mm3']==pytest.approx(24)


def test_interference_query_error_is_not_zero_overlap(monkeypatch):
    import adsl.core.assembly_topology as core
    def reject(s):raise ValueError('native query failed')
    monkeypatch.setattr(core,'checked',reject)
    with pytest.raises(ValueError,match='native query failed'):
        material_interference(mf.Manifold.cube((1,1,1)),mf.Manifold.cube((1,1,1)),volume_tolerance_mm3=.1)
