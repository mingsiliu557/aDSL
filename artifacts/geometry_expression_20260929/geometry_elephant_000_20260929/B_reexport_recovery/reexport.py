import json, hashlib, time, sys
from pathlib import Path
sys.path.insert(0, '/tmp/adsl_geometry_20260929')
from adsl.agents.utils.execution import execute_asset_source
from experiments.geometry_expression.run_demo import render_glb, RENDER
import trimesh
root=Path('/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/geometry_elephant_000_20260929')
out=root/'B_reexport_recovery'; source=root/'B/source.py'
start=time.monotonic()
e=execute_asset_source(source,out/'exec_final',render=False,export_urdf=False,fixed_assembly=None,timeout=300)
export_seconds=time.monotonic()-start
(out/'exec_final/stdout.log').write_text(e.stdout);(out/'exec_final/stderr.log').write_text(e.stderr)
start=time.monotonic();images=render_glb(e.glb_path,out/'views_final',300);render_seconds=time.monotonic()-start
scene=trimesh.load(e.glb_path,force='scene',process=False)
import struct
raw=e.glb_path.read_bytes();doc=json.loads(raw[20:20+struct.unpack('<I',raw[12:16])[0]])
recoveries=[dict(node=n.get('name'),report=json.loads(n['extras']['adsl_boolean_recovery']),trigger=n['extras'].get('adsl_boolean_recovery_trigger')) for n in doc['nodes'] if n.get('extras',{}).get('adsl_boolean_recovery')]
trunk=[n for n in doc['nodes'] if 'trunk' in n.get('extras',{}).get('adsl_path','').lower()]
result=dict(status='rendered',kind='same_source_export_recovery_no_model_call',source=str(source),source_sha256=hashlib.sha256(source.read_bytes()).hexdigest(),glb=str(e.glb_path),images=[str(p) for p in images],render_config=RENDER,export_seconds=export_seconds,render_seconds=render_seconds,triangles=sum(len(m.faces) for m in scene.geometry.values()),vertices=sum(len(m.vertices) for m in scene.geometry.values()),recovery_reports=recoveries,trunk_hierarchy=trunk,model_calls=0,checkers=[],fea=False)
(out/'reexport_result.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result,indent=2))
