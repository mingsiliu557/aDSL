"""Render existing meshes only; no candidate generation, repair or checker runs."""
from pathlib import Path
import json,math
import numpy as np
import bpy
from mathutils import Vector
R=Path('/vepfs_default/chanxueyan/lhp/lms/aDSL');O=R/'temp/core_showcase_20260929';P=O/'panels';P.mkdir(exist_ok=True)
D=json.loads((O/'render_inputs.json').read_text());records=[]
COLORS=['#467BC1','#D38B45','#62AB90','#9774B6','#CE718A','#6BAFBF','#AFAC60','#BF886C']
def rgba(s):return tuple(int(s[i:i+2],16)/255 for i in (1,3,5))+(1,)
def mat(name,c):
 m=bpy.data.materials.new(name);m.diffuse_color=rgba(c);m.use_nodes=True;b=m.node_tree.nodes.get('Principled BSDF');b.inputs['Base Color'].default_value=rgba(c);b.inputs['Roughness'].default_value=.72;return m
def reset():
 bpy.ops.wm.read_factory_settings(use_empty=True)
 s=bpy.context.scene;s.render.engine='CYCLES';s.cycles.device='CPU';s.cycles.samples=24;s.cycles.use_denoising=True;s.render.threads_mode='FIXED';s.render.threads=4
 s.render.resolution_x=900;s.render.resolution_y=760;s.render.resolution_percentage=100;s.render.film_transparent=True
 s.view_settings.view_transform='AgX';s.view_settings.look='None';s.view_settings.exposure=0;s.view_settings.gamma=1
 w=bpy.data.worlds.new('studio');w.use_nodes=True;w.node_tree.nodes['Background'].inputs[0].default_value=(1,1,1,1);w.node_tree.nodes['Background'].inputs[1].default_value=.35;s.world=w
 for loc,power,size in [((3,-4,6),130,4),((-3,-1,3),55,3),((1,4,4),85,3)]:
  ld=bpy.data.lights.new('softbox','AREA');ld.energy=power;ld.size=size;ob=bpy.data.objects.new('softbox',ld);s.collection.objects.link(ob);ob.location=loc;ob.rotation_euler=(Vector((0,0,.5))-ob.location).to_track_quat('-Z','Y').to_euler()
 return s
def mesh(name,v,f,c):
 data=bpy.data.meshes.new(name);data.from_pydata(v,[],f);data.update();ob=bpy.data.objects.new(name,data);bpy.context.collection.objects.link(ob);ob.data.materials.append(mat(name,c));return ob
def rod(a,b,r,c):
 v=Vector(b)-Vector(a)
 bpy.ops.mesh.primitive_cylinder_add(vertices=12,radius=r,depth=v.length,location=(Vector(a)+Vector(b))/2)
 ob=bpy.context.object;ob.rotation_euler=v.to_track_quat('Z','Y').to_euler();ob.data.materials.append(mat('guide',c))
def sphere(p,r,c):
 bpy.ops.mesh.primitive_uv_sphere_add(segments=16,ring_count=8,radius=r,location=p);bpy.context.object.data.materials.append(mat('marker',c))
def shot(name,target=(0,0,.5),direction=(1.4,-2.5,1.1),scale=1.3):
 s=bpy.context.scene;ca=bpy.data.cameras.new('camera');ob=bpy.data.objects.new('camera',ca);s.collection.objects.link(ob);ob.location=Vector(target)+Vector(direction).normalized()*4;ob.rotation_euler=(Vector(target)-ob.location).to_track_quat('-Z','Y').to_euler();ca.type='ORTHO';ca.ortho_scale=scale;ca.lens=50;ca.clip_end=100;s.camera=ob
 s.render.filepath=str(P/(name+'.png'));bpy.ops.render.render(write_still=True);records.append(dict(panel=name,target=target,direction=direction,ortho_scale=scale));print('PANEL_DONE',name,flush=True)
 return ob
def assembly(key,mode='assembled'):
 reset();info=D[key];bounds=np.array(info['bounds']);extent=bounds[1]-bounds[0];scale=max(extent);origin=np.r_[(bounds[0,:2]+bounds[1,:2])/2,bounds[0,2]];parts=info['parts'];m=json.loads(Path(info['manifest']).read_text());offsets={};palette={}
 for i,pid in enumerate(parts):
  if key.startswith('SF10'):
   c={'tabletop':COLORS[0],'left_pedestal':COLORS[1],'left_pedestal_lower_brace':COLORS[1],'right_pedestal':COLORS[2],'upper_brace':COLORS[3],'lower_brace':COLORS[4]}[pid]
   off={'tabletop':(0,0,.23),'left_pedestal':(-.15,0,0),'left_pedestal_lower_brace':(-.15,0,0),'right_pedestal':(.15,0,0),'upper_brace':(0,-.16,.03),'lower_brace':(0,-.22,0)}[pid] if mode=='exploded' else (0,0,0)
  elif key=='SF13':
   c=COLORS[i]; off=((-.2,0,0) if pid=='side_left' else (.2,0,0) if pid=='side_right' else (0,0,.12) if pid=='cap_top' else (0,-.12,0)) if mode=='exploded' else (0,0,0)
  else:
   c=COLORS[0] if pid=='top_shelf' else COLORS[1] if pid=='slanted_back_panel' else '#C8CED5';off=(0,0,0)
  a=np.load(O/'resources'/key/(pid+'.world.npz'));v=(a['vertices']-origin)/scale+np.array(off);mesh(pid,v.tolist(),a['faces'].tolist(),c);offsets[pid]=np.array(off);palette[pid]=c
 if key=='SF13' and mode=='exploded':
  lookup={p['id']:p for p in m['parts']}
  for co in m['connections']:
   tf=np.array(lookup[co['tab_part']]['assembly_transform'])@np.array(co['tab_frame']);p=(tf[:3,3]*m['mm_per_unit']-origin)/scale
   a=p+offsets[co['tab_part']];b=p+offsets[co['slot_part']];rod(a,b,.0013,'#B8BDC7');sphere(a,.0045,'#C74742');sphere(b,.0045,'#C74742')
 info['colors']=palette
 return m,scale,origin
for arm in ['A','B']:
 for case in ['SF21','SF06']:
  reset();result=json.loads((R/'temp/geometry_expression_20260929'/arm/case/'demo_result.json').read_text());bpy.ops.import_scene.gltf(filepath=result['glb']);obs=[o for o in bpy.context.scene.objects if o.type=='MESH'];hidden=[]
  for o in obs[:]:
   par=o.parent;paths=[]
   while par:paths.append(par.get('adsl_path',par.name));par=par.parent
   if any(p.lower().endswith(('/background','/presentation_background')) or '/presentation_background/' in p.lower() or '/background/' in p.lower() for p in paths):o.hide_render=True;obs.remove(o);hidden.append(o.name)
  verts=np.array([tuple(o.matrix_world@v.co) for o in obs for v in o.data.vertices]);lo=verts.min(0);hi=verts.max(0);factor=max(hi-lo);origin=np.r_[(lo[:2]+hi[:2])/2,lo[2]]
  for o in obs:
   o.data=o.data.copy()
   vs=[tuple((np.array(o.matrix_world@v.co)-origin)/factor) for v in o.data.vertices];o.data.vertices.foreach_set('co',np.array(vs).reshape(-1));o.parent=None;o.matrix_world.identity();o.data.materials.clear();o.data.materials.append(mat('neutral','#B7C3D0'))
  records.append(dict(panel=f'{case}_{arm}',original_glb=result['glb'],source_sha256=result['source_sha256'],hidden_display_background_nodes=hidden,normalization_scale=float(factor)))
  shot(f'{case}_{arm}',scale=1.25,direction=(1.4,-2.5,1.25))
  if case=='SF21':shot(f'{case}_{arm}_detail',target=(0,0,.84),direction=(1.0,-2.3,1.9),scale=.46)
for key in ['SF13','SF10_before','SF10_after','SF16_before','SF16_after']:
 assembly(key)
 if key=='SF13':shot(key+'_assembled',scale=1.3,direction=(1.4,-2.8,1.2))
 elif key.startswith('SF10'):shot(key+'_assembled',target=(0,0,.25),scale=1.22,direction=(1.3,-2.5,1.25))
 else:shot(key+'_assembled',scale=1.3,direction=(1.8,-2.8,1.1))
 if key in ['SF13','SF10_before','SF10_after']:
  assembly(key,'exploded');shot(key+'_exploded',target=(0,0,.55) if key=='SF13' else (0,-.035,.34),scale=1.55 if key=='SF13' else 1.62,direction=(1.2,-2.7,1.25))
(O/'render_records.json').write_text(json.dumps(records,indent=2)+'\n');(O/'render_inputs.json').write_text(json.dumps(D,indent=2)+'\n')
