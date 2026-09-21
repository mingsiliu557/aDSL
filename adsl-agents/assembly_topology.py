"""Thin fixed-assembly checker/Engineer adapter; no independent repair loop."""
from __future__ import annotations

import argparse
import ast
from copy import deepcopy
import json
import os
from pathlib import Path
import signal
import sys
import time

from .checkers import run_checker, CheckerRun
from .feedback_schema import sha256_file
from .models import (CheckerSpec, CheckerResult, CheckerFinding, RegionEvidence,
                     SourceCandidate, EngineeringCriticDecision, RelationEvidence, RelationEndpoint, MetricEvidence)
from .utils.execution import ExecutionResult
from .utils.io import read_json, write_json

NAME='assembly_topology'


def install_worker_cleanup():
    """Outer deadline must also kill nested run_checker's separate groups.

    This deployment is Linux. Direct children are the existing per-item checker
    leaders; kill their groups immediately, then let run_checker reap on unwind.
    """
    def terminate(signum,frame):
        children=Path(f'/proc/{os.getpid()}/task/{os.getpid()}/children')
        for pid in children.read_text().split():
            try: os.killpg(int(pid),signal.SIGKILL)
            except ProcessLookupError: pass
        raise SystemExit(128+signum)
    signal.signal(signal.SIGTERM,terminate)
    signal.signal(signal.SIGINT,terminate)


def checker_spec(timeout_seconds=900):
    return CheckerSpec(name=NAME, timeout_seconds=timeout_seconds, command=[
        '{python}', '-m', 'adsl.agents.assembly_topology', '--manifest',
        '{asset_dir}/assembly_manifest.json', '--source', '{source}',
        '--output', '{output_dir}', '--source-index', '{source_index}',
        '--budget-seconds', str(timeout_seconds)])


def _input(manifest_path, source):
    report=read_json(manifest_path)
    if report.get('source_sha256')!=sha256_file(source):
        raise ValueError('manifest/source hash mismatch')
    return report


def _part_file(manifest_path, report, part):
    path=(manifest_path.parent/part['stl']).resolve()
    if path.parent != manifest_path.parent.resolve():
        raise ValueError('print mesh path outside assembly export')
    expected=report.get('files_sha256',{}).get(path.name)
    if not expected or sha256_file(path)!=expected:
        raise ValueError('print mesh hash missing/mismatched')
    return path


def source_candidates(source, source_index, names):
    """Exact semantic candidates only; absence never prohibits source reading."""
    if not source_index or not source_index.is_file(): return []
    try:
        index=read_json(source_index)
        if index.get('source_sha256')!=sha256_file(source): return []
        features=[f for f in index.get('features',[]) if f.get('name') in names]
        return [SourceCandidate(feature_id=f['feature_id'],source_ids=f.get('source_ids',[]),
            source_locations=f.get('source_locations',[]),method='direct',
            ambiguous=len(features)>1 or f.get('resolution')!='complete' or not f.get('source_ids'),
            evidence=['Exact runtime feature-name match; not unique triangle ownership']) for f in features[:6]]
    except (OSError,ValueError,KeyError): return []


def make_result(report, rows, source, output, source_index=None):
    status='FAIL' if any(r['status']=='FAIL' for r in rows) else (
        'INDETERMINATE' if not rows or any(r['status']!='PASS' for r in rows) else 'PASS')
    findings=[]
    parts={p['id']:p for p in report.get('part_declarations',report.get('parts',[]))}
    for row in rows:
        if row['status']=='PASS': continue
        names=[row['part_id']] if row['kind']=='part' else [row.get('tab_part'),row.get('slot_part')]
        names=[n for n in names if n]
        candidates=source_candidates(source,source_index,set(names)|{
            c for n in names for c in parts.get(n,{}).get('components',[])})
        ident=row.get('part_id') or row.get('connection_id','input')
        localized_open = (row['code']=='OPEN_PRINT_MESH' and row.get('bounds_mm')
                          and row.get('boundary_edge_count',0)>0)
        relations=[]
        nearest=row.get('nearest_components')
        if nearest:
            relations=[RelationEvidence(kind='disconnected',frame=f'part_local:{ident}',unit='mm',
                endpoints=[RelationEndpoint(role=f'component_{idx}',part_names=names,point=point)
                    for idx,point in zip(nearest['component_indices'],nearest['witness_points_mm'])],
                distance=MetricEvidence(name='sampled_separation_upper_bound',
                    value=nearest['sampled_distance_upper_bound_mm'],unit='mm'),details={
                    'method':nearest['method'],'aabb_distance_lower_bound_mm':nearest['aabb_distance_lower_bound_mm']})]
        findings.append(CheckerFinding(finding_id=f'{NAME}:{ident}:{row["code"]}',rule_id=row['code'],
            category='geometry_failure' if row['status']=='FAIL' else 'evidence_insufficient',
            repairability='geometry' if row['status']=='FAIL' or localized_open else 'analysis',
            message=(f'{ident}: open boundary located; connectivity and dependent interfaces unverified; '
                     'source-level geometric cause not established.' if localized_open else
                     f'{ident}: {row["code"]}; geometry evidence only, manufacture unverified.'),
            region=RegionEvidence(kind='aabb' if row.get('bounds_mm') else 'parts',
                frame=f'part_local:{ident}' if row['kind']=='part' else f'slot_interface:{ident}',
                unit='mm',bounds=row.get('bounds_mm'),part_names=names,
                details={'component_bounds_preview_mm':row.get('component_bounds_mm',[])[:3],
                    'boundary_regions_preview':row.get('boundary_regions',[])[:6],
                    'preview_only':row.get('component_count',0)>3}), relations=relations,
            source_candidates=candidates,evidence_refs=[str(output/'report.json')],domain=row))
    counts={s:sum(r['status']==s for r in rows) for s in ('PASS','FAIL','INDETERMINATE')}
    return CheckerResult(checker=NAME,status=status,summary=f'Assembly topology {status}; item counts {counts}',
        metrics={'items':rows}, findings=findings,
        assumptions={'source_sha256':sha256_file(source),'manifest_sha256':report.get('_manifest_sha256'),
            'scope':'within-part solid connectivity and local TabSlot pairing; not retention, insertion path, strength or manufacturing',
            'localization':'index-assisted if current candidates exist; otherwise model source inference'},
        artifacts={'report':str(output/'report.json')})


def worker(args):
    from adsl.core.assembly_topology import (part_measurement,read_print_mesh,
        interface_measurement,mesh_solid,solid_mesh,OpenPrintMeshError)
    import trimesh
    report=_input(args.manifest,args.source)
    parts={p['id']:p for p in report['parts']}
    if args.part:
        part=parts[args.part]
        mesh=read_print_mesh(_part_file(args.manifest,report,part),part['print_transform_mm'])
        try:
            row,solid=part_measurement(mesh,args.part,part.get('mesh_face_groups'))
            # PLY preserves indexed triangles and millimetres, no STL re-welding.
            solid_mesh(solid).export(args.output/'solid.ply',encoding='binary_little_endian')
        except OpenPrintMeshError as error:
            write_json(args.output/'boundary_edges.json',error.evidence)
            row=dict(kind='part',part_id=args.part,status='INDETERMINATE',code='OPEN_PRINT_MESH',
                stage='final_print_mesh_validation',reason=str(error),
                **{k:v for k,v in error.evidence.items() if k!='boundary_edges_mm'},
                boundary_report=str(args.output/'boundary_edges.json'))
    else:
        connection=next(c for c in report['connections'] if c['id']==args.connection)
        paths=read_json(args.solids)
        solids={name:mesh_solid(trimesh.load_mesh(paths[name],process=False))
                for name in (connection['tab_part'],connection['slot_part'])}
        row=interface_measurement(connection,parts,solids,report['mm_per_unit'])
    result=CheckerResult(checker=NAME,status=row['status'],summary=row['code'],metrics={'item':row})
    write_json(args.output/'result.json',result.model_dump())


def measure(args):
    install_worker_cleanup()
    start=time.monotonic()
    report=_input(args.manifest,args.source)
    report['_manifest_sha256']=sha256_file(args.manifest)
    parts={p['id']:p for p in report['parts']}
    declared=report.get('part_declarations',report['parts'])
    rows=[]; paths={}
    def save():
        write_json(args.output/'report.json',{'source_sha256':sha256_file(args.source),
            'manifest_sha256':report['_manifest_sha256'],'items':rows,'elapsed_seconds':time.monotonic()-start,
            'single_operation_timeout_seconds':120,'checker_budget_seconds':args.budget_seconds})
        write_json(args.output/'result.json',make_result(report,rows,args.source,args.output,args.source_index).model_dump())
    execution=ExecutionResult(args.manifest.parent,args.manifest.parent/'scene.glb',None,(),'','')
    jobs=[('part',p['id'],p) for p in declared]+[('interface',c['id'],c) for c in report.get('connections',[])]
    save()
    for kind,ident,data in jobs:
        row=dict(kind=kind,status='INDETERMINATE',code='MEASUREMENT_UNAVAILABLE')
        row.update({'part_id':ident} if kind=='part' else
                   {k:data[k] for k in ('tab_part','slot_part','parameter_name','tab_port','slot_port')})
        if kind=='interface': row['connection_id']=ident
        remaining=args.budget_seconds-(time.monotonic()-start)-3
        if kind=='part' and ident not in parts:
            row['code']='PRINT_MESH_UNAVAILABLE'
        elif kind=='interface' and any(data[k] not in paths for k in ('tab_part','slot_part')):
            row['code']='DEPENDENCY_MESH_UNAVAILABLE'
        elif remaining<=0:
            row['code']='CHECKER_BUDGET_NOT_EXECUTED'
        else:
            write_json(args.output/'solids.json',paths)
            command=['{python}','-m','adsl.agents.assembly_topology','--manifest',str(args.manifest),
                '--source','{source}','--output','{output_dir}',
                '--part' if kind=='part' else '--connection',ident,'--solids',str(args.output/'solids.json')]
            job_start=time.monotonic()
            run=run_checker(CheckerSpec(name=NAME,command=command,timeout_seconds=min(120,remaining)),
                execution=execution,source_path=args.source,round_root=args.output/'items'/f'{kind}_{ident}')
            if run.result.metrics.get('item'):
                row.update(run.result.metrics['item'])
                if kind=='part' and (run.output_dir/'solid.ply').is_file():
                    paths[ident]=str(run.output_dir/'solid.ply')
            else:
                violation=next(iter(run.result.violations),{})
                row.update(code=violation.get('code','MEASUREMENT_UNAVAILABLE'),stage=violation.get('stage'),
                    reason=run.result.summary[:240],raw_status=run.result.status)
            row.update(elapsed_seconds=time.monotonic()-job_start,report_path=str(run.output_dir/'result.json'))
        rows.append(row); save()


def run_assembly_topology(spec, *, execution, source, root):
    """Raw infrastructure errors stay on disk; the geometry remains unverified."""
    run=run_checker(spec,execution=execution,source_path=source,round_root=root)
    if run.result.status!='ERROR': return run
    write_json(run.output_dir/'execution_error.json',run.result.model_dump())
    partial=run.output_dir/'report.json'
    rows=[]
    if partial.exists():
        try:
            saved=read_json(partial)
            if saved.get('source_sha256')==sha256_file(source): rows=saved.get('items',[])
        except (ValueError,OSError,AttributeError):
            pass  # A corrupt partial report must not mask the original failure.
    rows.append(dict(kind='part',part_id='uncompleted_measurement',status='INDETERMINATE',
        code='CHECKER_UNAVAILABLE',reason=run.result.summary[:240],stage='execution'))
    result=make_result({},rows,source,run.output_dir)
    write_json(run.output_dir/'result.json',result.model_dump())
    return CheckerRun(spec,result,run.output_dir,run.command)


EVIDENCE_PATH_INSTRUCTION = (
    'Read the given evidence_files/result_ref paths directly; do not add a candidate directory prefix. '
    'Relative tool paths are workspace-relative. Only assigned_source may be modified. '
    'Initial-source-only evidence is a historical clue, not a measurement of modified geometry. '
    'Use inline facts first; reading every detailed report is not required.')


def prepare_evidence(feedback, *, workspace, source_sha256, evidence_files=()):
    """Normalize only this adapter's explicit file fields, not arbitrary text."""
    workspace = workspace.resolve()
    files = []
    # Reviews/manifests have already been version-hashed. Normalize the payload,
    # never mutate those saved records through shared dictionaries.
    for key in ('failures','render_issue','checker_summary','typed_findings'):
        if key in feedback: feedback[key] = deepcopy(feedback[key])

    def reference(path, purpose, metadata=None):
        row = dict(metadata or {'source_sha256':source_sha256, 'version_role':'current_source'})
        row.update(path=None, purpose=purpose, availability='UNAVAILABLE')
        try:
            target = Path(path).expanduser()
            if not target.is_absolute(): target = workspace/target
            target = target.resolve()
            if not target.is_relative_to(workspace):
                row['reason'] = 'outside_workspace'
            else:
                row['path'] = str(target)
                row['availability'] = 'AVAILABLE' if target.is_file() else 'UNAVAILABLE'
                if row['availability'] == 'UNAVAILABLE': row['reason'] = 'file_not_found'
        except (OSError, ValueError, TypeError) as error:
            row['reason'] = f'path_unavailable:{type(error).__name__}'
        row['matches_current_source'] = row.get('source_sha256') == source_sha256
        if row not in files: files.append(row)
        return row['path']

    for row in evidence_files:
        reference(row.get('path'), row.get('purpose', 'optional evidence'), row)
    rows = [feedback, *(feedback.get('failures') or []),
            *(feedback.get('checker_summary') or []), *(feedback.get('typed_findings') or [])]
    if feedback.get('render_issue'): rows.append(feedback['render_issue'])
    for row in rows:
        for field in ('result_ref', 'report_path', 'boundary_report'):
            if row.get(field): row[field] = reference(row[field], field)
        if row.get('evidence_refs'):
            row['evidence_refs'] = [reference(p, 'checker detail') for p in row['evidence_refs']]
        for field in ('report_path', 'boundary_report'):
            values = row.get('key_values', {})
            if values.get(field): values[field] = reference(values[field], field)
    feedback['evidence_files'] = files
    feedback['evidence_path_instruction'] = EVIDENCE_PATH_INSTRUCTION


ENGINEERING_INSTRUCTION='''Fixed assembly specialization: the old whole-object topology
bridge_parent/scope-only rules do NOT apply. Index candidates are hints, not edit
permissions. Read the assigned current source; infer relevant body classes,
helpers and add_part/connect/frame calls when index candidates are absent or
ambiguous. Explain location, evidence and uncertainty; never invent index IDs.
Use exact current source symbols in allowed_scopes; top-level assembly statements
can use <module>. These are optional location hints, not edit permissions or
invented index IDs. Unresolved attribute hints must be verified by reading source.
You may minimally correct print grouping and connections, without a new Planner.
Keep frozen units, fit allowance, dimensions and task requirements. No checker,
configuration or mesh repair edits. Propose at most ONE coordinated source patch,
including compatible pending Image/Code issues. Stop with no proposals if no
reasonable edit follows from evidence. INDETERMINATE alone is not a shape defect.
OPEN_PRINT_MESH with measured boundary locations permits a bounded local SOURCE
repair attempt, but is NOT confirmed disconnection. Read the current source and
boundary evidence; local body/decoration simplification may be proposed without
assuming the connector is at fault. Preserve required visible features. The exact
cause remains uncertain until re-export and recheck. Never fill holes in exported
meshes, change tolerances/checker settings, or treat unavailable interfaces as FAIL.
Engineering approval cannot override measured FAIL. Recheck assembly_topology.
'''


async def engineer(workflow,runtime,request,plan,source,execution,root,run,context,feedback,remaining):
    from .service import _actionable_findings
    from .prompts import object_prompt
    from .tools import READ_TOOLS, AgentToolContext
    from .utils.inputs import user_input
    agent=runtime.agent(name='object-engineering-critic',tools=READ_TOOLS,
        instructions=object_prompt('engineering_critic',articulation=False)+ '\n'+ENGINEERING_INSTRUCTION,
        output_type=EngineeringCriticDecision,strict_json_schema=False)
    payload={'requirement':request.requirement,'plan':plan.model_dump(),
        'assigned_source':str(source.relative_to(request.workspace)), 'assembly_context':context,
        **{k:feedback.get(k) for k in ('checker_summary','typed_findings','evidence_access',
                                     'evidence_files','evidence_path_instruction')},
        'source_sha256':sha256_file(source), 'source_version':feedback.get('source_version'),
        'assembly_item_statuses':[{k:r.get(k) for k in ('part_id','connection_id','status','code')}
            for r in run.result.metrics.get('items',[])],
        'pending_reviews':{k:feedback.get(k) for k in ('image_critic','code_critic','render_issue','repair_history')},
        'remaining_repairs':remaining,'maximum_repair_proposals':1,
        'assignment':ENGINEERING_INSTRUCTION + EVIDENCE_PATH_INSTRUCTION}
    write_json(root/'engineering_input.json',payload)
    tool_context=AgentToolContext(workspace=request.workspace,source_path=source)
    response=await runtime.run(agent=agent,input=user_input(json.dumps(payload),
        (*request.image_paths,*(execution.render_paths if execution else ()))),
        role=f'engineering-critic:assembly:{root.name}',stage=f'assembly_engineering:{root.name}',context=tool_context)
    workflow._require_tool_event(tool_context,'read_file','engineering critic')
    decision=workflow._typed_output(response.final_output,EngineeringCriticDecision)
    write_json(root/'engineering_critique.json',decision.model_dump())
    if len(decision.repair_proposals)>1: raise ValueError('expected at most one coordinated assembly proposal')
    proposal=next(iter(decision.repair_proposals),None)
    feedback['engineering'] = {'status':'PROPOSAL' if proposal else 'NO_PROPOSAL',
        'observations':[s[:300] for s in decision.observations[:3]],
        'unresolved_findings':decision.unresolved_findings[:6],
        'report_path':str((root/'engineering_critique.json').resolve())}
    if proposal:
        ids={f.finding_id for f in _actionable_findings(run)}
        if not set(proposal.finding_ids)<=ids or proposal.action in ('request_evidence','change_print_orientation'):
            feedback['engineering']={'status':'UNAVAILABLE','reason':'unknown/unrepairable finding or unsupported action'}
            write_json(root/'proposal_rejected.json',feedback['engineering'])
            return None
        known={sid for f in run.result.findings for c in f.source_candidates for sid in c.source_ids}
        features={c.feature_id for f in run.result.findings for c in f.source_candidates}
        if not set(proposal.target.source_ids)<=known or not set(proposal.target.feature_ids)<=features:
            feedback['engineering']={'status':'UNAVAILABLE','reason':'unverified source/index IDs'}
            write_json(root/'proposal_rejected.json',feedback['engineering'])
            return None
        tree=ast.parse(source.read_text())
        symbols={'<module>':tree}
        def visit(body,prefix=''):
            for node in body:
                if isinstance(node,(ast.ClassDef,ast.FunctionDef,ast.AsyncFunctionDef)):
                    symbols[prefix+node.name]=node
                    visit(node.body,prefix+node.name+'.')
        visit(tree.body)
        unresolved=[scope for scope in proposal.target.allowed_scopes if scope not in symbols]
        if unresolved:
            feedback['engineering']['unresolved_location_hints']=unresolved
            feedback['engineering']['location_note']='Unresolved hints, not tool-confirmed scopes; Coder must read source to verify.'
            proposal=proposal.model_copy(update={'target':proposal.target.model_copy(update={
                'allowed_scopes':[scope for scope in proposal.target.allowed_scopes if scope in symbols]})})
        write_json(root/'proposal_location.json',{'source_sha256':sha256_file(source),
            'method':'index_assisted' if proposal.target.source_ids else 'model_inferred',
            'tool_confirmed_geometry_ownership':False,
            'scopes':[{ 'symbol':s,'start_line':getattr(symbols[s],'lineno',1),
                'end_line':getattr(symbols[s],'end_lineno',len(source.read_text().splitlines()))}
                for s in proposal.target.allowed_scopes], 'unresolved_location_hints':unresolved,
                'basis':proposal.evidence})
    return proposal


def main():
    parser=argparse.ArgumentParser(__doc__)
    for name in ('manifest','source','output','solids'): parser.add_argument('--'+name,type=Path,required=name in ('manifest','source','output'))
    parser.add_argument('--source-index',default='')
    parser.add_argument('--part'); parser.add_argument('--connection')
    parser.add_argument('--budget-seconds',type=float,default=900)
    args=parser.parse_args(); args.output.mkdir(parents=True,exist_ok=True)
    args.source_index=Path(args.source_index) if args.source_index else None
    if args.part or args.connection:
        try:
            worker(args)
        except (ValueError,KeyError,OSError,RuntimeError) as error:
            row=dict(kind='part' if args.part else 'interface',status='INDETERMINATE',
                code='PRINT_MESH_UNMEASURABLE' if args.part else 'INTERFACE_QUERY_UNAVAILABLE',
                stage='final_mesh_load_or_union' if args.part else 'interface_query',
                reason=f'{type(error).__name__}: {str(error)[:240]}')
            row.update({'part_id':args.part} if args.part else {'connection_id':args.connection})
            write_json(args.output/'result.json',CheckerResult(checker=NAME,status='INDETERMINATE',
                summary=row['reason'],metrics={'item':row}).model_dump())
    else: measure(args)


if __name__=='__main__': main()
