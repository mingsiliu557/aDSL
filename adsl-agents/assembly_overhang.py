"""Per-print-part overhang with optional frozen-reference partition scoring."""
from __future__ import annotations
import hashlib
import json
import numpy as np
from .assembly_physics import cli, checker_spec as _spec, load_parts, finding
from .models import CheckerResult
from .utils.io import write_json

NAME='assembly_overhang'
def checker_spec(timeout_seconds=900, **kwargs):
    return _spec(NAME,timeout_seconds,**kwargs)


def measure_part(mesh, transform, config):
    from experiments.support_requirement_critical_surfaces.analyze import overhang_mask, area_uncertainty, geometric_regions
    printed=mesh.copy(); printed.apply_transform(np.asarray(transform,dtype=float))
    mask=overhang_mask(printed,config['overhang_threshold_from_horizontal_deg'],config['layer_height_mm'])
    regions=geometric_regions(printed,mask)
    uncertainty=area_uncertainty(printed,config)
    colored=printed.copy()
    colored.visual.face_colors=np.where(mask[:,None],[[220,45,45,255]],[[170,180,190,255]])
    return dict(area_mm2=float(printed.area_faces[mask].sum()),uncertainty=uncertainty,
                regions=regions,print_transform_mm=np.asarray(transform).tolist()),colored


def analyze(args,physics):
    config=physics.get('overhang',{})
    partition = config.get('partition_objective')
    area_available = all(k in config for k in ('overhang_threshold_from_horizontal_deg','layer_height_mm'))
    if partition is None and not area_available:
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NEEDS_SPEC: overhang angle and bed exclusion')
    if area_available and (not 0 < config['overhang_threshold_from_horizontal_deg'] < 90 or config['layer_height_mm']<=0):
        raise ValueError('invalid overhang measurement configuration')
    report,inputs,meshes=load_parts(args)
    parts={p['id']:p for p in report['parts']}
    rows=[]; findings=[]
    for input_row in inputs:
        name=input_row['part_id']
        if name not in meshes:
            rows.append(input_row);continue
        if not area_available:
            rows.append(dict(part_id=name,status='PASS',area_mm2=None,uncertainty={'bound_mm2':None}))
            continue
        measured,colored=measure_part(meshes[name],parts[name]['print_transform_mm'],config)
        colored.export(args.output/f'{name}.overhang.glb')
        write_json(args.output/f'{name}.regions.json',measured)
        regions=[{k:v for k,v in r.items() if k!='global_face_ids'} for r in measured.pop('regions')[:3]]
        rows.append(dict(part_id=name,status='PASS',**measured,largest_regions=regions))
        if partition is None and config.get('orientation_editable',False) and measured['area_mm2']>measured['uncertainty']['bound_mm2']:
            findings.append(finding(NAME,'PRINT_ORIENTATION_OPPORTUNITY',
                f'{name}: geometric overhang remains; consider print rotation only, not assembly/shape edits.',
                part_ids=[name],frame=f'print:{name}',category='optimization_opportunity',
                repairability='design_variable',required=False,domain={
                    'area_mm2':measured['area_mm2'],'largest_regions':regions,
                    'print_transform_mm':measured['print_transform_mm'],
                    'overlay':str(args.output/f'{name}.overhang.glb')}))
    complete=bool(rows) and all(r['status']=='PASS' for r in rows)
    result = CheckerResult(checker=NAME,status='PASS' if complete else 'INDETERMINATE',
        summary='Measurement complete; not support-free or printability approval' if complete else 'Partial measurement; total unknown',
        metrics={'items':rows,'total_overhang_mm2':sum(r['area_mm2'] for r in rows) if complete and area_available else None,
            'area_uncertainty_mm2':sum(r['uncertainty']['bound_mm2'] for r in rows) if complete and area_available else None,
            'measurement_config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
            'support_material':'NOT_EVALUATED'},findings=findings if complete else [],
        assumptions={'scale':'manifest mm; no normalization','surface':'within-part Manifold exterior; no cross-part union',
            'scope':'geometric overhang, not bed stability, slicer support volume or printing success'})

    if partition is not None:
        return partition_measurement(args,physics,report,meshes,result,area_available)
    return result


def partition_measurement(args, physics, report, meshes, result, area_available):
    from pathlib import Path
    from .feedback_schema import sha256_file
    from .partition_score import (objective_config, create_reference, load_reference,
                                  best_print_pose, score_partition)
    config = physics['overhang']
    objective = objective_config(config['partition_objective'])
    reference = None
    try:
        path = physics.get('partition_reference_path')
        if not path:
            path = create_reference(args.manifest, args.output/'references', objective)
        reference = load_reference(path, objective)
        result.artifacts['partition_reference'] = str(Path(path).resolve())
    except (ValueError, KeyError, OSError, RuntimeError) as error:
        result.metrics['partition_reference_error'] = f'{type(error).__name__}: {str(error)[:240]}'
    layouts = []
    for row in result.metrics['items']:
        name = row['part_id']
        row['input_pose_area_mm2'] = row.get('area_mm2')
        row['gap_voxels'] = None
        if name not in meshes or reference is None:
            continue
        try:
            measured = best_print_pose(meshes[name], reference)
            row.update(measured)
            printed = meshes[name].copy()
            printed.apply_transform(measured['recommended_print_transform_mm'])
            path = args.output/f'{name}.recommended.stl'
            printed.export(path, file_type='stl_ascii')
            if area_available:
                area,colored = measure_part(meshes[name], measured['recommended_print_transform_mm'], config)
                row['recommended_pose_area_mm2'] = area['area_mm2']
                colored.export(args.output/f'{name}.recommended_pose.overhang.glb')
                write_json(args.output/f'{name}.recommended_pose.regions.json', area)
            layouts.append(dict(part_id=name, stl=str(path.resolve()), stl_sha256=sha256_file(path),
                mesh_sha256=sha256_file(args.output/f'{name}.solid.npz'),
                **{k:v for k,v in measured.items() if k != 'orientations'}))
        except (ValueError, RuntimeError, OSError) as error:
            row.update(status='INDETERMINATE', gap_voxels=None, reason=str(error)[:240])
        write_json(args.output/'partition_progress.json', {'items':result.metrics['items']})
    count = len(report.get('part_declarations',report['parts']))
    score = score_partition(result.metrics['items'], reference, part_count=count, config=objective)
    result.metrics['partition_objective'] = score
    result.status = 'PASS' if score['score'] is not None else 'INDETERMINATE'
    result.summary = ('Partition measurement complete; vertical-gap proxy, not slicer support or printability approval'
                      if result.status=='PASS' else 'Partition measurement incomplete; total gap and score unknown')
    layout = dict(status=result.status, source_sha256=report['source_sha256'],
        manifest_sha256=sha256_file(args.manifest), reference_sha256=score['reference_sha256'],
        evaluation_config_sha256=score['evaluation_config_sha256'], parts=layouts)
    write_json(args.output/'print_layout.json', layout)
    result.artifacts['print_layout'] = str((args.output/'print_layout.json').resolve())
    result.assumptions.update(partition_scope='Dapper objective; independent beds; 24 rotations; no packing/refinement',
        limitations='Vertical empty voxel proxy; not actual support/time, bridging, removability or bed stability. Coarse voxels may mask connectors.')
    if result.status == 'PASS' and config.get('partition_editable',False):
        result.findings.append(finding(NAME,'PRINT_PARTITION_OPPORTUNITY',
            'Evaluate at most one supported local split/merge, including at zero gap. Preserve pre-connector body shape and update every affected interface; NO_PROPOSAL is valid.',
            part_ids=[r['part_id'] for r in result.metrics['items']], frame='assembly',
            category='optimization_opportunity', repairability='design_variable', required=False,
            domain=dict(partition_objective=score, items=result.metrics['items'])))
    return result

if __name__=='__main__': cli(NAME,analyze)
