"""Per-print-part geometric overhang; print orientation is the only design variable."""
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
    if not all(k in config for k in ('overhang_threshold_from_horizontal_deg','layer_height_mm')):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NEEDS_SPEC: overhang angle and bed exclusion')
    if not 0 < config['overhang_threshold_from_horizontal_deg'] < 90 or config['layer_height_mm']<=0:
        raise ValueError('invalid overhang measurement configuration')
    report,inputs,meshes=load_parts(args)
    parts={p['id']:p for p in report['parts']}
    rows=[]; findings=[]
    for input_row in inputs:
        name=input_row['part_id']
        if name not in meshes:
            rows.append(input_row);continue
        measured,colored=measure_part(meshes[name],parts[name]['print_transform_mm'],config)
        colored.export(args.output/f'{name}.overhang.glb')
        write_json(args.output/f'{name}.regions.json',measured)
        regions=[{k:v for k,v in r.items() if k!='global_face_ids'} for r in measured.pop('regions')[:3]]
        rows.append(dict(part_id=name,status='PASS',**measured,largest_regions=regions))
        if config.get('orientation_editable',False) and measured['area_mm2']>measured['uncertainty']['bound_mm2']:
            findings.append(finding(NAME,'PRINT_ORIENTATION_OPPORTUNITY',
                f'{name}: geometric overhang remains; consider print rotation only, not assembly/shape edits.',
                part_ids=[name],frame=f'print:{name}',category='optimization_opportunity',
                repairability='design_variable',required=False,domain={
                    'area_mm2':measured['area_mm2'],'largest_regions':regions,
                    'print_transform_mm':measured['print_transform_mm'],
                    'overlay':str(args.output/f'{name}.overhang.glb')}))
    complete=bool(rows) and all(r['status']=='PASS' for r in rows)
    return CheckerResult(checker=NAME,status='PASS' if complete else 'INDETERMINATE',
        summary='Measurement complete; not support-free or printability approval' if complete else 'Partial measurement; total unknown',
        metrics={'items':rows,'total_overhang_mm2':sum(r['area_mm2'] for r in rows) if complete else None,
            'area_uncertainty_mm2':sum(r['uncertainty']['bound_mm2'] for r in rows) if complete else None,
            'measurement_config_sha256':hashlib.sha256(json.dumps(config,sort_keys=True).encode()).hexdigest(),
            'support_material':'NOT_EVALUATED'},findings=findings if complete else [],
        assumptions={'scale':'manifest mm; no normalization','surface':'within-part Manifold exterior; no cross-part union',
            'scope':'geometric overhang, not bed stability, slicer support volume or printing success'})

if __name__=='__main__': cli(NAME,analyze)
