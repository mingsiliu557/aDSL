"""Planner/Coder shape demonstration, ending at CPU GLB rendering (no critics/checkers).

Run this same script with each checkout's adsl namespace on PYTHONPATH. Never
resume or overwrite a run directory: each case/arm receives one initial sample.
"""
from __future__ import annotations

import argparse
import ast
import asyncio
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import time

from adsl.agents.models import ObjectPlan
from adsl.agents.prompts import object_prompt
from adsl.agents.tools import AgentToolContext, WRITE_TOOLS, PATCH_TOOLS
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.runner import AgentRuntime
from adsl.agents.utils.execution import (execute_asset_source, AssetExecutionError,
    AssetInfrastructureError, _stop_process_group)

RENDER = dict(width=512, height=512, samples=32, threads=4, view_layout='review_eight',
              views=8, material='neutral', background='gray', engine='CYCLES')


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=str)+'\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def render_glb(glb, output, timeout):
    output.mkdir()
    command=[sys.executable,'-m','adsl.tools.render','--glb-path',str(glb),
        '--output-dir',str(output),'--width','512','--height','512','--render-samples','32',
        '--render-threads','4','--background','gray','--material-mode','neutral',
        '--view-layout','review_eight','--num-camera-per-layer','8']
    env=dict(os.environ,ADSL_RENDER_ENGINE='CYCLES',CUDA_VISIBLE_DEVICES='',
        HIP_VISIBLE_DEVICES='',ROCR_VISIBLE_DEVICES='',LIBGL_ALWAYS_SOFTWARE='1')
    env.pop('ADSL_GPU_RENDER_QUEUE',None)
    write_json(output/'command.json',command)
    with (output/'stdout.log').open('w') as stdout, (output/'stderr.log').open('w') as stderr:
        process=subprocess.Popen(command,stdout=stdout,stderr=stderr,env=env,start_new_session=True)
        try:
            code=process.wait(timeout=timeout)
        except BaseException:
            _stop_process_group(process)
            raise
    images=sorted(output.glob('*.png'))
    if code or len(images)!=8:
        raise RuntimeError(f'Render exit={code}, image_count={len(images)}; see {output}')
    return images


async def run_demo(case, output, model_profile, *, arm, timeout=300.0, session_root=None):
    output=Path(output).resolve()
    output.mkdir(parents=True,exist_ok=False)
    source=output/'source.py';source.touch()
    # Snapshot the exact conditioning images once; all roles use the same bytes.
    references=[]
    for i,value in enumerate(case.get('reference_images',())):
        original=Path(value).expanduser().resolve()
        destination=output/'reference'/f'{i:02d}_{original.name}'
        destination.parent.mkdir(exist_ok=True)
        shutil.copyfile(original,destination)
        references.append(dict(path=str(destination),original_path=str(original),sha256=sha(destination)))
    image_paths=tuple(Path(r['path']) for r in references)
    case={**case,'reference_images':[str(p) for p in image_paths]}

    import adsl.core, adsl.agents.models
    core_path=Path(adsl.core.__file__).resolve()
    checkout=core_path.parents[2]
    commit=subprocess.check_output(['git','-C',str(checkout),'rev-parse','HEAD'],text=True).strip()
    result=dict(case_id=case['id'],arm=arm,status='execution_failed',started_at=datetime.now(timezone.utc).isoformat(),
        commit=commit,core_path=str(core_path),agents_path=str(Path(adsl.agents.models.__file__).resolve()),
        runner_sha256=sha(Path(__file__)),input_sha256=hashlib.sha256(case['requirement'].encode()).hexdigest(),
        reference_images=references,
        input_bundle_sha256=hashlib.sha256(json.dumps(dict(requirement=case['requirement'],
            images=[r['sha256'] for r in references]),sort_keys=True).encode()).hexdigest(),
        render_config=RENDER,execution_timeout_seconds=timeout,render_timeout_seconds=timeout,
        initial_generations=0,execution_patches=0,execution_attempts=[],render_seconds=None,
        critics=[],checkers=[],export_urdf=False,fixed_assembly=None)
    write_json(output/'input.json',case)
    runtime=AgentRuntime(model_profile=model_profile,workspace=output,task_id=f'{arm}-{case["id"]}',
                         session_database_root=session_root)
    runtime.write_runtime_config(workflow='geometry_expression',request=case,
        execution=dict(timeout=timeout,render=RENDER,export_urdf=False,fixed_assembly=None,checkers=[]),
        context_policy={'planner':'fresh','coder:initial':'fresh','coder:execution_patch':'fresh'},arm=arm,commit=commit)

    async def call(role, payload, *, tools=(), output_type=None, context=None):
        instructions=object_prompt('planner' if role=='planner' else 'coder',articulation=False,fixed_assembly=False)
        agent=runtime.agent(name=f'geometry-{role}',instructions=instructions,tools=tools,output_type=output_type)
        folder=output/role;folder.mkdir()
        (folder/'system_prompt.md').write_text(instructions)
        write_json(folder/'input.json',payload)
        write_json(folder/'tools.json',[dict(name=t.name,description=t.description,parameters=t.params_json_schema) for t in tools])
        start=time.monotonic()
        try:
            response=await runtime.run(agent=agent,input=payload,role=role,stage=role,context=context,max_turns=16)
            write_json(folder/'messages.json',response.to_input_list())
            write_json(folder/'output.json',response.final_output.model_dump() if hasattr(response.final_output,'model_dump') else response.final_output)
            return response.final_output
        finally:
            write_json(folder/'timing.json',dict(seconds=time.monotonic()-start))
            if context is not None:write_json(folder/'tool_events.json',[asdict(e) for e in context.events])

    try:
        plan=await call('planner',user_input(case['requirement'],image_paths),output_type=ObjectPlan)
        if not isinstance(plan,ObjectPlan):plan=ObjectPlan.model_validate(plan)
        write_json(output/'plan.json',plan.model_dump())
        context=AgentToolContext(workspace=output,source_path=source)
        result['initial_generations']=1
        await call('coder_initial',user_input(json.dumps(dict(requirement=case['requirement'],
            articulation_required=False,plan=plan.model_dump(),assignment='Write source.py with the complete initial implementation.'),ensure_ascii=False),image_paths),
            tools=WRITE_TOOLS,context=context)
        if not any(e.tool=='write_file' and e.success for e in context.events):
            raise RuntimeError('Initial Coder did not write the assigned source.py')
        for attempt in range(2):
            snapshot=output/f'source_attempt_{attempt}.py';shutil.copyfile(source,snapshot)
            row=dict(attempt=attempt,source_sha256=sha(source));result['execution_attempts'].append(row)
            start=time.monotonic()
            try:
                execution=execute_asset_source(source,output/f'exec_{attempt}',render=False,export_urdf=False,
                    fixed_assembly=None,timeout=timeout)
            except Exception as error:
                row.update(seconds=time.monotonic()-start,error=str(error),exception=type(error).__name__)
                (output/f'execution_error_{attempt}.txt').write_text(str(error))
                # Only actual source/evaluation errors can consume the one patch.
                if attempt or not isinstance(error,AssetExecutionError) or isinstance(error,AssetInfrastructureError):
                    raise
                result['execution_patches']=1
                context=AgentToolContext(workspace=output,source_path=source)
                await call('coder_execution_patch',user_input(json.dumps(dict(requirement=case['requirement'],
                    plan=plan.model_dump(),execution_error=str(error),source_path=str(source),
                    assignment='Read source.py and apply the smallest correction for this execution error. Preserve the requested object.'),ensure_ascii=False),image_paths),
                    tools=PATCH_TOOLS,context=context)
                if not any(e.tool=='read_file' and e.success for e in context.events) or not any(e.tool=='apply_patch' and e.success for e in context.events):
                    raise RuntimeError('Execution correction did not read and patch source.py')
            else:
                row.update(seconds=time.monotonic()-start,status='exported')
                (output/f'exec_{attempt}'/'stdout.log').write_text(execution.stdout)
                (output/f'exec_{attempt}'/'stderr.log').write_text(execution.stderr)
                break
        result.update(glb=str(execution.glb_path),source_index=str(execution.source_index_path),
                      analysis_geometry=str(execution.analysis_geometry_path))
        import trimesh
        scene=trimesh.load(execution.glb_path,force='scene')
        result.update(vertices=sum(len(m.vertices) for m in scene.geometry.values()),
            triangles=sum(len(m.faces) for m in scene.geometry.values()),glb_bounds=scene.bounds.tolist(),
            glb_extents=scene.extents.tolist(),glb_coordinate_frame='glTF Y-up')
        result['status']='render_failed'
        start=time.monotonic()
        try:
            images=render_glb(execution.glb_path,output/'views',timeout)
        finally:
            result['render_seconds']=time.monotonic()-start
        result.update(status='rendered',images=[str(p) for p in images])
    except Exception as error:
        result.update(error=str(error),exception=type(error).__name__)
    finally:
        if source.is_file():
            result.update(source_sha256=sha(source),source=str(source),source_lines=len(source.read_text().splitlines()))
            try:
                names=[n.func.id if isinstance(n.func,ast.Name) else n.func.attr if isinstance(n.func,ast.Attribute) else ''
                       for n in ast.walk(ast.parse(source.read_text())) if isinstance(n,ast.Call)]
                result['new_api_call_sites']={n:names.count(n) for n in ('Polygon','linear_extrude','rotate_extrude','hull')}
            except SyntaxError:pass
        result.update(finished_at=datetime.now(timezone.utc).isoformat(),usage=asdict(runtime.usage.totals()))
        write_json(output/'demo_result.json',result)
        db=runtime.sessions.database_path
        if db.is_file() and db.resolve()!=(output/'sessions.sqlite3').resolve():
            with sqlite3.connect(db) as src, sqlite3.connect(output/'sessions.sqlite3') as dst:src.backup(dst)
    return result


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cases',type=Path,default=Path(__file__).with_name('cases.json'))
    parser.add_argument('--case',required=True)
    parser.add_argument('--arm',choices=('A','B'),required=True)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--model-profile',required=True,type=Path)
    parser.add_argument('--session-root',type=Path)
    parser.add_argument('--timeout',type=float,default=300)
    args=parser.parse_args()
    case=next(c for c in json.loads(args.cases.read_text())['cases'] if c['id']==args.case)
    result=asyncio.run(run_demo(case,args.output,args.model_profile,arm=args.arm,
        timeout=args.timeout,session_root=args.session_root))
    print(json.dumps(result,ensure_ascii=False,indent=2))
    return 0 if result['status']=='rendered' else 1


if __name__=='__main__':raise SystemExit(main())
