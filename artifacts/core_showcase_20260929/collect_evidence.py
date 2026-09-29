from pathlib import Path
import shutil,json,hashlib
R=Path('/vepfs_default/chanxueyan/lhp/lms/aDSL');O=R/'temp/core_showcase_20260929';E=O/'evidence';E.mkdir(exist_ok=True)
def cp(p,target):
 if not p.exists():return
 if p.is_dir():shutil.copytree(p,target,dirs_exist_ok=True,ignore=shutil.ignore_patterns('*.sqlite3','__pycache__'))
 else:target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,target)
roots={'SF10':R/'temp/partition_coordination_20260929/SF10_one_attempt','SF16':R/'temp/assembly_interference_20260929/cases/SF16/agent_repair','SF13':R/'temp/sf13_microcrack_standing_20260928'}
for key,base in roots.items():
 for name in ['plan.json','input.json','user_input.json','user_input.txt','runtime_config.json','physics.json','completion.json','extra_repair_completion.json','assembly_result.json','assembly_versions.json','actual_model_calls','README.md','standing_criterion_20260928/report.json','standing_criterion_20260928/result.json']:
  cp(base/name,E/key/name)
 for p in base.glob('attempt_*.diff'):cp(p,E/key/p.name)
 if key in ['SF10','SF16']:
  for p in (base/'rounds').rglob('*.json'):
   if p.name in ['engineering_input.json','engineering_critique.json','decision.json','image_critique.json','code_critique.json','result.json','report.json'] and '/items/' not in str(p):cp(p,E/key/p.relative_to(base))
for name in ['partition_coordination_20260929.md','assembly_interference_20260929.md','numeric_microcrack_welding_20260928.md','standing_observation_criterion_20260928.md','geometry_expression_20260929.md']:
 cp(R/'reports'/name,E/'reports'/name)
# Exact SF21/SF06 generation materials were already uploaded; copy the four bundles
# so this showcase folder is independently usable. Exclude session databases.
for case in ['SF21','SF06']:
 for arm in ['A','B']:
  cp(R/'temp/geometry_expression_20260929'/arm/case,O/'resources'/case/arm)
print('Evidence and original shape resources collected')
