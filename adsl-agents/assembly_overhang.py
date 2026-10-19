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


def _display_overlay(mesh, path):
    """Optional float32 visualization; never gate a float64 measurement."""
    import trimesh
    from adsl.core.export.mesh_validity import mesh_metrics
    try:
        mesh.export(path)
        saved = trimesh.load(path, force='scene', process=False)
        values = []
        for name in saved.graph.nodes_geometry:
            transform, geometry = saved.graph[name]
            item = saved.geometry[geometry].copy()
            item.apply_transform(np.asarray(transform, dtype=np.float64))
            values.append(item)
        if not values:
            raise ValueError('empty saved overlay')
        actual = trimesh.util.concatenate(values)
        metrics = mesh_metrics(actual.vertices, actual.faces)
        if not metrics['valid']:
            return dict(status='FAIL', file=str(path), metrics=metrics,
                        reason='saved display overlay mesh invalid; material measurement unchanged')
        return dict(status='PASS', file=str(path), metrics=metrics)
    except (ValueError, RuntimeError, OSError) as error:
        return dict(status='FAIL', file=str(path), reason=str(error)[:240])


def _verify_recommended_stl(path, printed):
    """Read the actual ASCII STL and verify its mesh and exact print pose."""
    import trimesh
    from adsl.core.export.mesh_validity import validate_mesh
    loaded = trimesh.load_mesh(path, process=False)
    _, metrics = validate_mesh(loaded.vertices, loaded.faces, stage='recommended_print_readback')
    if not np.array_equal(loaded.triangles, printed.triangles):
        raise ValueError('recommended ASCII STL does not match the measured print mesh/pose')
    return dict(status='PASS', stage='recommended_print_readback', precision='ASCII float64',
                exact_triangles_match=True, metrics=metrics)


def analyze(args,physics):
    config=physics.get('overhang',{})
    from .support_volume import support_options
    support_metric, _ = support_options(config)
    partition = config.get('partition_objective')
    area_available = all(k in config for k in ('overhang_threshold_from_horizontal_deg','layer_height_mm'))
    if (partition is None or support_metric == 'nsv') and not area_available:
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
        overlay = _display_overlay(colored, args.output/f'{name}.overhang.glb')
        write_json(args.output/f'{name}.regions.json',measured)
        regions=[{k:v for k,v in r.items() if k!='global_face_ids'} for r in measured.pop('regions')[:3]]
        rows.append(dict(part_id=name,status='PASS',**measured,largest_regions=regions,
                         display_overlay=overlay))
        if support_metric == 'area' and partition is None and config.get('orientation_editable',False) and measured['area_mm2']>measured['uncertainty']['bound_mm2']:
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
    if support_metric == 'nsv':
        _support_measurements(args, config, report, meshes, result, recommended=False)
    return result


def partition_measurement(args, physics, report, meshes, result, area_available):
    from pathlib import Path
    from .feedback_schema import sha256_file
    from .partition_score import (objective_config, create_reference, load_reference,
                                  best_print_pose, score_partition, partition_score_guidance)
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
            print_validation = _verify_recommended_stl(path, printed)
            row['recommended_print_validation'] = print_validation
            if area_available:
                area,colored = measure_part(meshes[name], measured['recommended_print_transform_mm'], config)
                row['recommended_pose_area_mm2'] = area['area_mm2']
                row['recommended_pose_display_overlay'] = _display_overlay(
                    colored, args.output/f'{name}.recommended_pose.overhang.glb')
                regions_path = args.output/f'{name}.recommended_pose.regions.json'
                write_json(regions_path, area)
                row['recommended_pose_regions'] = dict(frame=f'recommended_print:{name}', length_unit='mm',
                    part_local_to_print_mm=measured['recommended_print_transform_mm'],
                    regions=[{k:r[k] for k in ('area_mm2','bounds_mm','centroid_mm') if k in r}
                             for r in area['regions'][:3]],
                    evidence_ref={'path':str(regions_path.resolve()),'json_pointer':'/regions'})
            layouts.append(dict(part_id=name, stl=str(path.resolve()), stl_sha256=sha256_file(path),
                mesh_sha256=sha256_file(args.output/f'{name}.solid.npz'),
                file_validation=print_validation,
                **{k:v for k,v in measured.items() if k != 'orientations'}))
        except (ValueError, RuntimeError, OSError) as error:
            row.update(status='INDETERMINATE', gap_voxels=None, reason=str(error)[:240])
        write_json(args.output/'partition_progress.json', {'items':result.metrics['items']})
    count = len(report.get('part_declarations',report['parts']))
    score = score_partition(result.metrics['items'], reference, part_count=count, config=objective)
    result.metrics['partition_objective'] = score
    result.metrics['partition_guidance'] = partition_score_guidance(score)
    result.status = 'PASS' if score['score'] is not None else 'INDETERMINATE'
    result.summary = ('Partition measurement complete; vertical-gap proxy, not slicer support or printability approval'
                      if result.status=='PASS' else 'Partition measurement incomplete; total gap and score unknown')
    if config.get('support_metric', 'area') == 'nsv':
        _support_measurements(args, config, report, meshes, result, recommended=True)
    layout = dict(status=result.status, source_sha256=report['source_sha256'],
        manifest_sha256=sha256_file(args.manifest), reference_sha256=score['reference_sha256'],
        evaluation_config_sha256=score['evaluation_config_sha256'], parts=layouts)
    write_json(args.output/'print_layout.json', layout)
    result.artifacts['print_layout'] = str((args.output/'print_layout.json').resolve())
    result.assumptions.update(partition_scope='Dapper objective; independent beds; 24 rotations; no packing/refinement',
        limitations='Vertical empty voxel proxy; not actual support/time, bridging, removability or bed stability. Coarse voxels may mask connectors.')
    if result.status == 'PASS' and config.get('partition_editable',False):
        result.findings.append(finding(NAME,'PRINT_PARTITION_OPPORTUNITY',
            ('Evaluate one authorized grouping hypothesis using measured support S and part count N; NO_PROPOSAL is valid.'
             if config.get('support_metric', 'area') == 'nsv' else
             'Evaluate at most one supported local split/merge, including at zero gap. Preserve pre-connector body shape and update every affected interface; NO_PROPOSAL is valid.'),
            part_ids=[r['part_id'] for r in result.metrics['items']], frame='assembly',
            category='optimization_opportunity', repairability='design_variable', required=False,
            domain=({'support':result.metrics['support'], 'part_count':count}
                if config.get('support_metric', 'area') == 'nsv' else
                dict(partition_objective=score, partition_guidance=result.metrics['partition_guidance'], items=result.metrics['items']))))
    return result


def _support_measurements(args, config, report, meshes, result, *, recommended):
    from .support_volume import (support_volume, aggregate, SupportMeasurementError,
                                ALGORITHM_VERSION, SELF_HIT_RELATIVE_EPSILON)
    parts = {p['id']:p for p in report.get('parts', [])}
    declarations = report.get('part_declarations')
    rows = result.metrics['items']
    for row in rows:
        name = row['part_id']
        row['support'] = dict(status='INDETERMINATE', reason='print_part_or_pose_unavailable')
        if row.get('status') != 'PASS' or name not in meshes:
            continue
        transform = (row.get('recommended_print_transform_mm') if recommended else
                     parts.get(name, {}).get('print_transform_mm'))
        if transform is None:
            continue
        printed = meshes[name].copy()
        printed.apply_transform(np.asarray(transform, dtype=float))
        bed_offset = -float(printed.bounds[0, 2])
        measured_transform = np.asarray(transform, dtype=float).copy()
        measured_transform[2, 3] += bed_offset
        printed.apply_translation((0, 0, bed_offset))
        try:
            row['support'] = support_volume(printed,
                overhang_threshold_from_horizontal_deg=config['overhang_threshold_from_horizontal_deg'],
                bed_tolerance_mm=config['layer_height_mm'])
        except SupportMeasurementError as error:
            row['support'] = dict(status='INDETERMINATE', reason=str(error)[:240])
        row['support_print_transform_mm'] = measured_transform.tolist()
        row['support_bed_translation_mm'] = bed_offset
        row['support_frame'] = f'support_print:{name}'
        if row['support']['status'] == 'PASS':
            area, _ = measure_part(meshes[name], measured_transform, config)
            row['support_regions'] = [
                {k:r[k] for k in ('area_mm2','bounds_mm','centroid_mm') if k in r}
                for r in area['regions'][:3]]
            detail = dict(support=row['support'], print_transform_mm=measured_transform.tolist(),
                          regions=area['regions'], source_sha256=report.get('source_sha256'))
            path = args.output/f'{name}.support.json'
            write_json(path, detail)
            row['support_evidence_ref'] = dict(path=str(path.resolve()), json_pointer='',
                                               source_sha256=report.get('source_sha256'))
    measured = [r.get('support') for r in rows]
    # Missing declared pieces must not disappear through a successful subset.
    ids = [p.get('id') for p in declarations or []]
    row_ids = [r['part_id'] for r in rows]
    if (not ids or len(ids) != len(set(ids)) or len(row_ids) != len(set(row_ids)) or
            set(ids) != set(row_ids) or set(ids) != set(parts)):
        measured.append(None)
    summary = aggregate(measured)
    result.metrics['support'] = summary
    contract = dict(algorithm=ALGORITHM_VERSION,
        overhang_threshold_from_horizontal_deg=config['overhang_threshold_from_horizontal_deg'],
        bed_tolerance_mm=config['layer_height_mm'], mask_bed_epsilon_mm=1e-8,
        self_hit_relative_epsilon=SELF_HIT_RELATIVE_EPSILON,
        ray_backends=sorted({r['support']['ray_backend'] for r in rows if r.get('support',{}).get('status')=='PASS'}))
    result.metrics['support_measurement_config'] = contract
    result.metrics['support_measurement_config_sha256'] = hashlib.sha256(
        json.dumps(contract,sort_keys=True,allow_nan=False).encode()).hexdigest()
    result.assumptions['support_scope'] = 'Centroid downward-ray volume proxy; not slicer material; fixed manifest mm scale'
    if summary['status'] != 'PASS':
        result.status = 'INDETERMINATE'
        result.summary = 'Support measurement incomplete; no partial total'
        result.findings = [f for f in result.findings if f.category != 'optimization_opportunity']
        return
    result.summary = 'Support-volume proxy measured at the recorded print poses; not slicer consumption or minimum-support pose search'
    for row in rows:
        support = row['support']
        domain = dict(support=support, print_transform_mm=row['support_print_transform_mm'],
            largest_regions=row.get('support_regions',[]), evidence_ref=row.get('support_evidence_ref'),
            overlay=row.get('recommended_pose_display_overlay' if recommended else 'display_overlay'))
        if config.get('shape_editable',False) and support['support_volume_mm3'] > (
                config['layer_height_mm'] * support['risky_projected_area_mm2']):
            result.findings.append(finding(NAME,'SUPPORT_SHAPE_OPPORTUNITY',
                f'{row["part_id"]}: support proxy exceeds one baseline layer; consider an authorized local shape hypothesis without global shrinking.',
                part_ids=[row['part_id']], frame=row['support_frame'], category='optimization_opportunity',
                repairability='design_variable', required=False, domain=domain))
        if not recommended and config.get('orientation_editable',False) and support['support_volume_mm3'] > 0:
            result.findings.append(finding(NAME,'PRINT_ORIENTATION_OPPORTUNITY',
                f'{row["part_id"]}: consider an authorized print rotation; adoption requires support-volume gain.',
                part_ids=[row['part_id']], frame=row['support_frame'], category='optimization_opportunity',
                repairability='design_variable', required=False, domain=domain))

if __name__=='__main__': cli(NAME,analyze)
