"""One fixed offline mesh stress replay; no Agent or physical checker calls.

Run only after the current mesh group is complete:
    python reports/mesh_precision_export_20261007/stress.py --output /fresh/path

Expected categories distinguish fully representable positives, manufacturing
geometry whose display may be limited, and deliberately protected negatives.
No geometry threshold or production setting is changed by this script.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import math
from pathlib import Path
import subprocess
import time

import numpy as np
import trimesh

from adsl.core import (Asset, Cube, Cylinder, Sphere, boolean_difference,
                       boolean_intersection, boolean_union, rotate_shape,
                       scale_shape, translate_shape)
from adsl.core.export.mesh64 import evaluate_shape
from adsl.core.export.mesh_validity import (MeshEvaluationError, mesh_metrics,
    validate_mesh, validate_written_mesh, world_vertices)

exporter = importlib.import_module('adsl.core.export.export_assembly')
SEED = 20261007
REPO = Path(__file__).resolve().parents[2]
VALID = 'VALID_POSITIVE'
BOUNDED = 'MANUFACTURING_VALID_DISPLAY_MAY_BE_LIMITED'
PROTECTED = 'PROTECTED_DISPLAY_REJECTION'
INVALID = 'INVALID_INPUT_REJECTION'


def fixtures():
    """Eighteen generic deterministic inputs, independent of benchmark assets."""
    rng = np.random.default_rng(SEED)
    cases = []
    def add(name, shape, expected=VALID, unit=1., **checks):
        cases.append(dict(id=name, shape=shape, expected_category=expected,
                          mm_per_unit=unit, checks=checks))
    add('union_volume_12', boolean_union(Cube(2), Cube(2, center=(1,0,0))), volume=12.)
    add('difference_volume_4', boolean_difference(Cube(2), Cube(2, center=(1,0,0))), volume=4.)
    add('intersection_volume_4', boolean_intersection(Cube(2), Cube(2, center=(1,0,0))), volume=4.)

    nested = boolean_intersection(boolean_difference(boolean_union(
        Cube((2,2,2)), Cube((2,2,2), center=(1+1e-5,0,0))),
        Cube((1,4,4), center=(-.5,0,0))), Cube((4,1,4), center=(.5,0,0)))
    add('nested_near_coplanar', nested)
    for count in (32,96,160):
        # Each block overlaps several neighbours. Tiny generic perturbations
        # avoid relying on exact coincident faces for the dense union.
        children = []
        for i in range(count):
            x = 6*i/(count-1)
            jitter = rng.uniform(-.002,.002,3)
            center = np.array([x,.15*math.sin(x),.1*math.cos(x)])+jitter
            children.append(Cube((.8,.8,.8), center=tuple(center)))
        add(f'dense_union_{count}', boolean_union(*children), BOUNDED,
            primitive_count=count)

    points = [(2.5*i/12, .35*math.sin(1.5*i/12), .2*math.cos(2*i/12)) for i in range(13)]
    rounded = [Cylinder(.22,p0=a,p1=b) for a,b in zip(points[:-1],points[1:])]
    rounded += [Sphere(.22,center=p) for p in points[1:-1]]
    add('rounded_non_axis_chain', boolean_union(*rounded), BOUNDED, primitive_count=len(rounded))

    cavity = boolean_difference(Cube(4), Cube(2))
    add('signed_cavity', cavity, volume=56., material_components=1, shell_components=2)
    add('reflected_cavity', scale_shape(cavity,(-1,1,1),center=(0,0,0)),
        volume=56., material_components=1, shell_components=2)
    add('large_translation_nested', translate_shape(nested,(1e7,2e7,-3e7)),
        comparison_case='nested_near_coplanar', translation=[1e7,2e7,-3e7])
    rotated = rotate_shape(boolean_difference(Cube((3,2,2)),
        Cylinder(.4,height=4)),(1,2,3),37,center=(0,0,0))
    add('non_axis_rotation_nonunit_mm', rotated, unit=2.5)
    add('distinct_material_union', boolean_union(Cube(2,color=(1,0,0)),
        Cube(2,center=(1,0,0),color=(0,0,1))), minimum_materials=2, volume=12.)

    real_gap = boolean_union(Cube(1,center=(.5,.5,.5)),
        Cube(1,center=(1.5002,.5,.5)))
    add('real_point_two_mm_gap', real_gap, unit=1000.,
        material_components=2, gap_mm=.2, volume=2e9)
    too_small_gap = boolean_union(Cube(1,center=(.5,.5,.5)),
        Cube((2,1,1),center=(2+1e-9,.5,.5)))
    add('sub_float32_gap', too_small_gap, PROTECTED, material_components=2)

    base = trimesh.creation.box((1,1,1))
    def invalid_mesh(name, faces):
        shape = Asset(name)
        shape._primitives.append(dict(type='mesh', params=dict(
            vertices=tuple(tuple(float(c) for c in v) for v in base.vertices),
            triangles=tuple(tuple(int(c) for c in f) for f in faces)),
            xform=np.eye(4), color=(1,1,1), alpha=None))
        return shape
    add('missing_face', invalid_mesh('missing_face',base.faces[1:]), INVALID)
    add('duplicate_face', invalid_mesh('duplicate_face',np.vstack([base.faces,base.faces[:1]])), INVALID)
    add('tiny_distinct_material_overlap', boolean_union(
        Cube(1,center=(.5,.5,.5),color=(1,0,0)),
        Cube(1,center=(1+1e-9,.5+1e-9,.5),color=(0,0,1))), BOUNDED,
        minimum_materials=2)
    return cases


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def diagnostic(error):
    return dict(getattr(error,'diagnostic',{}) or dict(
        code=type(error).__name__, failure_kind='unclassified', message=str(error)))


def glb_readback(path, unit):
    """Group material meshes under their object, then check directed material."""
    scene = trimesh.load(path,force='scene',process=False)
    z_up = np.array([[1.,0,0,0],[0,0,-1,0],[0,1,0,0],[0,0,0,1]])
    pieces = []
    material_names = []
    for node in scene.graph.nodes_geometry:
        matrix,key = scene.graph[node]
        source = scene.geometry[key]
        vertices = world_vertices(source.vertices,z_up@matrix)*unit
        pieces.append(trimesh.Trimesh(vertices,source.faces,process=False))
        material_names.append(getattr(source.visual.material,'name',None))
    if not pieces:
        raise ValueError('actual GLB contains no geometry')
    mesh = trimesh.util.concatenate(pieces)
    solid,metrics = validate_mesh(mesh.vertices,mesh.faces,stage='file_readback')
    return dict(status='PASS',metrics=metrics,volume_mm3=float(solid.volume()),
                bounds_mm=mesh.bounds.tolist(),material_names=material_names,
                sha256=sha(path))


def checked_expectations(row,case,previous):
    """Check guards and known values; never relabel a failed measurement PASS."""
    kind = case['expected_category']
    if kind==INVALID:
        return (row['internal']['status']=='FAIL'
            and row['internal']['diagnostic'].get('failure_kind')=='input_geometry'
            and row['manufacturing']['status']=='FAIL'
            and row['manufacturing']['diagnostic'].get('failure_kind')=='input_geometry')
    if row['internal']['status']!='PASS' or row['manufacturing']['status']!='PASS' or row['stl']['status']!='PASS':
        return False
    expected = case['checks']
    metrics = row['manufacturing']['metrics']
    for key in ('material_components','shell_components'):
        if key in expected and metrics[key]!=expected[key]:
            return False
    if 'volume' in expected and not math.isclose(row['manufacturing']['volume_mm3'],
            expected['volume'],rel_tol=1e-7,abs_tol=1e-7):
        return False
    if 'minimum_materials' in expected and len(row['manufacturing']['material_ids'])<expected['minimum_materials']:
        return False
    if 'gap_mm' in expected and not math.isclose(row['manufacturing'].get('gap_mm',float('nan')),
                                                expected['gap_mm'],rel_tol=1e-6,abs_tol=2e-4):
        return False
    if 'comparison_case' in expected:
        base = previous[expected['comparison_case']]['manufacturing']
        if not math.isclose(row['manufacturing']['volume_mm3'],base['volume_mm3'],rel_tol=1e-7,abs_tol=1e-7):
            return False
        if not np.allclose(np.asarray(row['manufacturing']['bounds_mm'])-expected['translation'],
                           base['bounds_mm'],rtol=0,atol=1e-7):
            return False
    if kind==PROTECTED:
        return (row['display']['status']=='FAIL'
            and row['display']['diagnostic'].get('code')=='TARGET_PRECISION_UNREPRESENTABLE'
            and row['glb']['status']=='NOT_EXECUTED')
    if row['display']['status']=='FAIL':
        return kind==BOUNDED and row['display']['diagnostic'].get('output_role')=='display'
    if row['glb']['status']!='PASS':
        return False
    for key in ('material_components','shell_components'):
        if key in expected and row['glb']['metrics'][key]!=expected[key]:
            return False
    return True


def replay(case,output,previous):
    start=time.monotonic()
    output.mkdir()
    shape,unit = case['shape'],case['mm_per_unit']
    payload=shape.to_dict()
    source=json.dumps(payload,sort_keys=True,default=lambda value:value.tolist())
    (output/'fixture.json').write_text(source)
    row={key:case[key] for key in ('id','expected_category','mm_per_unit','checks')}
    row.update(fixture_sha256=hashlib.sha256(source.encode()).hexdigest(),
        internal=dict(status='NOT_EXECUTED'),manufacturing=dict(status='NOT_EXECUTED'),
        display=dict(status='NOT_EXECUTED'),stl=dict(status='NOT_EXECUTED'),
        glb=dict(status='NOT_EXECUTED'),self_intersection='NOT_EVALUATED')
    stage=time.monotonic()
    try:
        result=evaluate_shape(shape,mm_per_unit=unit)
        items=[]
        for piece in result.pieces:
            if piece.solid.is_empty():
                continue
            raw=piece.solid.to_mesh64()
            items.append(dict(node_path=piece.node_path,
                metrics=mesh_metrics(raw.vert_properties[:,:3],raw.tri_verts),
                transform=piece.transform.tolist()))
        row['internal']=dict(status='PASS',pieces=items,records=result.records)
    except Exception as error:
        row['internal']=dict(status='FAIL',diagnostic=diagnostic(error))
    row['internal']['elapsed_seconds']=time.monotonic()-stage
    mesh=solid=None
    stage=time.monotonic()
    try:
        mesh,solid,evidence=exporter.evaluated(shape,output/'display.glb',unit,
                                               keep_materials=True,_write_display=False)
        row['manufacturing']=dict(status='PASS',metrics=evidence['canonical_mesh'],
            volume_mm3=float(solid.volume()),bounds_mm=mesh.bounds.tolist(),
            material_ids=np.unique(mesh.face_attributes['material']).tolist())
        if 'gap_mm' in case['checks']:
            boxes=sorted((np.asarray(p.bounding_box()) for p in solid.decompose() if p.volume()>0),
                          key=lambda box:box[0])
            row['manufacturing']['gap_mm']=float(boxes[1][0]-boxes[0][3])
    except Exception as error:
        row['manufacturing']=dict(status='FAIL',diagnostic=diagnostic(error))
    row['manufacturing']['elapsed_seconds']=time.monotonic()-stage
    if mesh is not None:
        stage=time.monotonic()
        try:
            printed=mesh.copy()
            origin=mesh.bounds[0].copy()
            printed.vertices=np.asarray(mesh.vertices,dtype=np.float64)-origin
            printed.export(output/'print.stl',file_type='stl_ascii')
            saved,saved_solid,readback=validate_written_mesh(output/'print.stl')
            row['stl']=dict(readback,sha256=sha(output/'print.stl'),
                volume_mm3=float(saved_solid.volume()),local_origin_mm=origin.tolist())
        except Exception as error:
            row['stl']=dict(status='FAIL',diagnostic=diagnostic(error))
        row['stl']['elapsed_seconds']=time.monotonic()-stage
        stage=time.monotonic()
        row['display']=exporter._display_export({'stress_part':mesh},{'stress_part':np.eye(4)},
                                               output/'display.glb',unit,{})
        row['display']['elapsed_seconds']=time.monotonic()-stage
        if row['display']['status']=='PASS':
            stage=time.monotonic()
            try:
                row['glb']=glb_readback(output/'display.glb',unit)
            except Exception as error:
                row['glb']=dict(status='FAIL',diagnostic=diagnostic(error))
            row['glb']['elapsed_seconds']=time.monotonic()-stage
    row['expectation_matched']=checked_expectations(row,case,previous)
    if case['expected_category']==INVALID and row['expectation_matched']:
        row['conclusion']='INVALID_INPUT_CORRECTLY_REJECTED'
    elif case['expected_category']==PROTECTED and row['expectation_matched']:
        row['conclusion']='UNREPRESENTABLE_FEATURE_CORRECTLY_PROTECTED'
    elif row['expectation_matched'] and row['display']['status']=='FAIL':
        row['conclusion']='MANUFACTURING_VALID_DISPLAY_LIMITED'
    elif row['expectation_matched']:
        row['conclusion']='VALID_FULL_CHAIN'
    else:
        row['conclusion']='UNEXPECTED_RESULT'
    row['elapsed_seconds']=time.monotonic()-start
    (output/'result.json').write_text(json.dumps(row,indent=2))
    return row


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    started=time.monotonic()
    try:
        commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
        diff_hash=hashlib.sha256(subprocess.check_output(['git','diff','--binary'],cwd=REPO)).hexdigest()
    except (OSError,subprocess.SubprocessError):
        commit='UNKNOWN'
        diff_hash='UNKNOWN'
    provenance=dict(seed=SEED,commit=commit,git_diff_sha256=diff_hash,script_sha256=sha(__file__),
        scope='offline mesh validity, target conversion and actual GLB/ASCII STL readback only',
        self_intersection='NOT_EVALUATED',thresholds_changed=False,
        code_sha256={str(p.relative_to(REPO)):sha(p) for p in [
            REPO/'adsl-core/core/export/mesh64.py',
            REPO/'adsl-core/core/export/mesh_validity.py',
            REPO/'adsl-core/core/export/local_precision_repair.py',
            REPO/'adsl-core/core/export/export_assembly.py',
            REPO/'adsl-core/core/export/export_glb.py']})
    (args.output/'provenance.json').write_text(json.dumps(provenance,indent=2))
    results={}
    for case in fixtures():
        print(json.dumps(dict(case=case['id'],stage='start')),flush=True)
        row=replay(case,args.output/case['id'],results)
        results[case['id']]=row
        print(json.dumps({key:row[key] for key in ('id','conclusion','expectation_matched','elapsed_seconds')}),flush=True)
        (args.output/'results.json').write_text(json.dumps(list(results.values()),indent=2))
    summary=dict(case_count=len(results),matched=sum(r['expectation_matched'] for r in results.values()),
        unexpected=[name for name,row in results.items() if not row['expectation_matched']],
        conclusions={label:sum(r['conclusion']==label for r in results.values())
            for label in sorted({r['conclusion'] for r in results.values()})},
        elapsed_seconds=time.monotonic()-started,scope=provenance['scope'],
        self_intersection='NOT_EVALUATED')
    (args.output/'summary.json').write_text(json.dumps(summary,indent=2))
    print(json.dumps(summary),flush=True)
    return 0 if not summary['unexpected'] else 1


if __name__=='__main__':
    raise SystemExit(main())
