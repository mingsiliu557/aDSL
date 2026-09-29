from pathlib import Path
import json
import numpy as np
import trimesh,manifold3d as m3
O=Path('/vepfs_default/chanxueyan/lhp/lms/aDSL/temp/core_showcase_20260929');out={}
for key in ['SF16_before','SF16_after']:
 solids={}
 for name in ['top_shelf','slanted_back_panel']:
  a=np.load(O/'resources'/key/(name+'.world.npz'));m=trimesh.Trimesh(a['vertices'],a['faces'],process=True)
  s=m3.Manifold(m3.Mesh64(np.array(m.vertices,dtype=np.float64),np.array(m.faces,dtype=np.uint64)))
  assert s.status()==m3.Error.NoError,(name,s.status());solids[name]=s
 overlap=solids['top_shelf']^solids['slanted_back_panel'];assert overlap.status()==m3.Error.NoError
 sections={}
 for name,s in dict(solids,overlap=overlap).items():
  s=s.transform([[0,1,0,0],[0,0,1,0],[1,0,0,0]])
  sections[name]=[np.asarray(p).tolist() for p in s.slice(0).to_polygons()]
 out[key]=dict(section_plane='assembly x=0 mm',axes=['y_mm','z_mm'],polygons=sections,display_recomputed_intersection_mm3=overlap.volume())
 print(key,overlap.volume(),{k:len(v) for k,v in sections.items()})
(O/'sections.json').write_text(json.dumps(out,indent=2)+'\n')
