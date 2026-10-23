"""Reassess artificial saved assets, never rerun Standing or support search."""
from copy import deepcopy
import json
from pathlib import Path

import numpy as np
import pytest
import trimesh

from benchmark.metrics import geometry as g, physical
from benchmark.scripts import evaluate_physical_metrics as runner


def dump(path, value):
    path.write_text(json.dumps(value,indent=2)+'\n')


def reference(path, *, pointer='', asset=None):
    result=dict(path=str(path),json_pointer=pointer,file_sha256=g.file_hash(path))
    if asset is not None:
        result['asset_sha256']=asset
    return result


def frozen_inputs(tmp_path):
    """Two adapters and two frozen cases; synthetic archived numeric results."""
    archive=tmp_path/'archive';archive.mkdir()
    methods={};summary=dict(status='COMPLETE',protocol_id=physical.STANDING_PROTOCOL,
        table=[],common_measurable_case_ids={'nsv':['first','second'],'N':['first','second']},samples={})
    config=json.loads(Path('benchmark/configs/physical_main_v1.json').read_text())
    support_config=dict(config['support'],algorithm='saved_fixture',orientation_set='axis_aligned_24')
    for method in ('baseline','ours'):
        records=[];metrics=[];standing_rows=[];connectivity_rows=[]
        for index, case_id in enumerate(('first','second')):
            root=archive/f'{method}_{case_id}';root.mkdir()
            source=root/'source.py';source.write_text('raise AssertionError("must not execute")\n')
            if method=='baseline':
                scene=trimesh.Scene()
                for i,x in enumerate((0.,1.001)):
                    transform=np.eye(4);transform[0,3]=x
                    scene.add_geometry(trimesh.creation.box(),node_name=f'object_{i}',transform=transform)
                mesh_path=root/'asset.glb';scene.export(mesh_path)
                geometry=dict(kind='single_print_mesh',mesh_path=str(mesh_path),
                    mesh_sha256=g.file_hash(mesh_path),length_unit_to_mm=1.,
                    file_to_assembly_mm=np.eye(4).tolist(),excluded_auxiliary_nodes=[])
            else:
                stl=root/'body.stl';trimesh.creation.box().export(stl)
                manifest=root/'manifest.json'
                dump(manifest,dict(source_sha256=g.file_hash(source),mm_per_unit=1.,
                    part_declarations=[{'id':'body'}],connections=[],
                    parts=[dict(id='body',stl=stl.name,print_transform_mm=np.eye(4).tolist(),
                        assembly_transform=np.eye(4).tolist())],files_sha256={stl.name:g.file_hash(stl)}))
                geometry=dict(kind='fixed_assembly',manifest_path=str(manifest),source_path=str(source),
                    manifest_sha256=g.file_hash(manifest))
            loaded=g.load_evaluation_parts({'geometry_input':geometry})
            assert loaded['status']=='PASS'
            asset=loaded['asset_sha256']
            standing=dict(case_id=case_id,applicable=True,evaluation_outcome='PASS',reason_codes=[],
                raw_status='PASS',observation=dict(status='PASS',observed_tipping=False,
                    observed_interface_exit=False,peak_tilt_deg=0.,final_tilt_deg=0.,duration_s=5.),
                source_sha256=g.file_hash(source),asset_sha256=asset,protocol_id=physical.STANDING_PROTOCOL)
            support=dict(protocol_id=g.SUPPORT_PROTOCOL,status='PASS',orientation_objective='support_volume_mm3',
                orientation_set='axis_aligned_24',total_support_volume_mm3=1.,total_volume_mm3=10.,nsv_total=.1,
                part_count=1,asset_sha256=asset,measurement_config_sha256=g.digest(support_config),reason_codes=[])
            support_file=root/'support.json'
            dump(support_file,dict(support,measurement_config=support_config,details={'saved_fixture':True}))
            support['evidence_ref']=reference(support_file,asset=asset)
            old=dict(protocol_id='print_plan_structural_connectivity_v1',status='PASS',overall_pass=True,
                internal_connected=True,interpart_connected=True,part_count=1,reason_codes=[],
                asset_sha256=asset,measurement_config_sha256='archived-connectivity-config')
            old_file=root/'connectivity.json';dump(old_file,dict(old,details={'saved_fixture':True}))
            old['evidence_ref']=reference(old_file,asset=asset)
            connectivity=dict(case_id=case_id,applicable=True,evaluation_outcome='PASS',reason_codes=[],
                status='PASS',internal_connected=True,interpart_connected=True)
            records.append(dict(case_id=case_id,method=method,applicable=True,state='TERMINAL',
                protocol_id=physical.STANDING_PROTOCOL,source_sha256=g.file_hash(source),asset_sha256=asset,
                part_count=1,geometry_input=geometry,standing_evaluation=standing,
                support_evaluation=support,connectivity=old))
            metrics.append(dict(case_id=case_id,standing=deepcopy(standing),connectivity=deepcopy(connectivity),
                raw_ivr=None,nsv=.1,N=1,missing_reasons={'raw_ivr':'SINGLE_PART_NOT_APPLICABLE','nsv':None,'N':None}))
            standing_rows.append(deepcopy(standing));connectivity_rows.append(connectivity)
        methods[method]=records
        summary['samples'][method]=dict(standing=dict(protocol_id=physical.STANDING_PROTOCOL,status='COMPLETE',
            planned_total=2,success_count=2,terminal_failure_count=0,pending_count=0,success_rate=1.,
            observed_tipping_count=0,observed_interface_exit_count=0,cases=standing_rows),
            connectivity=dict(planned_total=2,success_count=2,terminal_failure_count=0,pending_count=0,
                success_rate=1.,cases=connectivity_rows),metrics=metrics,
            metric_case_ids={'nsv':['first','second'],'N':['first','second']},measured_counts={'nsv':2,'N':2})
        summary['table'].append(dict(method=method,standing_success_rate=1.,structural_connectivity_rate=1.,nsv=.1,N=1.))
    summary_file=archive/'summary.json';dump(summary_file,summary)
    for method,records in methods.items():
        for index,r in enumerate(records):
            r['standing_evidence_ref']=reference(summary_file,
                pointer=f'/samples/{method}/standing/cases/{index}',asset=r['asset_sha256'])
    records_file=tmp_path/'records.json'
    dump(records_file,dict(methods=methods,frozen_summary_ref=reference(summary_file)))
    config_file=tmp_path/'config.json';dump(config_file,config)
    return records_file,config_file,summary


@pytest.fixture
def forbid_other_metrics(monkeypatch):
    def forbidden(*args,**kwargs):
        raise AssertionError('Standing or support must not run during connectivity reassessment')
    monkeypatch.setattr(runner,'measure_support24',forbidden)
    monkeypatch.setattr(runner,'summarize_physical_methods',forbidden)
    monkeypatch.setattr(physical,'classify_standing_record',forbidden)
    monkeypatch.setattr(physical,'summarize_standing',forbidden)


def unchanged_metrics(old,new):
    assert old['common_measurable_case_ids']==new['common_measurable_case_ids']
    for before,after in zip(old['table'],new['table']):
        assert {k:v for k,v in before.items() if k!='structural_connectivity_rate'}=={
            k:v for k,v in after.items() if k!='structural_connectivity_rate'}
    for method, before in old['samples'].items():
        after=new['samples'][method]
        for field in ('standing','metric_case_ids','measured_counts'):
            assert before[field]==after[field]
        for a,b in zip(before['metrics'],after['metrics']):
            assert {k:v for k,v in a.items() if k!='connectivity'}=={k:v for k,v in b.items() if k!='connectivity'}


def test_connectivity_only_two_adapters_preserve_frozen_metrics_and_evidence(tmp_path,forbid_other_metrics):
    pytest.importorskip('fcl')
    records,config,old=frozen_inputs(tmp_path)
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    output=tmp_path/'new'
    result=runner.evaluate(records,config,output,connectivity_only=True)
    unchanged_metrics(old,result)
    assert result['status']=='COMPLETE'
    assert all(r['structural_connectivity_rate']==1 for r in result['table'])
    assert result['samples']['baseline']['connectivity']['internal_multicomponent_case_count']==2
    data=json.loads((output/'measured_records.json').read_text())
    originals=json.loads(records.read_text())
    for method,rows in data['methods'].items():
        for index,r in enumerate(rows):
            original=originals['methods'][method][index]
            for field in ('standing_evaluation','asset_sha256','source_sha256','part_count','state','case_id'):
                assert r[field]==original[field]
            assert {k:v for k,v in r['support_evaluation'].items() if k!='evidence_ref'}=={
                k:v for k,v in original['support_evaluation'].items() if k!='evidence_ref'}
            for ref in (r['standing_evidence_ref'],r['support_evaluation']['evidence_ref'],
                    r['previous_connectivity']['evidence_ref'],r['connectivity']['evidence_ref']):
                assert g.file_hash(output/ref['path'])==ref['file_sha256']
                value=json.loads((output/ref['path']).read_text())
                for token in ref['json_pointer'].split('/')[1:]:
                    value=value[int(token)] if isinstance(value,list) else value[token]
                assert value['asset_sha256']==r['asset_sha256']
    assert all(path.read_bytes()==contents for path,contents in before.items())
    identity=json.loads((output/'input_identity.json').read_text())
    assert identity['remeasured_metrics']==['connectivity']
    assert identity['model_calls']==0 and identity['standing_simulated'] is identity['support_measured'] is False
    assert (output/'main_table.csv').exists()
    with pytest.raises(FileExistsError):
        runner.evaluate(records,config,output,connectivity_only=True)


def test_deferred_is_not_measured_or_counted_as_failure(tmp_path,monkeypatch,forbid_other_metrics):
    pytest.importorskip('fcl')
    records,config,old=frozen_inputs(tmp_path)
    data=json.loads(records.read_text())
    data['deferred_connectivity']=[dict(method='ours',case_id='second',reason='previous distance unavailable; user deferred')]
    dump(records,data)
    calls=[];real=runner.measure_connectivity
    def measure(parts,**kwargs):
        calls.append(parts[0]['_asset_sha256']);return real(parts,**kwargs)
    monkeypatch.setattr(runner,'measure_connectivity',measure)
    output=tmp_path/'new';result=runner.evaluate(records,config,output,connectivity_only=True)
    assert len(calls)==3
    assert result['status']=='INCOMPLETE'
    assert result['table'][0]['structural_connectivity_rate']==1.
    assert result['table'][1]['structural_connectivity_rate'] is None
    con=result['samples']['ours']['connectivity']
    assert con['planned_total']==2 and con['success_count']==1 and con['pending_count']==1
    assert con['terminal_failure_count']==0 and con['unavailable_count']==0
    assert con['cases'][1]['evaluation_outcome']=='PENDING'
    assert con['cases'][1]['reason_codes']==['CONNECTIVITY_REEVALUATION_DEFERRED']
    row=json.loads((output/'measured_records.json').read_text())['methods']['ours'][1]
    assert row['state']=='TERMINAL'
    assert row['connectivity']['status']=='UNAVAILABLE' and row['connectivity']['overall_pass'] is None
    evidence=json.loads((output/row['connectivity']['evidence_ref']['path']).read_text())
    assert evidence['details']['measured'] is False
    assert (output/evidence['details']['previous_evidence_ref']['path']).exists()
    unchanged_metrics(old,result)


@pytest.mark.parametrize('failure', [
    'missing_support','source','asset','count','standing','standing_pointer','summary_hash',
    'support_value','support_config','case_order','state','missing_evidence','geometry_hash',
    'duplicate_deferred',
])
def test_reuse_conflicts_are_rejected_not_recomputed(tmp_path,monkeypatch,forbid_other_metrics,failure):
    records,config,_=frozen_inputs(tmp_path);data=json.loads(records.read_text())
    r=data['methods']['baseline'][0]
    if failure=='missing_support':r.pop('support_evaluation')
    elif failure=='source':r['source_sha256']='other'
    elif failure=='asset':r['asset_sha256']='other'
    elif failure=='count':r['part_count']=2
    elif failure=='standing':r['standing_evaluation']['evaluation_outcome']='FAIL'
    elif failure=='standing_pointer':r['standing_evidence_ref']['json_pointer']='/not_saved'
    elif failure=='summary_hash':data['frozen_summary_ref']['file_sha256']='wrong'
    elif failure=='support_value':r['support_evaluation']['nsv_total']=.2
    elif failure=='support_config':
        cfg=json.loads(config.read_text());cfg['support']['bed_tolerance_mm']=.3;dump(config,cfg)
    elif failure=='case_order':data['methods']['baseline'].reverse()
    elif failure=='state':r['state']='PENDING'
    elif failure=='missing_evidence':Path(r['support_evaluation']['evidence_ref']['path']).unlink()
    elif failure=='geometry_hash':r['geometry_input']['mesh_sha256']='wrong'
    elif failure=='duplicate_deferred':
        data['deferred_connectivity']=[dict(method='baseline',case_id='first',reason='pause')]*2
    dump(records,data)
    def forbidden(*args,**kwargs):raise AssertionError('invalid reuse must not trigger geometry measurements')
    monkeypatch.setattr(runner,'measure_connectivity',forbidden)
    with pytest.raises((ValueError,OSError)):
        runner.evaluate(records,config,tmp_path/'new',connectivity_only=True)


def test_audit_and_connectivity_only_are_mutually_exclusive(tmp_path):
    with pytest.raises(ValueError,match='mutually exclusive'):
        runner.evaluate(tmp_path/'unused',tmp_path/'unused',tmp_path/'new',audit_only=True,connectivity_only=True)


@pytest.mark.parametrize('field,value',[
    ('min_facing_area_ratio',1.5e-4),('min_facing_area_ratio',None),
    ('facing_normal_dot_max',-.5),('facing_normal_dot_max',False),
    ('facing_area_reduction','max'),('facing_area_reduction',None),
])
def test_frozen_contact_settings_cannot_silently_change(tmp_path,monkeypatch,forbid_other_metrics,field,value):
    records,config,_=frozen_inputs(tmp_path)
    data=json.loads(config.read_text())
    if value is None:data['connectivity'].pop(field)
    else:data['connectivity'][field]=value
    dump(config,data)
    def forbidden(*args,**kwargs):raise AssertionError('invalid protocol must not run connectivity')
    monkeypatch.setattr(runner,'measure_connectivity',forbidden)
    with pytest.raises(ValueError,match='facing-contact setting'):
        runner.evaluate(records,config,tmp_path/'new',connectivity_only=True)
