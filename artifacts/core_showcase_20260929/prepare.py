from pathlib import Path
import json, shutil, hashlib
import numpy as np
import trimesh
R=Path('/vepfs_default/chanxueyan/lhp/lms/aDSL');O=R/'temp/core_showcase_20260929';(O/'resources').mkdir(exist_ok=True)
spec={
'SF13':('temp/sf13_microcrack_standing_20260928/asset/assembly/assembly_manifest.json','temp/sf13_microcrack_standing_20260928/topology/report.json'),
'SF10_before':('temp/partition_coordination_20260929/SF10_one_attempt/rounds/round_01/asset/assembly/assembly_manifest.json','temp/partition_coordination_20260929/SF10_one_attempt/rounds/round_01/checkers/assembly_topology/report.json'),
'SF10_after':('temp/partition_coordination_20260929/SF10_one_attempt/rounds/round_02/candidates/01_partition_merge_left_pedestal_lower_brace_v1/asset/assembly/assembly_manifest.json','temp/partition_coordination_20260929/SF10_one_attempt/rounds/round_02/candidates/01_partition_merge_left_pedestal_lower_brace_v1/checkers/assembly_topology/report.json'),
'SF16_before':('temp/assembly_interference_20260929/cases/SF16/original/asset/assembly/assembly_manifest.json','temp/assembly_interference_20260929/cases/SF16/original/verification/checkers/assembly_topology/report.json'),
'SF16_after':('temp/assembly_interference_20260929/cases/SF16/agent_repair/rounds/round_03/candidates/02_assembly_or_appearance/asset/assembly/assembly_manifest.json','temp/assembly_interference_20260929/cases/SF16/agent_repair/rounds/round_03/candidates/02_assembly_or_appearance/checkers/assembly_topology/report.json')}
provenance={};data={}
for key,(mp,rp) in spec.items():
 m=json.loads((R/mp).read_text());report=json.loads((R/rp).read_text());dest=O/'resources'/key;dest.mkdir(exist_ok=True);base=(R/mp).parent
 for p in base.iterdir():
  if p.is_file() and p.suffix in ['.stl','.glb','.json']:shutil.copy2(p,dest/p.name)
 shutil.copy2(R/rp,dest/'topology_report.json')
 meshes={}
 for part in m['parts']:
  mesh=trimesh.load(base/part['stl'],force='mesh',process=False)
  T=np.array(part['assembly_transform']);T[:3,3]*=m['mm_per_unit']
  mesh.apply_transform(T@np.linalg.inv(np.array(part['print_transform_mm'])))
  meshes[part['id']]=mesh
  np.savez_compressed(dest/(part['id']+'.world.npz'),vertices=mesh.vertices,faces=mesh.faces)
 bounds=np.array([np.vstack([x.vertices for x in meshes.values()]).min(0),np.vstack([x.vertices for x in meshes.values()]).max(0)])
 data[key]=dict(manifest=str(dest/'assembly_manifest.json'),bounds=bounds.tolist(),parts=list(meshes),mm_per_unit=m['mm_per_unit'])
 pairs=[i for i in report.get('items',[]) if i.get('kind')=='pair' and set(i.get('part_ids',[]))=={'slanted_back_panel','top_shelf'}]
 if pairs:data[key]['target_pair']=pairs[0]
 provenance[key]=dict(original_manifest=str(R/mp),original_report=str(R/rp),source_sha256=m['source_sha256'],manifest_sha256=hashlib.sha256((R/mp).read_bytes()).hexdigest(),parts=len(m['parts']),connections=len(m['connections']))
 print(key,'bounds',bounds.tolist(),'parts',len(m['parts']),'connections',len(m['connections']),'pair',[(p.get('status'),p.get('undeclared_interference_mm3')) for p in pairs])
 # Source is copied only when it matches the manifest hash.
 candidates=[]
 for parent in [base.parent,base.parent.parent,base.parent.parent.parent,R/'temp/sf13_microcrack_standing_20260928']:
  candidates += [parent/'source.py',parent/'original/source.py']
 for p in candidates:
  if p.exists() and hashlib.sha256(p.read_bytes()).hexdigest()==m['source_sha256']:
   shutil.copy2(p,dest/'source.py');provenance[key]['original_source']=str(p);break
 # Reports specific to the same candidate.
 checkers=(R/rp).parent.parent
 for name in ['assembly_overhang','assembly_standing']:
  p=checkers/name/'report.json'
  if p.exists():shutil.copy2(p,dest/(name+'_report.json'))
(O/'render_inputs.json').write_text(json.dumps(data,indent=2)+'\n');(O/'provenance.json').write_text(json.dumps(provenance,indent=2)+'\n')
