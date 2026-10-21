"""Artificial/stub overhang transport assertions; not executed in this change."""
from copy import deepcopy
from types import SimpleNamespace
import numpy as np
import pytest
from adsl.agents import assembly_overhang as overhang, support_volume as support
from test_support_volume import row


def setup(tmp_path,monkeypatch):
    import trimesh
    mesh=trimesh.creation.box()
    manifest={'source_sha256':'source','parts':[dict(id='body',print_transform_mm=np.eye(4).tolist())],
              'part_declarations':[{'id':'body'}]}
    args=SimpleNamespace(output=tmp_path,manifest=tmp_path/'manifest.json')
    monkeypatch.setattr(overhang,'load_parts',lambda a:(manifest,[{'part_id':'body','status':'PASS'}],{'body':mesh}))
    def measure(m,transform,config):
        return dict(area_mm2=6.,uncertainty={'bound_mm2':.1},regions=[],print_transform_mm=np.asarray(transform).tolist()),m.copy()
    monkeypatch.setattr(overhang,'measure_part',measure)
    monkeypatch.setattr(overhang,'_display_overlay',lambda *a:{'status':'PASS'})
    config={'overhang_threshold_from_horizontal_deg':45,'layer_height_mm':.2}
    return args,manifest,mesh,config


def test_area_output_does_not_gain_support_fields(tmp_path,monkeypatch):
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    def unexpected(*a,**kw):raise AssertionError('NSV measurement must not run')
    monkeypatch.setattr(support,'support_volume',unexpected)
    result=overhang.analyze(args,{'overhang':config})
    assert result.status=='PASS' and 'support' not in result.metrics
    assert all('support' not in item for item in result.metrics['items'])


def test_support_failure_never_becomes_zero_or_pass(tmp_path,monkeypatch):
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    def unavailable(*a,**kw):raise support.SupportMeasurementError('backend_unavailable')
    monkeypatch.setattr(support,'support_volume',unavailable)
    result=overhang.analyze(args,{'overhang':{**config,'support_metric':'nsv','shape_editable':True}})
    assert result.status=='INDETERMINATE'
    assert result.metrics['support']['total_support_volume_mm3'] is None
    assert not result.findings


def test_known_error_only_and_original_mesh_unchanged(tmp_path,monkeypatch):
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    original=mesh.vertices.copy()
    monkeypatch.setattr(support,'support_volume',lambda *a,**kw:row())
    result=overhang.analyze(args,{'overhang':{**config,'support_metric':'nsv','shape_editable':True}})
    assert np.array_equal(mesh.vertices,original)
    assert result.metrics['support']['nsv_total']==pytest.approx(.12)
    assert any(f.rule_id=='SUPPORT_SHAPE_OPPORTUNITY' for f in result.findings)
    def programming_error(*a,**kw):raise TypeError('program error')
    monkeypatch.setattr(support,'support_volume',programming_error)
    with pytest.raises(TypeError,match='program error'):
        overhang.analyze(args,{'overhang':{**config,'support_metric':'nsv'}})


def test_shape_disabled_has_no_shape_opportunity(tmp_path,monkeypatch):
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    monkeypatch.setattr(support,'support_volume',lambda *a,**kw:row())
    result=overhang.analyze(args,{'overhang':{**config,'support_metric':'nsv'}})
    assert not any(f.rule_id=='SUPPORT_SHAPE_OPPORTUNITY' for f in result.findings)


def test_missing_declaration_invalidates_whole_support_total(tmp_path,monkeypatch):
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    manifest['part_declarations'].append({'id':'missing'})
    monkeypatch.setattr(support,'support_volume',lambda *a,**kw:row())
    result=overhang.analyze(args,{'overhang':{**config,'support_metric':'nsv'}})
    assert result.status=='INDETERMINATE'
    assert result.metrics['support']['total_volume_mm3'] is None


@pytest.mark.parametrize('reference_available',[True,False])
def test_nsv_pose_measurement_and_layout_use_same_support_minimum(tmp_path,monkeypatch,reference_available):
    import json
    from adsl.agents import partition_score as ps
    args,manifest,mesh,config=setup(tmp_path,monkeypatch)
    args.manifest.write_text(json.dumps(manifest))
    np.savez(tmp_path/'body.solid.npz',vertices=mesh.vertices,faces=mesh.faces)
    reference=dict(voxel_pitch_mm=1.,reference_voxels=100,reference_sha256='ref')
    def create(*a):
        if not reference_available:
            raise ValueError('reference_geometry_invalid')
        return str(tmp_path/'reference.json')
    monkeypatch.setattr(ps,'create_reference',create)
    monkeypatch.setattr(ps,'load_reference',lambda *a:reference)
    calls=[]
    def measure(*a,**kw):
        index=len(calls);calls.append(index)
        return row(s=1. if index==5 else 10.)
    monkeypatch.setattr(support,'support_volume',measure)
    expected=np.eye(4);expected[:3,:3]=ps.rotations24()[5]
    expected[:3,3]=-(mesh.vertices@expected[:3,:3].T).min(axis=0)
    gaps=[]
    def gap_at_selected(mesh,reference,rotations=None):
        assert rotations is not None and len(rotations)==1
        assert np.array_equal(rotations[0],expected[:3,:3])
        gaps.append(None)
        return dict(gap_voxels=6,orientations=[dict(rotation_id=0,gap_voxels=6,
            occupied_voxels=1,print_transform_mm=expected.tolist())])
    monkeypatch.setattr(ps,'best_print_pose',gap_at_selected)
    monkeypatch.setattr(overhang,'_verify_recommended_stl',lambda *a:dict(status='PASS'))
    result=overhang.analyze(args,{'overhang':{**config,'support_metric':'nsv',
        'partition_objective':dict(ps.OBJECTIVE)}})
    item=result.metrics['items'][0]
    assert len(calls)==24  # Reuse selected measurement, no 25th support query.
    assert item['selected_rotation_id']==5
    assert np.allclose(item['support_print_transform_mm'],expected,rtol=0,atol=1e-12)
    assert item['recommended_print_transform_mm']==expected.tolist()
    assert result.metrics['support']['total_support_volume_mm3']==1.
    assert result.metrics['support_measurement_config']['pose_policy']==support.SUPPORT_POSE_POLICY
    assert len(gaps)==int(reference_available)
    layout=json.loads((tmp_path/'print_layout.json').read_text())
    assert layout['parts'][0]['recommended_print_transform_mm']==expected.tolist()
    published,parts=ps.publish_print_layout(result.model_dump(),tmp_path/'published','source','v1')
    if reference_available:
        assert result.status=='PASS' and published and len(parts)==1
        assert parts[0]['recommended_print_transform_mm']==expected.tolist()
    else:
        assert result.status=='INDETERMINATE' and published is None
        assert 'reference_geometry_invalid' in result.metrics['partition_reference_error']
        assert not result.findings
