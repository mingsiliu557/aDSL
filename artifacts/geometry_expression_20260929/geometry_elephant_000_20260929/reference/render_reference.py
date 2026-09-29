from pathlib import Path
import json,sys
import bpy,trimesh
root=Path(__file__).resolve().parent
bpy.ops.object.select_all(action='SELECT');bpy.ops.object.delete(use_global=False)
mesh=trimesh.load(root/'mesh.ply',process=False)
data=bpy.data.meshes.new('elephant_000_reference')
data.from_pydata(mesh.vertices.tolist(),[],mesh.faces.tolist());data.update()
obj=bpy.data.objects.new('elephant_000_reference',data);bpy.context.collection.objects.link(obj)
bpy.ops.export_scene.gltf(filepath=str(root/'reference.glb'),export_format='GLB',export_yup=True)
# No geometry modification/reconstruction; the public PLY's vertices/faces are retained.
(root/'mesh_transport.json').write_text(json.dumps(dict(vertices=len(mesh.vertices),faces=len(mesh.faces),bounds=mesh.bounds.tolist(),transform='identity PLY to Blender; standard Blender glTF Y-up export',source_materials='PLY contains no vertex colors/material; neutral reference render'),indent=2))
