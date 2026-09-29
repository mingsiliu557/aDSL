"""One enhanced SF06 generation through the existing FixedAssembly workflow without FEA."""
import asyncio,json,subprocess,sys,time,traceback
from pathlib import Path
from adsl.agents.models import ObjectRequest,RepairPolicy,CheckerSpec
from adsl.agents.assembly_topology import checker_spec as topology_spec
from adsl.agents.assembly_physics import checker_spec
from adsl.agents.partition_score import OBJECTIVE
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.utils.io import read_json,write_json
from experiments.fixed_assembly_prompt import run as adapter

ROOT=Path(__file__).resolve().parent
REPO=Path('/tmp/adsl_geometry_20260929')
PROFILE=REPO/'adsl-agents/configs/llm/cliproxy-gpt-5.6-sol.yaml'
adapter.SESSION_ROOT=Path('/tmp/adsl_sf06_full_sessions_20260929')

def commit():return subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip()
def utc():return time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime())

def prepare():
    if (ROOT/'job.json').exists():raise RuntimeError('Already prepared; preserve the single initial generation and budget')
    catalog=REPO/'experiments/standing_fea_30/case_manifest.json'
    case=next(c for c in read_json(catalog)['cases'] if c['case_id']=='SF06')
    physics=dict(scope='SF06 armchair, gravity-only screening; FEA disabled. Uniform density is an experimental assumption.',
        material=dict(density_kg_m3=1240.),
        standing=dict(duration_seconds=5.,timestep_seconds=.002,friction=[.5,.005,.0001],tilt_threshold_deg=25.,
            settle_window_seconds=.5,settle_linear_m_s=.001,settle_angular_rad_s=.01,collision_backend='rigid_flex'),
        overhang=dict(overhang_threshold_from_horizontal_deg=45.,layer_height_mm=.2,orientation_editable=True,partition_objective=OBJECTIVE))
    manufacturing=adapter.MANUFACTURING.replace('No physical checkers, snap_floaters, or extra interface types.',
        'Enable Image/Code review and only these assembly checkers: assembly_topology, assembly_overhang, assembly_standing. FEA is disabled. No snap_floaters or extra interface types.')
    manufacturing+=''' Use a fresh Planner plan and initial source; no previous source or renders are supplied. At most 5 evaluation rounds including the initial candidate, at most 4 shared source modifications, and the existing 7200-second repair budget. Engineering/Coder may correct measured visual, geometry and physical failures. After necessary checks pass, allow one evidence-supported local print grouping proposal per feasible baseline using the Dapper alpha=.3/Rvox=.1 objective and 24 print rotations; NO_PROPOSAL is valid. Do not change the frozen density, gravity settings, thresholds, dimensions or budget. Use actual millimetre dimensions. Standing measures gravity contact using the existing rigid_flex backend. Keep feet at Z=0. No external load analysis or FEA will run. Missing or invalid measurements remain unverified. The original task remains authoritative; optional inferred texture is not a mandatory defect. The plain black background describes presentation: build only the chair, without a physical floor or backdrop that would become a print part.'''
    fixed=dict(validation_mode='visual_only',mm_per_unit=1.,final_size_mm=[900.,800.,900.],fit_offset_mm=.2,require_multiple_parts=False,physics=physics)
    requirement='[ORIGINAL TASK]\n'+case['prompt']+'\n\n[UNIFORM MANUFACTURING REQUIREMENTS]\n'+manufacturing
    requirement+='\nFrozen dimensions: 900 x 800 x 900 mm; mm_per_unit=1; fit_offset_mm=0.2.'
    specs=[topology_spec(),checker_spec('assembly_overhang'),checker_spec('assembly_standing')]
    policy=RepairPolicy(print_partition_editable=True,print_orientation_editable=True,max_total_candidates=4,time_budget_seconds=7200)
    data=dict(case_id='SF06',original_prompt=case['prompt'],requirement=requirement,image_paths=[],fixed_assembly=fixed,
        max_rounds=5,repair_policy=policy.model_dump(),checker_specs=[s.model_dump() for s in specs],
        provenance=dict(catalog=str(catalog),catalog_sha256=sha256_file(catalog),dataset=case['dataset'],object_id=case['object_id'],
            caption_source=case['caption_source'],dimension_basis='900 mm historical target height; new frozen 900x800 mm armchair footprint. No FEA/load/support specification.',
            prior_geometry_used=False,baseline_arm=False))
    write_json(ROOT/'input.json',data);write_json(ROOT/'physics.json',physics)
    write_json(ROOT/'job.json',dict(status='PREPARED',created_utc=utc(),commit=commit(),model_profile=str(PROFILE),
        profile_sha256=sha256_file(PROFILE),input_sha256=sha256_file(ROOT/'input.json'),case_order=['SF06'],cpu_only=True,fea_enabled=False,supersedes='geometry_sf06_full_20260929 (stopped by user before source returned)',
        checkers=[s.name for s in specs],reviews=['image','code'],max_rounds=5,max_source_repairs=4,
        repair_time_budget_seconds=7200,one_initial_generation=True,render=dict(engine='CYCLES',width=512,height=512,samples=32,views=8),
        geometry_timeout_seconds=120,render_timeout_seconds=300))

async def main():
    job=read_json(ROOT/'job.json');data=read_json(ROOT/'input.json')
    assert commit()==job['commit'] and sha256_file(PROFILE)==job['profile_sha256']
    assert sha256_file(ROOT/'input.json')==job['input_sha256']
    work=ROOT/'generate'
    if work.exists() or (ROOT/'started.json').exists():raise RuntimeError('No automatic replay/resume or budget reset')
    request=ObjectRequest(requirement=data['requirement'],workspace=work,task_id='SF06_constructive_no_fea_20260929',
        image_paths=(),articulation=False,max_rounds=5,fixed_assembly=data['fixed_assembly'],
        repair_policy=RepairPolicy.model_validate(data['repair_policy']),
        checker_specs=tuple(CheckerSpec.model_validate(s) for s in data['checker_specs']))
    adapter.PromptWorkflow._validate_request(request)
    workflow=adapter.PromptWorkflow(PROFILE);started=time.monotonic();error=None;result=None
    write_json(ROOT/'started.json',dict(started_utc=utc(),pid=__import__('os').getpid(),commit=commit()))
    write_json(ROOT/'status.json',dict(status='RUNNING',started_utc=utc(),workspace=str(work)))
    try:result=await workflow.generate(request)
    except Exception as exc:
        error=dict(type=type(exc).__name__,message=str(exc));(ROOT/'runner_error.log').write_text(traceback.format_exc())
    snapshot=adapter.snapshot_sessions(getattr(workflow,'runtime',None),work)
    final=read_json(work/'assembly_result.json') if (work/'assembly_result.json').exists() else {}
    book=read_json(work/'assembly_versions.json') if (work/'assembly_versions.json').exists() else {}
    completion=dict(status='ERROR' if error else 'COMPLETED',completed_utc=utc(),elapsed_seconds=time.monotonic()-started,
        approved=bool(result and result.approved),error=error,selected=book.get('retained'),qualified=book.get('qualified'),
        stop_reason=book.get('stop_reason'),checker_statuses=final.get('checker_statuses'),source=final.get('source'),
        source_sha256=final.get('source_sha256'),snapshot=snapshot,assembly_result=str(work/'assembly_result.json'))
    write_json(ROOT/'completion.json',completion);write_json(ROOT/'status.json',completion)
    print(json.dumps(completion,indent=2),flush=True)

if __name__=='__main__':
    if sys.argv[1:]==['--prepare']:prepare()
    elif not sys.argv[1:]:asyncio.run(main())
    else:raise SystemExit('Use --prepare or no arguments')
