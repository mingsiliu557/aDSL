"""Fixed-assembly adapters sharing the existing checker process boundary.

No model execution, scaling normalization, whole-assembly union or new scheduler.
Optional native dependencies are imported only inside the selected tool process.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import time

from .checkers import CheckerRun, run_checker
from .feedback_schema import sha256_file
from .models import CheckerSpec, CheckerResult, CheckerFinding, RegionEvidence
from .utils.io import read_json, write_json
from .utils.execution import ExecutionResult

NAMES = ('assembly_topology', 'assembly_standing', 'assembly_overhang', 'assembly_fea')


def checker_spec(name, timeout_seconds=900, *, prepend_environment=None):
    if name not in NAMES[1:]:
        raise ValueError(f'unsupported assembly physics tool: {name}')
    return CheckerSpec(name=name, required=name != 'assembly_overhang', timeout_seconds=timeout_seconds,
        prepend_environment=prepend_environment or {}, command=[
            '{python}', '-m', f'adsl.agents.{name}', '--manifest', '{asset_dir}/assembly_manifest.json',
            '--source', '{source}', '--output', '{output_dir}', '--source-index', '{source_index}',
            '--physics', '{round_dir}/assembly_physics.json',
            '--topology-cache', '{round_dir}/checkers/assembly_topology'])


def run_assembly_checks(specs, *, execution, source, root, physics, initial_topology_run=None):
    from .assembly_topology import run_assembly_topology
    write_json(root/'assembly_physics.json', physics)
    analysis = ExecutionResult(execution.output_root, execution.glb_path, None, (), '', '',
                               source_index_path=execution.source_index_path)
    runs = []
    # Topology's version-bound solids are optional reusable inputs, not a global gate.
    for spec in sorted(specs, key=lambda s:s.name != 'assembly_topology'):
        if spec.name == 'assembly_topology':
            run = initial_topology_run or run_assembly_topology(spec,execution=analysis,source=source,root=root)
        else:
            run = run_checker(spec,execution=analysis,source_path=source,round_root=root)
            if any(v.get('code') == 'CHECKER_TIMEOUT' for v in run.result.violations):
                write_json(run.output_dir/'execution_error.json', run.result.model_dump())
                stage={}
                for path in (run.output_dir/'stage.json',run.output_dir/'stages.json'):
                    if path.is_file():
                        try:
                            saved=read_json(path);stage=saved[-1] if isinstance(saved,list) and saved else saved
                        except (OSError,ValueError) as error:
                            stage={'report_unavailable':type(error).__name__}
                        break
                result = run.result.model_copy(update={'status':'INDETERMINATE',
                    'summary':f'{spec.name}: CHECKER_TIMEOUT; physical condition not evaluated',
                    'findings':[], 'metrics':{**run.result.metrics,'last_stage':stage},
                    'assumptions':{'source_sha256':sha256_file(source)}})
                write_json(run.output_dir/'result.json', result.model_dump())
                run = CheckerRun(spec,result,run.output_dir,run.command)
        runs.append(run)
    return runs


def load_parts(args):
    """Return local-mm final solids, keeping individual unmeasurable parts visible."""
    from .assembly_topology import _input, _part_file, _load_solid_mesh, _save_solid_mesh
    from adsl.core.assembly_topology import read_print_mesh, part_measurement, solid_mesh, mesh_solid
    report = _input(args.manifest, args.source)
    rows, meshes = [], {}
    cache = None
    if args.topology_cache and (args.topology_cache/'report.json').is_file():
        saved = read_json(args.topology_cache/'report.json')
        if (saved.get('source_sha256') == sha256_file(args.source) and
                saved.get('manifest_sha256') == sha256_file(args.manifest)):
            cache = {r.get('part_id'):r for r in saved.get('items',[]) if r.get('kind')=='part'}
    parts = {p['id']:p for p in report['parts']}
    for declaration in report.get('part_declarations', report['parts']):
        name = declaration['id']
        try:
            part = parts[name]
            _part_file(args.manifest, report, part)  # Always verify published input hash.
            cached = cache.get(name) if cache is not None else None
            path = (Path(cached['report_path']).parent/'solid.npz') if cached and cached.get('report_path') else None
            if (path and path.is_file() and cached.get('status') in ('PASS','FAIL') and
                    cached.get('solid_sha256')==sha256_file(path)):
                mesh = _load_solid_mesh(path)
                mesh_solid(mesh)  # Validate the lossless transport, never repair it.
                row = dict(cached)
            else:
                mesh = read_print_mesh(_part_file(args.manifest,report,part),part['print_transform_mm'])
                row, solid = part_measurement(mesh,name,part.get('mesh_face_groups'))
                mesh = solid_mesh(solid)
            meshes[name] = mesh
            _save_solid_mesh(mesh,args.output/f'{name}.solid.npz')
        except (ValueError, KeyError, OSError, RuntimeError) as error:
            row = dict(part_id=name,status='INDETERMINATE',code='PRINT_MESH_UNMEASURABLE',
                       stage='load_final_part',reason=f'{type(error).__name__}: {str(error)[:240]}')
        rows.append(row)
        write_json(args.output/'input_parts.json', {'source_sha256':sha256_file(args.source),'items':rows})
    return report, rows, meshes


def finding(name, code, message, *, part_ids=(), bounds=None, frame='assembly', domain=None,
            category='physical_violation', repairability='geometry', required=True):
    return CheckerFinding(finding_id=f'{name}:{code}:{":".join(part_ids)}',rule_id=code,
        category=category,repairability=repairability,required=required,message=message,
        region=RegionEvidence(kind='aabb' if bounds is not None else 'parts',frame=frame,
            unit='mm',bounds=bounds,part_names=list(part_ids)),domain=domain or {})


def cli(name, analyze):
    import sys
    sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
    p = argparse.ArgumentParser()
    for key in ('manifest','source','output','physics'):
        p.add_argument('--'+key,type=Path,required=True)
    p.add_argument('--source-index',default='')
    p.add_argument('--topology-cache',type=Path)
    args = p.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    started = time.monotonic()
    from .assembly_topology import source_candidates
    try:
        physics = read_json(args.physics)
        result = analyze(args,physics)
    except ImportError as error:
        result = CheckerResult(checker=name,status='INDETERMINATE',summary=f'DEPENDENCY_UNAVAILABLE: {error}',
                               violations=[{'code':'DEPENDENCY_UNAVAILABLE','stage':'dependency'}])
    except (ValueError,KeyError,OSError) as error:
        result = CheckerResult(checker=name,status='INDETERMINATE',summary=f'ANALYSIS_UNAVAILABLE: {str(error)[:240]}',
            violations=[{'code':'ANALYSIS_UNAVAILABLE','stage':'input_or_analysis','reason':str(error)[:240]}])
    # Unexpected exceptions escape: run_checker's ERROR preserves the traceback.
    result.assumptions.update(source_sha256=sha256_file(args.source),
        manifest_sha256=sha256_file(args.manifest) if args.manifest.is_file() else None,
        physics_sha256=sha256_file(args.physics),elapsed_seconds=time.monotonic()-started)
    for f in result.findings:
        names=set(f.region.part_names if f.region else [])
        if args.manifest.is_file():
            manifest=read_json(args.manifest)
            names.update(c for p in manifest.get('part_declarations',manifest.get('parts',[]))
                         if p['id'] in names for c in p.get('components',[]))
        f.source_candidates = source_candidates(args.source,Path(args.source_index) if args.source_index else None,names)
        f.evidence_refs.append(str(args.output/'report.json'))
    result.artifacts['report']=str(args.output/'report.json')
    write_json(args.output/'report.json',result.model_dump())
    write_json(args.output/'result.json',result.model_dump())


def orientation_only(before, after):
    """AST comparison excluding only literal calls to the print-rotation API."""
    import ast
    def stripped(text):
        tree=ast.parse(text)
        class RemoveOrientation(ast.NodeTransformer):
            def visit_Expr(self,node):
                call=node.value
                if isinstance(call,ast.Call) and isinstance(call.func,ast.Attribute) and call.func.attr=='set_print_orientation':
                    if len(call.args)!=1 or not isinstance(call.args[0],ast.Constant) or not isinstance(call.args[0].value,str):
                        return node
                    if len(call.keywords)!=1 or call.keywords[0].arg!='rotation_deg': return node
                    try:
                        values=ast.literal_eval(call.keywords[0].value)
                        if len(values)!=3 or any(not isinstance(x,(int,float)) for x in values): return node
                    except (ValueError,TypeError): return node
                    return None
                return self.generic_visit(node)
        return ast.dump(RemoveOrientation().visit(tree),include_attributes=False)
    return before != after and stripped(before)==stripped(after)


def area_comparison(old, new):
    a,b=old.get('metrics',{}),new.get('metrics',{})
    keys=('total_overhang_mm2','area_uncertainty_mm2','measurement_config_sha256')
    if old.get('status')!='PASS' or new.get('status')!='PASS' or any(a.get(k) is None or b.get(k) is None for k in keys):
        return {'conclusion':'NOT_EVALUATED'}
    if a['measurement_config_sha256'] != b['measurement_config_sha256']:
        return {'conclusion':'NOT_COMPARABLE'}
    delta=b['total_overhang_mm2']-a['total_overhang_mm2']
    bound=a['area_uncertainty_mm2']+b['area_uncertainty_mm2']
    return {'conclusion':'IMPROVED' if delta < -bound else 'WORSE' if delta > bound else 'UNCHANGED',
            'delta_mm2':delta,'uncertainty_mm2':bound}
