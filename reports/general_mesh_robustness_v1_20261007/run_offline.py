"""Fixed-source offline evidence, no model calls or benchmark launch.

Run from a checkout with its packages on PYTHONPATH. Output goes in a NEW
specified directory; source inputs are only copied. Source-specific labels
below select regression fixtures, never production geometry policies.
"""
from pathlib import Path
import argparse
import hashlib
import importlib
import json
import runpy
import shutil
import subprocess
import time

import numpy as np

from adsl.core import Asset, Cube, boolean_union, translate_shape
from adsl.core.export.mesh64 import evaluate_shape
from adsl.core.export.mesh_validity import MeshEvaluationError, mesh_metrics, target_mesh, validate_written_mesh
from adsl.core.export.export_assembly import export_assembly
from adsl.core.assembly_topology import part_measurement, read_print_mesh
from adsl.agents.source_index import build_source_index
from adsl.agents.fixed_assembly import _failure_feedback
from adsl.agents.assembly_topology import checker_spec, run_assembly_topology, evaluation_evidence_run
from adsl.agents.utils.execution import ExecutionResult


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=lambda x:x.tolist() if isinstance(x,np.ndarray) else x.item())+'\n')


def geometry_probe(shape, root):
    root.mkdir(parents=True, exist_ok=True)
    start=time.monotonic()
    try:
        result=evaluate_shape(shape)
        row={'internal_evaluation':{'status':'PASS','seconds':time.monotonic()-start,
            'records':result.records},'pieces':[]}
        for i,piece in enumerate(result.pieces):
            mesh=piece.world_mesh()
            raw=piece.solid.to_mesh64()
            np.savez(root/f'internal_local_{i}.npz',vertices=raw.vert_properties[:,:3],
                faces=raw.tri_verts, transform=piece.transform)
            metrics=mesh_metrics(raw.vert_properties[:,:3],raw.tri_verts)
            local=piece.transform.copy();local[:3,3]=0
            start=time.monotonic()
            try:
                canonical,tf,record=target_mesh(piece.solid.transform(np.ascontiguousarray(local[:3])),
                    node_path=piece.node_path,operation=piece.primitive_type.upper(),
                    input_count=piece.records[-1].get('input_count',1))
                precision=dict(status='PASS',seconds=time.monotonic()-start,record=record)
            except MeshEvaluationError as error:
                precision=dict(status='FAIL',seconds=time.monotonic()-start,diagnostic=error.diagnostic)
            # Independent diagnostic of Mesh64 -> ASCII STL. This does not
            # publish a manufacturing candidate whose GLB precision failed.
            path=root/f'mesh64_diagnostic_{i}.stl'
            mesh.export(path,file_type='stl_ascii')
            try:
                actual,solid,readback=validate_written_mesh(path)
                measured,_=part_measurement(actual,f'piece_{i}')
                stl=dict(status='PASS',readback=readback,checker=measured)
            except (ValueError,RuntimeError) as error:
                stl=dict(status='FAIL',reason=str(error))
            row['pieces'].append(dict(node_path=piece.node_path,transform=piece.transform.tolist(),
                local_metrics=metrics,volume_scene_units3=float(piece.solid.volume()),
                target_precision=precision,ascii_stl_diagnostic=stl))
    except MeshEvaluationError as error:
        row={'internal_evaluation':{'status':'FAIL','seconds':time.monotonic()-start,
                                  'diagnostic':error.diagnostic}}
    save(root/'probe.json',row)
    return row


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    repo=Path(__file__).resolve().parents[2]
    output=args.output.resolve()
    output.mkdir(parents=True,exist_ok=False)
    lamp_source=repo/'reports/benchmark_six_boolean_failure_20261007/source.py'
    lamp_output=output/'lamp';lamp_output.mkdir()
    source=lamp_output/'source.py';shutil.copy2(lamp_source,source)
    ns=runpy.run_path(str(source))
    arm=ns['CurvedArm'](ns['UpperStructure']().offset_lamp_head)
    summary=dict(code_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip(),
        source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),
        scope='offline fixed-source Mesh64/export/checker evidence; no model API, FEA or benchmark resume',
        code_files_sha256={str(p.relative_to(repo)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in [repo/'adsl-core/core/export'/f for f in
                ('mesh64.py','mesh_validity.py','export_glb.py','export_assembly.py')]
                +[repo/'adsl-agents'/f for f in ('fixed_assembly.py','assembly_topology.py')]})
    summary['arm']=geometry_probe(arm,lamp_output/'arm')
    summary['upper']=geometry_probe(ns['assembly'].parts['upper_structure'],lamp_output/'upper')
    summary['translations']={}
    for name,offset in [('origin',(0,0,-138.8)),('original',(0,0,0)),('large',(1e7,2e7,-3e7))]:
        summary['translations'][name]=geometry_probe(translate_shape(arm,offset),lamp_output/'translations'/name)
    config=json.loads((repo/'reports/general_mesh_robustness_v1_20261007/frozen_geometry_config.json').read_text())
    asset=lamp_output/'asset';assembly_dir=asset/'assembly'
    start=time.monotonic()
    manifest=export_assembly(ns['assembly'],assembly_dir,source_sha256=summary['source_sha256'],expected=config)
    summary['assembly']=dict(status=manifest['status'],export_status=manifest.get('export_status'),
        seconds=time.monotonic()-start,files_sha256=manifest.get('files_sha256'),failures=manifest['failures'])
    index=asset/'source_index.json';index.parent.mkdir(exist_ok=True)
    index.write_text(build_source_index(source,ns['scene']).model_dump_json(indent=2))
    execution=ExecutionResult(asset,assembly_dir/'scene.glb',None,(),'','',source_index_path=index)
    topology=run_assembly_topology(checker_spec(120),execution=execution,source=source,root=lamp_output/'verification')
    summary['topology']=dict(status=topology.result.status,summary=topology.result.summary,
                            report=str(topology.output_dir/'report.json'))
    feedback=_failure_feedback(manifest,source_sha256=summary['source_sha256'])
    evidence=evaluation_evidence_run(manifest,feedback,source,lamp_output/'evaluation_feedback',index)
    summary['evaluation_feedback']=dict(failures=feedback,
        findings=[] if evidence is None else [f.model_dump() for f in evidence.result.findings])
    trunk_source=repo/'tests/fixtures/elephant_trunk.py'
    trunk=runpy.run_path(str(trunk_source))['scene']
    summary['trunk']=geometry_probe(trunk,output/'elephant_trunk')
    shutil.copy2(trunk_source,output/'elephant_trunk/source.py')
    # Minimize a failing prefix without changing any retained operand geometry.
    operands=list(arm.tube._parts.items())
    first=None
    for count in range(2,len(operands)+1):
        selected=boolean_union(*(shape.copy() for _,shape in operands[:count]))
        row=geometry_probe(selected,output/'minimal_prefix'/str(count))
        if row.get('pieces') and row['pieces'][0]['target_precision']['status']=='FAIL':
            first=dict(operand_count=count,original_operand_names=[n for n,_ in operands[:count]],
                scope='smallest failing prefix; not a proof of globally minimum subset')
            active=operands[:count]
            for candidate in list(active):
                trial=[pair for pair in active if pair[0]!=candidate[0]]
                if len(trial)<2: continue
                try:
                    piece=evaluate_shape(boolean_union(*(shape.copy() for _,shape in trial))).pieces[0]
                    linear=piece.transform.copy();linear[:3,3]=0
                    target_mesh(piece.solid.transform(np.ascontiguousarray(linear[:3])))
                except MeshEvaluationError as error:
                    if error.diagnostic['code']=='TARGET_PRECISION_UNREPRESENTABLE':active=trial
            first.update(reduced_operand_count=len(active), reduced_operand_names=[n for n,_ in active],
                reduction='one greedy deletion pass preserving original operands; local reduction, not global minimum')
            save(output/'minimal_prefix/operands.json',[shape.to_dict() for _,shape in active])
            geometry_probe(boolean_union(*(shape.copy() for _,shape in active)),output/'minimal_prefix/reduced')
            break
    summary['small_reproduction']=first
    save(output/'summary.json',summary)
    save(output/'provenance.json',dict(code_sha=summary['code_sha'],base_commit='f78e846b36899b72273f4fb80bd64e99a613db1c',
        branch='codex/mesh-validity-v1',sources={str(lamp_source):summary['source_sha256'],
        str(trunk_source):hashlib.sha256(trunk_source.read_bytes()).hexdigest()}))
    print(json.dumps(dict(output=str(output),assembly=summary['assembly']['export_status'],
                         topology=summary['topology']['status'],small_reproduction=first)))

if __name__=='__main__':main()
