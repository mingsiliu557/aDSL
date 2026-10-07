"""Blender import and CPU preview adapter. Geometry files remain separate from originals."""
import argparse
from pathlib import Path
import numpy as np
from common import config, dump, load, sha


def import_reference(c, row, output):
    import bpy
    import mathutils
    import trimesh
    import manifold3d as mf
    from adsl.core.export.mesh_repair import (_normalize_zero_area_tessellation,
        _normalize_numeric_microcracks)
    from adsl.core.export.mesh_validity import _mesh_defects
    from adsl.core.assembly_topology import mesh_solid, solid_mesh, checked, union_print_mesh
    root=Path(c['root']); raw=root/row['raw_mesh']
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if raw.suffix.lower()=='.glb': bpy.ops.import_scene.gltf(filepath=str(raw))
    elif raw.suffix.lower()=='.blend': bpy.ops.wm.open_mainfile(filepath=str(raw))
    else: raise ValueError('unsupported reference format')
    objects=[o for o in bpy.context.scene.objects if o.type=='MESH']
    if not objects: raise ValueError('EMPTY_GEOMETRY')
    deps=bpy.context.evaluated_depsgraph_get()
    original=[]; points=[]
    # The Blender importer already converts glTF Y-up into Z-up.
    for o in objects:
        data=bpy.data.meshes.new_from_object(o.evaluated_get(deps),depsgraph=deps)
        o.data=data
        world=np.asarray(o.matrix_world,dtype=float)
        vertices=np.asarray([v.co[:] for v in data.vertices],dtype=float)
        if not len(vertices) or not np.isfinite(vertices).all(): raise ValueError('EMPTY_OR_NONFINITE_GEOMETRY')
        v=vertices@world[:3,:3].T+world[:3,3]
        points.append(v); original.append(dict(node=o.name,world_matrix=world.tolist(),vertex_count=len(v),materials=[m.name if m else None for m in data.materials]))
    bounds=np.array([np.vstack(points).min(axis=0),np.vstack(points).max(axis=0)])
    extent=bounds[1]-bounds[0]
    if extent.max()<=0: raise ValueError('ZERO_EXTENT')
    scale=c['longest_extent_mm']/extent.max()
    t=np.eye(4); t[:3,:3]*=scale
    t[:3,3]=[-(bounds[0,0]+bounds[1,0])*scale/2,-(bounds[0,1]+bounds[1,1])*scale/2,-bounds[0,2]*scale]
    operations=[]; solids=[]; defects=[]
    for o,v in zip(objects,points):
        positions=v*scale+t[:3,3]
        for vertex,p in zip(o.data.vertices,positions): vertex.co=p
        o.matrix_world=mathutils.Matrix.Identity(4)
        before=_mesh_defects(o.data)
        error=None
        try:
            _normalize_zero_area_tessellation(o)
            weld=_normalize_numeric_microcracks(o,1.0)
        except (ValueError,RuntimeError) as e:
            error=str(e); weld=None
        o.data.calc_loop_triangles()
        vertices=np.asarray([v.co[:] for v in o.data.vertices],dtype=float)
        faces=np.asarray([f.vertices[:] for f in o.data.loop_triangles],dtype=np.int64)
        unique,inverse=np.unique(vertices,axis=0,return_inverse=True)
        mesh=trimesh.Trimesh(unique,inverse[faces],process=False)
        reversed_winding=False
        if mesh.is_watertight and mesh.is_winding_consistent and mesh.volume<0:
            mesh.invert(); reversed_winding=True
        operations.append(dict(node=o.name,before=before,after=_mesh_defects(o.data),microcrack=weld,
            error=error,exact_duplicate_vertices_merged=len(vertices)-len(unique),winding_reversed=reversed_winding))
        try:
            solid, components, tolerance=union_print_mesh(mesh)
            solids.append(solid)
            operations[-1]['validated_material_islands']=len(components)
            operations[-1]['volume_mm3_after_union']=float(solid.volume())
        except (ValueError,RuntimeError) as e: defects.append(dict(node=o.name,reason=str(e)))
    transform=dict(source_sha256=sha(raw),source_axis='glTF Y-up; imported once by Blender' if raw.suffix=='.glb' else 'blend Z-up (requires manual confirmation)',
        normalization_mm=t.tolist(),original_bounds_imported= bounds.tolist(),target_longest_extent_mm=c['longest_extent_mm'],
        original_nodes=original,operations=operations,material_union='existing union_print_mesh, signed cavities preserved',print_pose_separate=True,preview_export_scale_mm_to_m=.001)
    dump(output/'use_pose.json',transform)
    union=None; parts=0
    if not defects:
        try:
            united=checked(mf.Manifold.batch_boolean(solids,mf.OpType.Add))
            union=solid_mesh(united)
            parts=sum(s.volume()>0 for s in united.decompose())
            np.savez(output/'reference_whole.body.npz',vertices=union.vertices,faces=union.faces)
            union.export(output/'reference_whole.stl',file_type='stl_ascii')
            source=output/'reference_source.json'
            dump(source,dict(raw_sha256=sha(raw),use_pose_sha256=sha(output/'use_pose.json'),geometry='union of all validated material volumes; no semantic part inference'))
            identity=np.eye(4).tolist(); body=output/'reference_whole.body.npz'; stl=output/'reference_whole.stl'
            dump(output/'assembly_manifest.json',dict(root_id='reference_whole',mm_per_unit=1.,source_sha256=sha(source),connections=[],
                part_declarations=[dict(id='reference_whole',components=['reference_whole'],assembly_transform=identity)],
                parts=[dict(id='reference_whole',stl=stl.name,print_transform_mm=identity,assembly_transform=identity)],
                files_sha256={stl.name:sha(stl)},partition_reference_inputs=[dict(part_id='reference_whole',status='PASS',frame='part_local_mm',npz=body.name,sha256=sha(body),source_sha256=sha(source))]))
        except (ValueError,RuntimeError) as e:
            defects.append(dict(node='material_union',reason=str(e)))
    # Retain original texture/material/node boundaries for visual review, not physics masses.
    for o in list(bpy.context.scene.objects):
        o.select_set(o in objects)
        if o in objects: o.matrix_world=mathutils.Matrix.Scale(.001,4)
    bpy.ops.export_scene.gltf(filepath=str(output/'reference.glb'),export_format='GLB',use_selection=True,export_yup=True)
    report=dict(status='PASS' if union is not None else 'INDETERMINATE',raw_sha256=sha(raw),defects=defects,
        volume_mm3=float(union.volume) if union is not None else None,
        bounds_mm=union.bounds.tolist() if union is not None else None,
        surface_area_mm2=float(union.area) if union is not None else None,
        connected_components=parts if union is not None else None,
        standing_eligible=union is not None and parts==1,
        standing_limitation=None if union is not None and parts==1 else 'Unmeasurable or disconnected reference; no artificial whole-body binding.',
        vertices=len(union.vertices) if union is not None else None,triangles=len(union.faces) if union is not None else None,
        source_sha256=sha(output/'reference_source.json') if union is not None else None,glb_sha256=sha(output/'reference.glb'))
    dump(output/'basic.json',report)


def preview(c, glb, output, mode):
    import bpy
    from PIL import Image
    from adsl.tools.render import render_multiview, review_eight_render_views
    render=c['render']; views=review_eight_render_views(elevation=15)
    render_multiview(output,glb,views,width=render['width'],height=render['height'],render_samples=render['samples'],render_threads=render['threads'],background='transparent',material_mode=mode)
    meta=load(output/'meta.json'); hashes=[]; matrices=[]
    for frame,(record,view) in enumerate(zip(meta['locations'],views),start=1):
        bpy.context.scene.frame_set(frame); bpy.context.view_layer.update()
        camera=bpy.context.scene.camera
        matrix=np.asarray(camera.matrix_world,dtype=float)
        if not np.allclose(matrix,np.asarray(view.camera_matrix),rtol=0,atol=1e-5): raise ValueError(f'CAMERA_FRAME_MISMATCH:{frame}')
        record.update(transform_matrix=matrix.tolist(),frame=frame,lens_mm=camera.data.lens,
            camera_angle_y=camera.data.angle_y,clip_start=camera.data.clip_start,clip_end=camera.data.clip_end,
            projection_matrix=np.asarray(camera.calc_matrix_camera(bpy.context.evaluated_depsgraph_get(),x=render['width'],y=render['height']),dtype=float).tolist())
        image=output/record['file']; rgba=Image.open(image).convert('RGBA'); alpha=np.asarray(rgba)[:,:,3]
        occupied=np.argwhere(alpha>0)
        if not len(occupied): raise ValueError('EMPTY_PREVIEW')
        # A fixed distance serves all views; no per-view enlargement.
        record['alpha_bounds_pixels']=[occupied.min(axis=0).tolist(),occupied.max(axis=0).tolist()]
        record['image_sha256']=sha(image); hashes.append(sha(image)); matrices.append(matrix.tolist())
    if len({__import__('json').dumps(m) for m in matrices})!=8: raise ValueError('REPEATED_CAMERA_RECORD')
    if len(set(hashes))<2: raise ValueError('ALL_PREVIEWS_IDENTICAL')
    meta.update(camera_records_corrected_per_frame=True,normalization='renderer common scene normalization, factor=0.8; shared camera distance',engine='CYCLES',device='CPU')
    dump(output/'meta.json',meta)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);p.add_argument('--row');p.add_argument('--output',type=Path,required=True);p.add_argument('--stage',choices=['import','render'],required=True);p.add_argument('--glb',type=Path);p.add_argument('--mode',choices=['native','neutral'],default='neutral')
    a=p.parse_args();c=config(a.config);a.output.mkdir(parents=True,exist_ok=True)
    if a.stage=='import': import_reference(c,load(a.row),a.output)
    else: preview(c,a.glb,a.output,a.mode)
