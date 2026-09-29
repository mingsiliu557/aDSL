"""Focused profile, transform and native GLB contracts (no application checkers)."""
import builtins
import json
import math
import os
from pathlib import Path
import subprocess
import sys

import numpy as np
import pytest
import trimesh

from adsl.core import (Asset, Polygon, Cube, Cylinder, linear_extrude, rotate_extrude,
    hull, transform_shape, translation_matrix, rotation_matrix, scaling_matrix,
    shape_aabb, shape_support, boolean_difference, align_anchors)


def mesh_of(asset):
    p=asset.local_primitives()[0]
    return trimesh.Trimesh(vertices=p['params']['vertices'],faces=p['params']['triangles'],process=False)


def test_concave_holes_winding_closure_and_top_scale():
    l=Polygon([(0,0),(4,0),(4,1),(1,1),(1,3),(0,3),(0,0)])
    mesh=mesh_of(linear_extrude(l,5))
    assert mesh.is_watertight and mesh.volume==pytest.approx(6*5)
    outer=[(0,0),(5,0),(5,5),(0,5)];hole=[(1,1),(3,1),(3,3),(1,3)]
    for a,b in ((outer,hole),(outer[::-1],hole[::-1])):
        p=Polygon(a,holes=[b]);mesh=mesh_of(linear_extrude(p,3,center=True))
        assert mesh.is_watertight and mesh.volume==pytest.approx((25-4)*3)
        np.testing.assert_allclose(mesh.bounds[:,2],[-1.5,1.5])
        assert json.loads(json.dumps(p.to_dict()))['holes']
    tapered=mesh_of(linear_extrude(Polygon([(1,1),(3,1),(3,3),(1,3)]),3,scale_top=2))
    np.testing.assert_allclose(tapered.vertices[tapered.vertices[:,2]==3,:2].min(axis=0),[2,2])
    np.testing.assert_allclose(tapered.vertices[tapered.vertices[:,2]==3,:2].max(axis=0),[6,6])
    assert tapered.volume==pytest.approx(4*3*(1+2+4)/3)


def test_mesh_transform_support_and_copy():
    asset=linear_extrude(Polygon([(0,0),(3,0),(0,2)]),4)
    before=mesh_of(asset).vertices.copy()
    m=translation_matrix((8,-3,2)) @ rotation_matrix((1,2,3),37) @ scaling_matrix((2,.5,3))
    other=transform_shape(asset,m)
    expected=before @ m[:3,:3].T+m[:3,3]
    np.testing.assert_allclose(shape_aabb(other),[expected.min(axis=0),expected.max(axis=0)])
    direction=np.array([1.,-2.,3.]);direction/=np.linalg.norm(direction)
    point=shape_support(other,direction)
    assert np.dot(point,direction)==pytest.approx(np.max(expected @ direction))
    np.testing.assert_array_equal(mesh_of(asset).vertices,before)
    assert asset.local_primitives()[0]['params']['vertices'] is other.local_primitives()[0]['params']['vertices']


@pytest.mark.parametrize('angle',[360,180,90])
def test_revolve_hollow_and_axis_contact(angle):
    profile=Polygon([(1,0),(2,0),(2,3),(1,3)])
    asset=rotate_extrude(profile,angle=angle,segments=64)
    mesh=mesh_of(asset)
    assert mesh.is_watertight
    # Full-circle resolution: quarter/half revolutions retain the same step.
    count=64*angle/360
    assert mesh.volume==pytest.approx(.5*count*math.sin(2*math.pi/64)*(4-1)*3)
    np.testing.assert_allclose(mesh.bounds[:,2],[0,3])
    if angle<=180:assert mesh.bounds[0,1]>=-1e-12
    if angle==90:assert mesh.bounds[0,0]>=-1e-12
    solid=mesh_of(rotate_extrude(Polygon([(0,0),(2,0),(2,3),(0,3)]),segments=64))
    assert solid.is_watertight and solid.volume>mesh.volume


@pytest.mark.parametrize('points,holes,reason',[
    ([(0,0),(1,1),(0,1),(1,0)],[], 'self-intersection'),
    ([(0,0),(1,0),(2,0)],[], 'overlap|area'),
    ([(0,0),(1,0),(1,0),(0,1)],[], 'distinct'),
    ([(0,0),(float('nan'),0),(0,1)],[], 'finite'),
    ([(0,0,0),(1,0),(0,1)],[], '2D'),
    ([(0,0),(4,0),(4,4),(0,4)],[[(0,1),(1,1),(1,2),(0,2)]], 'strictly inside'),
    ([(0,0),(4,0),(4,4),(0,4)],[[(5,1),(6,1),(6,2),(5,2)]], 'strictly inside'),
    ([(0,0),(4,0),(4,4),(0,4)],[[(1,1),(3,1),(3,3),(1,3)],[(2,2),(3.5,2),(3.5,3),(2,3)]], 'overlap'),
    ([(0,0),(5,0),(5,5),(0,5)],[[(1,1),(4,1),(4,4),(1,4)],[(2,2),(3,2),(3,3),(2,3)]], 'nesting'),
])
def test_invalid_polygon(points,holes,reason):
    with pytest.raises(ValueError,match=reason):Polygon(points,holes=holes)


@pytest.mark.parametrize('op,kw',[(linear_extrude,dict(height=0)),(linear_extrude,dict(height=float('inf'))),
    (linear_extrude,dict(height=1,scale_top=(1,0))), (rotate_extrude,dict(angle=0)),
    (rotate_extrude,dict(angle=361)),(rotate_extrude,dict(segments=2)),(rotate_extrude,dict(segments=3.5))])
def test_invalid_parameters(op,kw):
    with pytest.raises(ValueError):op(Polygon([(0,0),(1,0),(0,1)]),**kw)


def test_revolve_negative_radius_and_missing_extra(monkeypatch):
    with pytest.raises(ValueError,match='nonnegative'):
        rotate_extrude(Polygon([(-1,0),(1,0),(1,2),(-1,2)]))
    original=builtins.__import__
    def unavailable(name,*a,**kw):
        if name=='manifold3d':raise ImportError('missing')
        return original(name,*a,**kw)
    monkeypatch.setattr(builtins,'__import__',unavailable)
    # Public API and Polygon remain usable without native dependencies.
    from adsl.core import Polygon as Profile
    with pytest.raises(ImportError,match=r'adsl-core\[geometry\]'):
        linear_extrude(Profile([(0,0),(1,0),(0,1)]),1)
