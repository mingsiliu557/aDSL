"""Native small export fixtures; real voxels, STL/GLB and topology."""
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest

pytestmark=pytest.mark.skipif(os.environ.get('ADSL_TEST_FIXED_REAL')!='1',reason='native Blender export')


@pytest.mark.parametrize('mode',['geometry','visual_only'])
def test_body_reference_and_print_layout_units(tmp_path,mode):
    from adsl.core import Cube,FixedAssembly
    from adsl.core.export.export_assembly import export_assembly
    from adsl.agents.partition_score import OBJECTIVE,create_reference,load_reference,occupied_voxels,vertical_gap_count
    from adsl.agents.assembly_overhang import analyze
    from adsl.agents.feedback_schema import sha256_file
    import trimesh
    assembly=FixedAssembly(root_id='part',mm_per_unit=2)
    assembly.add_part('part',Cube((1,1.5,2)),components=('body',))
    source=tmp_path/'source.py';source.write_text('# native export fixture')
    config=dict(partition_objective=OBJECTIVE,overhang_threshold_from_horizontal_deg=45,layer_height_mm=.2)
    report=export_assembly(assembly,tmp_path/'asset',source_sha256=sha256_file(source),expected=dict(
        validation_mode=mode,mm_per_unit=2,fit_offset_mm=.2,final_size_mm=[2,3,4],physics={'overhang':config}))
    assert report['status']==('PASS' if mode=='geometry' else 'NOT_EVALUATED'),report['failures']
    assert report['partition_reference_inputs'][0]['status']=='PASS'
    manifest=tmp_path/'asset/assembly_manifest.json'
    refpath=create_reference(manifest,tmp_path/'references');reference=load_reference(refpath)
    assert reference['voxel_pitch_mm']==pytest.approx(.2)
    output=tmp_path/'overhang';output.mkdir()
    result=analyze(SimpleNamespace(manifest=manifest,source=source,output=output,topology_cache=None),
        {'overhang':config,'partition_reference_path':str(refpath)})
    assert result.status=='PASS'
    score=result.metrics['partition_objective'];assert score['gap_voxels']==0 and score['print_part_count']==1
    row=json.loads(Path(result.artifacts['print_layout']).read_text())['parts'][0]
    printed=trimesh.load(row['stl'],force='mesh',process=True)
    np.testing.assert_allclose(printed.bounds[0],0,atol=1e-12)
    assert vertical_gap_count(occupied_voxels(printed,reference['voxel_pitch_mm'],[0,0,0]))==row['gap_voxels']
    assert report['part_declarations'][0]['assembly_transform']==np.eye(4).tolist()


def test_visual_reference_failure_does_not_reject_display(tmp_path,monkeypatch):
    from adsl.core import Cube,FixedAssembly
    e=importlib.import_module('adsl.core.export.export_assembly')
    from adsl.agents.partition_score import OBJECTIVE
    real=e.evaluated
    def evaluate(shape,path,*args,**kw):
        if str(path).endswith('.body.glb'):raise ValueError('deliberately unavailable reference')
        return real(shape,path,*args,**kw)
    monkeypatch.setattr(e,'evaluated',evaluate)
    a=FixedAssembly(root_id='part',mm_per_unit=1);a.add_part('part',Cube(1),components=('body',))
    r=e.export_assembly(a,tmp_path,source_sha256='fixture',expected=dict(validation_mode='visual_only',
        mm_per_unit=1,fit_offset_mm=.2,physics={'overhang':{'partition_objective':OBJECTIVE}}))
    assert r['export_status']=='PASS' and not r['failures']
    assert r['partition_reference_inputs'][0]['status']=='INDETERMINATE'
