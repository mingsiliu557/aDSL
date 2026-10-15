"""One artificial partition fixture; opt-in CPU export/physics, no models."""
import json
import os
from pathlib import Path
import time

import pytest

pytestmark = pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL') != '1',
                               reason='explicit native CPU partition smoke')


def test_documented_partition_measured_and_duplicate_material_rejected(tmp_path):
    from dataclasses import replace
    import numpy as np
    from adsl.agents.utils.execution import execute_asset_source
    from adsl.agents.assembly_physics import checker_spec, run_assembly_checks
    from adsl.agents.assembly_topology import checker_spec as topology_spec
    from adsl.agents.partition_score import create_reference, reference_shape_comparison, OBJECTIVE, compare_partition_scores
    from adsl.core.export.mesh_validity import validate_written_mesh
    from adsl.core.export.export_assembly import evaluated
    from adsl.core import Cube, boolean_union, boolean_difference
    started=time.monotonic()
    root=Path(__file__).resolve().parents[1]
    doc=(root/'adsl-core/core/docs/fixed_assembly.md').read_text()
    candidate=doc.split('### Implementing a regroup proposal',1)[1].split('```python\n',1)[1].split('```',1)[0]
    baseline=candidate[:candidate.index('body_piece =')]+"assembly = FixedAssembly(root_id='snowman', mm_per_unit=1)\nassembly.add_part('snowman', whole_snowman, components=('body','hat'))\nscene=assembly.scene()\n"
    negative=candidate.replace('body_piece = boolean_difference(semantic_body.copy(), semantic_hat.copy())','body_piece = whole_snowman.copy()').replace('hat_piece = semantic_hat.copy()','hat_piece = whole_snowman.copy()')
    config={'mm_per_unit':1.,'fit_offset_mm':.15,'final_size_mm':[16.,16.,23.]}
    physics={'overhang':{'overhang_threshold_from_horizontal_deg':45.,'layer_height_mm':.2,
        'orientation_editable':True,'partition_editable':True,'partition_objective':dict(OBJECTIVE)},
        'standing':{'duration_seconds':5.,'timestep_seconds':.002,'friction':[.5,.005,.0001],
        'tilt_threshold_deg':25.,'settle_window_seconds':.5,'settle_linear_m_s':.001,
        'settle_angular_rad_s':.01,'collision_backend':'rigid_flex'},'material':{'density_kg_m3':1240.}}
    config['physics']=physics
    specs=[topology_spec(),checker_spec('assembly_overhang'),checker_spec('assembly_standing')]
    results={};reference=None
    for name,text in [('baseline',baseline),('candidate',candidate),('duplicate_material',negative)]:
        folder=tmp_path/name;folder.mkdir();source=folder/'source.py';source.write_text(text)
        tick=time.monotonic()
        execution=execute_asset_source(source,folder/'asset',render=False,export_urdf=False,fixed_assembly=config,timeout=300)
        manifest=execution.output_root/'assembly/assembly_manifest.json'
        report=json.loads(manifest.read_text())
        execution=replace(execution,glb_path=manifest.parent/'scene.glb')
        if name=='baseline':reference=create_reference(manifest,tmp_path/'reference',OBJECTIVE)
        runs=run_assembly_checks(specs if name!='duplicate_material' else specs[:1],
            execution=execution,source=source,root=folder,physics=physics,partition_reference=reference)
        results[name]={'export_status':report['status'],'checks':{r.spec.name:r.result.status for r in runs},
            'elapsed_seconds':time.monotonic()-tick,'manifest':str(manifest),
            'result_paths':{r.spec.name:str(r.output_dir/'result.json') for r in runs}}
        if name!='duplicate_material':
            assert report['manufacturing_status']=='PASS' and report['display_status']=='PASS'
            assert len(report['parts'])==(1 if name=='baseline' else 2)
            for part in report['parts']:
                mesh,solid,validation=validate_written_mesh(manifest.parent/part['stl'])
                assert solid.volume()>0
                assert np.isfinite(mesh.vertices).all()
            assert any(row['file']=='scene.glb' and row['status']=='PASS' for row in report['export_consistency'])
            assert reference_shape_comparison(manifest,reference,config=OBJECTIVE)['status']=='MATCH'
            objective=next(r.result.metrics['partition_objective'] for r in runs if r.spec.name=='assembly_overhang')
            assert objective['score'] is not None
            results[name]['objective']=objective
            assert all(r.result.status=='PASS' for r in runs),results[name]
        else:
            assert runs[0].result.status=='FAIL'
            assert any(f.rule_id=='UNDECLARED_PART_INTERFERENCE' for f in runs[0].result.findings)
    # Independent analytic pre-connector volume oracle.
    body=Cube((10,10,20),center=(0,0,10));hat=Cube((16,16,4),center=(0,0,21))
    _,solid,_=evaluated(boolean_union(body,hat),tmp_path/'oracle.glb',1.,_write_display=False)
    assert solid.volume()==pytest.approx(2924.0)
    results['pre_connector_volume_mm3']=solid.volume()
    results['score_comparison']=compare_partition_scores(
        {'status':'PASS','metrics':{'partition_objective':results['baseline']['objective']}},
        {'status':'PASS','metrics':{'partition_objective':results['candidate']['objective']}})
    assert results['score_comparison']['conclusion']=='WORSE'
    results['elapsed_seconds']=time.monotonic()-started
    (tmp_path/'native_summary.json').write_text(json.dumps(results,indent=2,allow_nan=False))
    print('NATIVE_SUMMARY',tmp_path/'native_summary.json')
