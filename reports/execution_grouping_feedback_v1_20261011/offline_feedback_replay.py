import json,shutil
from pathlib import Path
from adsl.agents.agent_feedback import payload_for_agent
from adsl.agents.fixed_assembly import _assembly_context
base=Path('/jiigan-hp/lms/aDSL/experiment')
results=[]
def size(x):return len(json.dumps(x,ensure_ascii=False,separators=(',',':'),allow_nan=False).encode())
def subset(container):
 names=('evaluation_feedback','evaluation_failure_ids','typed_findings','checker_evidence','checker_summary',
 'preview_info','evidence_files','render_issue','execution_error','error','duplicate_source','error_evidence_ref',
 'execution_error_evidence_ref','render_issue_evidence_ref','repair_history','attempt_history',
 'previous_image_decisions','previous_code_decisions','code_critic_corrections','previous_attempts')
 if not isinstance(container,dict):return {}
 out={k:container[k] for k in names if k in container}
 for k in ('feedback','assembly_context','pending_reviews','engineering_feedback'):
  if k in container:out[k]=subset(container[k])
 return out
for name,run,case in [('penguin','benchmark_twenty_more_stepcode_sol56_20261009T134924Z','Toys4K_penguin_001'),('sheep','benchmark_five_new_toys_stepcode_sol56_20261009T120053Z','Toys4K_sheep_038')]:
 original=base/run/'jobs'/case/'ours/generation/native'
 book=json.loads((original/'assembly_versions.json').read_text());v=book['versions'][book['working']]
 workspace=Path('/tmp/adsl_feedback_bound_replay_20261011')/name;workspace.mkdir(parents=True,exist_ok=True)
 source=workspace/Path(v['source']).relative_to(original);source.parent.mkdir(parents=True,exist_ok=True);source.write_bytes(Path(v['source']).read_bytes())
 def remap(x):
  if isinstance(x,dict):return {k:remap(v) for k,v in x.items()}
  if isinstance(x,list):return [remap(v) for v in x]
  if isinstance(x,str) and x.startswith(str(original)+'/'):
   old=Path(x)
   if old.is_file() and old.suffix in {'.json','.py'}:
    new=workspace/old.relative_to(original);new.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(old,new)
   return str(workspace)+x[len(str(original)):]
  return x
 feedback=remap(book['feedback']);context=_assembly_context(source,remap(v['reviews']['geometry']),
     version_role='repair_starting_version',report_ref=remap(v['reviews'].get('geometry_report_ref')))
 raw={'feedback':feedback,'assembly_context':context,'source_version':book['working']}
 for role in ('coder','engineering'):
  out=payload_for_agent(raw,workspace=workspace,source_path=source,source_version=book['working'],role=role,report_ref=feedback.get('geometry_report_ref'),evidence_files=feedback.get('evidence_files',()))
  assert out==payload_for_agent(out,workspace=workspace,source_path=source,source_version=book['working'],role=role,report_ref=feedback.get('geometry_report_ref'),evidence_files=feedback.get('evidence_files',()))
  (workspace/f'{role}_projected.json').write_text(json.dumps(out,ensure_ascii=False,indent=2))
  fields=subset(out)
  row=dict(case=case,role=role,raw_payload_bytes=size(raw),projected_payload_bytes=size(out),
      feedback_subset_bytes=size(fields),feedback_limit_bytes=32768,
      unique_failure_count=out['evaluation_feedback']['unique_failure_count'],idempotent=True,
      fields_bytes={k:size(val) for k,val in fields.items()})
  assert row['feedback_subset_bytes']<=32768
  results.append(row)
Path('/tmp/adsl_feedback_bound_replay_20261011/summary.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
