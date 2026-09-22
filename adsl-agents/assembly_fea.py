"""Typed assembly FEA adapter. Self-weight standing is NOT a prerequisite."""
from __future__ import annotations
import os
import subprocess
from pathlib import Path
import time
from .assembly_physics import cli,checker_spec as _spec,load_parts,finding
from .models import CheckerResult
from .utils.io import write_json

NAME='assembly_fea'
def checker_spec(timeout_seconds=900,**kwargs):return _spec(NAME,timeout_seconds,**kwargs)


def analyze(args,physics):
    from adsl.core.assembly_topology import mesh_solid,interface_measurement
    config=physics.get('fea',{});material=physics.get('material',{})
    needed=('mesh_size_mm','supports','loads','allowable_stress_pa','allowable_displacement_m','include_gravity')
    if (any(k not in config for k in needed) or not config.get('supports') or
            any(k not in material for k in ('youngs_modulus_pa','poisson_ratio','density_kg_m3'))):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NEEDS_SPEC: explicit material, load, support and limits required')
    if min(config['mesh_size_mm'],config['allowable_stress_pa'],config['allowable_displacement_m'],material['youngs_modulus_pa'],material['density_kg_m3'])<=0 or not -1<material['poisson_ratio']<.5:
        raise ValueError('invalid material or FEA specification')
    report,rows,meshes=load_parts(args)
    if not rows or any(r['status']!='PASS' for r in rows):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='DEPENDENCY_MESH_UNAVAILABLE; FEA unverified',metrics={'items':rows})
    parts={p['id']:p for p in report['parts']};solids={n:mesh_solid(m) for n,m in meshes.items()}
    interfaces=[interface_measurement(c,parts,solids,report['mm_per_unit']) for c in report['connections']]
    if any(r['status']!='PASS' for r in interfaces):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='DEPENDENCY_INTERFACE_UNVERIFIED; no tie used to hide a failed mate',metrics={'items':interfaces})
    from experiments.load_bearing_structural_performance.assembly import mesh_part,remap_parts,tie_regions,write_assembly_deck,run_ccx
    local={};stages=[]
    for name,mesh in meshes.items():
        stages.append(dict(part_id=name,stage='volume_mesh',status='RUNNING'));write_json(args.output/'stages.json',stages)
        try:
            local[name]=mesh_part(mesh,args.output/f'{name}.inp',config['mesh_size_mm'])
        except Exception as error:
            # Gmsh's Python binding raises plain Exception for native errors.
            # Do not relabel unrelated Python programming failures as meshing.
            if not isinstance(error,(ValueError,RuntimeError)) and type(error) is not Exception:
                raise
            stages[-1].update(status='INDETERMINATE',reason=str(error)[:300]);write_json(args.output/'stages.json',stages)
            return CheckerResult(checker=NAME,status='INDETERMINATE',summary=f'{name}: MESH_INVALID' if 'MESH_INVALID' in str(error) else f'{name}: MESH_GENERATION_FAILED',metrics={'items':stages})
        stages[-1].update(status='PASS',**local[name][2]);write_json(args.output/'stages.json',stages)
    nodes,elements,mapping=remap_parts(local,report)
    ties=tie_regions(report,nodes,mapping)
    deck=args.output/'assembly.inp'
    regions=write_assembly_deck(deck,nodes,elements,mapping,material,ties,config['supports'],config['loads'],config['include_gravity'])
    write_json(args.output/'regions_and_ties.json',regions);write_json(args.output/'part_mapping.json',mapping)
    ccx=Path(os.environ.get('ADSL_CCX_BIN','ccx'))
    write_json(args.output/'stage.json',{'stage':'solve','status':'RUNNING'})
    try:
        solved=run_ccx(ccx,deck,config.get('solver_timeout_seconds',120))
    except subprocess.TimeoutExpired:
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='SOLVER_TIMEOUT; structural performance unverified',artifacts={'deck':str(deck)})
    warnings=list(args.output.glob('*WarnNodeMissMasterIntersect*'))+list(args.output.glob('*WarnNodeMissTiedContact*'))
    warnings=[p for p in warnings if any(line.strip() and not line.lstrip().startswith('*') for line in p.read_text().splitlines())]
    solver_log=(args.output/'assembly.stdout.log').read_text(errors='replace')
    if warnings or 'no tied MPC' in solver_log or 'no tied constraint' in solver_log:
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='INTERFACE_BINDING_UNVERIFIED: unmatched slave nodes',metrics={'solver':solved},artifacts={'deck':str(deck),'unmatched_nodes':str(warnings[0]) if warnings else str(args.output/'assembly.stdout.log')})
    if solved['status']!='SOLVED' or solved.get('max_von_mises_pa') is None:
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='SOLVER_FAILED; structural performance unverified',metrics=solved,artifacts={'deck':str(deck)})
    import numpy as np
    from experiments.load_bearing_structural_performance.assembly import result_fields,transfer_check,field_report
    u,rf,stress=result_fields(deck.with_suffix('.dat'))
    if set(u)!=set(nodes) or set(stress)!=set(elements) or not all(np.isfinite(v).all() for v in [*u.values(),*rf.values(),*stress.values()]):
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NUMERICAL_RESULT_UNVERIFIED: missing or nonfinite fields')
    # The legacy single-object parser does not request RF; here U/RF share a
    # four-column layout. Only the explicit displacement block defines U.
    un=max(u,key=lambda n:np.linalg.norm(u[n]));se=max(stress,key=stress.get)
    solved.update(max_displacement_m=float(np.linalg.norm(u[un])),max_displacement_node=un,
                  max_von_mises_pa=stress[se],max_von_mises_element=se)
    transfer=transfer_check(nodes,elements,mapping,regions,material,config['include_gravity'],u,rf)
    write_json(args.output/'transfer_check.json',transfer)
    solved['transfer_check']=transfer
    if not transfer['reaction_balanced'] or not transfer['interfaces_compatible']:
        return CheckerResult(checker=NAME,status='INDETERMINATE',summary='NUMERICAL_RESULT_UNVERIFIED: interface/reaction residual',metrics=solved)
    solved['items']=field_report(args.output,nodes,elements,mapping,u,stress)
    findings=[]
    for metric,limit,ids_key,key in [('max_displacement_m','allowable_displacement_m','node_ids','max_displacement_node'),
                                   ('max_von_mises_pa','allowable_stress_pa','element_ids','max_von_mises_element')]:
        ident=solved[key];part=next(n for n,p in mapping.items() if ident in p[ids_key])
        position=nodes[ident] if ids_key=='node_ids' else np.mean([nodes[n] for n in elements[ident][:4]],axis=0)
        solved[metric+'_location']=dict(part_id=part,entity_id=ident,position_mm=(position*1000).tolist())
        if solved[metric]>config[limit]:
            findings.append(finding(NAME,metric.upper(),f'{part}: {metric}={solved[metric]:.6g} exceeds {config[limit]:.6g}',
                part_ids=[part],bounds=[(position*1000).tolist()]*2,domain={'value':solved[metric],'limit':config[limit],
                    'position_mm':(position*1000).tolist(),'entity_id':ident,'condition':'self-weight + configured functional loads; ideal tie',
                    'field_plot':str(args.output/('displacement_mm.png' if ids_key=='node_ids' else 'von_mises_mpa.png'))}))
    return CheckerResult(checker=NAME,status='FAIL' if findings else 'PASS',summary='Specified-mesh elastic screening; convergence NOT_EVALUATED',
        metrics=solved,findings=findings,assumptions={'connection_model':'ideal_bonded','external_load_stability':'NOT_EVALUATED',
            'mesh_convergence':'NOT_EVALUATED','material_model':'isotropic equivalent linear elastic',
            'configuration':config,'material':material},artifacts={'deck':str(deck),'mapping':str(args.output/'part_mapping.json')})

if __name__=='__main__':cli(NAME,analyze)
