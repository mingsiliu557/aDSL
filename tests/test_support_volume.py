"""Written contracts for the NSV branch. No test execution in this change."""
from copy import deepcopy
from types import SimpleNamespace
import numpy as np
import pytest
from adsl.agents import actions, support_volume as sv


def row(s=12., v=100., a=6.):
    return dict(status='PASS', support_volume_mm3=s, mesh_volume_mm3=v,
                nsv=s/v, risky_projected_area_mm2=a, risky_face_count=4, ray_backend='stub')


def measurement(s, n=1):
    return dict(support=sv.aggregate([row(s)]), part_count=n)


@pytest.mark.parametrize('s,n,tau,rule', [
    (8,2,3,'split_support_gain'), (9,2,3,'split_without_support_gain'),
    (8,1,3,'support_gain'), (9,1,3,'no_support_gain'),
    (15,1,3,'merge_acceptable_support'), (15.01,1,3,'merge_worsens_support'),
])
def test_support_boundaries(s,n,tau,rule):
    baseline = measurement(12, 2 if rule.startswith('merge') else 1)
    assert actions.support_postcheck(baseline,measurement(s,n),tolerance_mm3=tau)['rule'] == rule


def test_zero_support_and_volume_dilution():
    assert actions.support_postcheck(measurement(0,2),measurement(0),tolerance_mm3=0)['conclusion']=='ACCEPT'
    assert actions.support_postcheck(measurement(0),measurement(0,2),tolerance_mm3=0)['conclusion']=='REJECT'
    candidate=measurement(12);candidate['support']['total_volume_mm3']=10000
    assert actions.support_postcheck(measurement(12),candidate,tolerance_mm3=1)['conclusion']=='REJECT'


@pytest.mark.parametrize('tau',[-1,float('nan'),float('inf'),True,None])
def test_invalid_tolerance(tau):
    with pytest.raises(ValueError):
        actions.support_postcheck({}, {}, tolerance_mm3=tau)


@pytest.mark.parametrize('rows',[[],[None],[row(),{'status':'INDETERMINATE'}],[row(v=-1)]])
def test_no_partial_total(rows):
    result=sv.aggregate(rows)
    assert result['status']=='INDETERMINATE'
    assert all(result[k] is None for k in ('total_support_volume_mm3','total_volume_mm3','nsv_total','total_risky_projected_area_mm2'))


def test_weighted_aggregate():
    assert sv.aggregate([row(10,100),row(90,900)])['nsv_total']==.1


def part(rotation=None, z=0):
    m=np.eye(4);m[2,3]=z
    if rotation is not None:m[:3,:3]=rotation
    return dict(id='body',print_transform_mm=m.tolist())


def test_pose_translation_rotation_and_initial_condition():
    assert actions.print_pose_postcheck([part()],[part(z=2)])['conclusion']=='ACCEPT'
    r=[[0,-1,0],[1,0,0],[0,0,1]]
    assert actions.print_pose_postcheck([part()],[part(r)])['rule']=='print_pose_changed'
    assert actions.print_pose_postcheck([part(r)],[part(r)])['rule']=='print_pose_changed'
    assert actions.print_pose_postcheck([part()],[])['rule']=='part_count_changed'
    assert actions.print_pose_postcheck([part()],[{'id':'body'}])['rule']=='print_pose_unavailable'


def test_config_is_read_only():
    config={};assert sv.support_options(config)==('area',False);assert config=={}
    with pytest.raises(ValueError):sv.support_options({'shape_editable':'False'})
    with pytest.raises(ValueError):sv.support_options({'support_metric':'volume'})


def test_shape_permission_does_not_authorize_grouping():
    proposal=SimpleNamespace(action='reshape',grouping_change=None)
    actions.regroup_precheck(proposal,purpose='partition_optimization',support_metric='nsv',shape_editable=True)
    with pytest.raises(actions.InvalidPartitionProposal):
        actions.regroup_precheck(proposal,purpose='partition_optimization')
    proposal.action='regroup_print_parts'
    with pytest.raises(actions.InvalidPartitionProposal):
        actions.regroup_precheck(proposal,purpose='partition_optimization',support_metric='nsv',shape_editable=True)


def test_cube_zero_and_source_unchanged(monkeypatch):
    import trimesh
    mesh=trimesh.creation.box();original=mesh.vertices.copy()
    backend=SimpleNamespace(intersects_location=lambda **kw: (_ for _ in ()).throw(AssertionError('no risky rays')))
    monkeypatch.setattr(sv,'_ray_backend',lambda m:(backend,'stub'))
    result=sv.support_volume(mesh,overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.01)
    assert result['support_volume_mm3']==0
    assert np.array_equal(original,mesh.vertices)


def t_mesh(k=1.):
    # Closed extruded T: lower stem width 2, upper flange width 6, depth 2.
    import trimesh
    outline=np.array([[-1,0],[1,0],[1,2],[3,2],[3,3],[-3,3],[-3,2],[-1,2]],float)
    vertices=np.array([[x,y,z] for y in (-1,1) for x,z in outline])*k
    cap=[(0,1,2),(0,2,7),(7,2,3),(7,3,4),(7,4,5),(7,5,6)]
    faces=[(a,b,c) for a,b,c in cap]+[(a+8,c+8,b+8) for a,b,c in cap]
    for i in range(8):
        j=(i+1)%8;faces.extend([(i,j+8,j),(i,i+8,j+8)])
    mesh=trimesh.Trimesh(vertices=vertices,faces=faces,process=False)
    if mesh.volume<0:mesh.invert()
    return mesh


def test_analytic_t_and_scaling():
    a=sv.support_volume(t_mesh(),overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.01)
    b=sv.support_volume(t_mesh(2),overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.02)
    assert a['support_volume_mm3']==pytest.approx(16.)
    assert b['support_volume_mm3']==pytest.approx(8*a['support_volume_mm3'])
    assert b['nsv']==pytest.approx(a['nsv'])


def test_ray_failure_is_not_platform_fallback(monkeypatch):
    def fail(**kwargs):raise RuntimeError('backend failed')
    monkeypatch.setattr(sv,'_ray_backend',lambda m:(SimpleNamespace(intersects_location=fail),'stub'))
    with pytest.raises(sv.SupportMeasurementError,match='ray_backend_failed'):
        sv.support_volume(t_mesh(),overhang_threshold_from_horizontal_deg=45,bed_tolerance_mm=.01)
