"""Distance fallback uses boundary evidence, not an absolute epsilon clamp."""
from types import SimpleNamespace

import manifold3d as mf
import numpy as np
import pytest
import trimesh

from benchmark.metrics import geometry as g


class _ReportedIntersection:
    """Keep real triangle evidence while injecting the known integration error."""
    def __init__(self, mesh, reported_volume):
        self.mesh = mesh
        self.reported_volume = reported_volume

    def status(self):
        return mf.Error.NoError

    def volume(self):
        return self.reported_volume

    def is_empty(self):
        return False

    def to_mesh64(self):
        return SimpleNamespace(vert_properties=np.asarray(self.mesh.vertices),
                               tri_verts=np.asarray(self.mesh.faces))


class _Operand:
    def __init__(self, solid, intersection):
        self.solid = solid
        self.intersection = intersection

    def __xor__(self, other):
        return self.intersection

    def __getattr__(self, key):
        return getattr(self.solid, key)


def _flat_boundary():
    # Closed double-sided planar boundary with different front/back diagonals.
    return trimesh.Trimesh(
        [[0.,0.,1.],[1.,0.,1.],[1.,1.,1.],[0.,1.,1.]],
        [[0,2,1],[0,1,3],[0,3,2],[1,2,3]], process=False)


def test_negative_contact_integral_is_exactly_zero_then_fcl_confirms_contact():
    pytest.importorskip('fcl')
    mesh = _flat_boundary()
    before_vertices, before_faces = mesh.vertices.copy(), mesh.faces.copy()
    a = _Operand(mf.Manifold.cube((1,1,1)), _ReportedIntersection(mesh, -3.13e-12))
    b = mf.Manifold.cube((1,1,1)).translate((0,0,1))
    result = g._distance(a,b,0.)
    # FCL can report either boundary collision or an exact zero separation.
    assert result['method'] in ('FCL_SURFACE_COLLISION','FCL_TRIANGLE_DISTANCE')
    assert result['distance_mm']==0. and result['intersection_mm3']==0.
    diagnostic = result['intersection_diagnostic']
    assert diagnostic['raw_volume_mm3']==-3.13e-12
    assert diagnostic['method']=='EXACT_FLOAT64_BOUNDARY_VOLUME'
    assert diagnostic['exact_volume_sign']==0
    assert np.array_equal(mesh.vertices,before_vertices)
    assert np.array_equal(mesh.faces,before_faces)


def test_exact_zero_boundary_does_not_itself_create_contact():
    pytest.importorskip('fcl')
    a = _Operand(mf.Manifold.cube((1,1,1)), _ReportedIntersection(_flat_boundary(), -1e-15))
    b = mf.Manifold.cube((1,1,1)).translate((1.125,0,0))
    result = g._distance(a,b,.125)
    assert result['method']=='FCL_TRIANGLE_DISTANCE' and result['distance_mm']==.125
    assert result['edge'] is True
    assert g._distance(a,b,np.nextafter(.125,0))['edge'] is False


def test_negative_reported_volume_is_recomputed_without_clamping_positive_boundary():
    cube = trimesh.creation.box(extents=(.1,.2,.3))
    a = _Operand(mf.Manifold.cube((1,1,1)), _ReportedIntersection(cube,-1e-16))
    result = g._intersection_measurement(a,a)
    assert result['volume_mm3']==pytest.approx(.006)
    assert result['exact_volume_sign']==1 and result['raw_volume_mm3']==-1e-16


@pytest.mark.parametrize('size',[1.,1e-5])
def test_genuinely_negative_boundary_volume_remains_unavailable(size):
    mesh=trimesh.creation.box(extents=(size,size,size));mesh.invert()
    a=_Operand(mf.Manifold.cube((1,1,1)),_ReportedIntersection(mesh,-size**3))
    with pytest.raises(ValueError,match='after exact boundary sum'):
        g._intersection_measurement(a,a)


def test_negative_invalid_boundary_and_nonfinite_integral_remain_errors():
    mesh=_flat_boundary();mesh.update_faces(np.arange(len(mesh.faces)-1))
    a=_Operand(mf.Manifold.cube((1,1,1)),_ReportedIntersection(mesh,-1e-15))
    with pytest.raises(ValueError,match='invalid boundary evidence'):
        g._intersection_measurement(a,a)
    a.intersection=_ReportedIntersection(_flat_boundary(),float('nan'))
    with pytest.raises(ValueError,match='invalid material intersection volume'):
        g._intersection_measurement(a,a)


def test_native_empty_overlap_and_containment_keep_original_results():
    a=mf.Manifold.cube((4,4,4),center=True)
    contained=mf.Manifold.cube((1,1,1),center=True)
    result=g._intersection_measurement(a,contained)
    assert result['volume_mm3']==1. and result['method']=='MANIFOLD_VOLUME'
    result=g._intersection_measurement(a,contained.translate((20,0,0)))
    assert result['volume_mm3']==0. and result['method']=='MANIFOLD_EMPTY'


def test_fcl_failure_is_not_hidden_by_corrected_zero_volume(monkeypatch):
    def fail(*args,**kwargs):
        raise ValueError('FCL unavailable')
    monkeypatch.setattr(trimesh.collision,'CollisionManager',fail)
    a=_Operand(mf.Manifold.cube((1,1,1)),_ReportedIntersection(_flat_boundary(),-1e-15))
    with pytest.raises(ValueError,match='FCL unavailable'):
        g._distance(a,mf.Manifold.cube((1,1,1)).translate((0,0,1)),0.)
