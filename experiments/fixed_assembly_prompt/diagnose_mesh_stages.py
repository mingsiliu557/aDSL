"""Bounded read-only construction reproduction; snapshot existing Boolean stages.

No geometry/config mutation or generation API. One selected print part only.
Run inside the existing checker process boundary (120 seconds).
"""
from __future__ import annotations
import argparse
import importlib
from pathlib import Path
import runpy
import sys
import time
import numpy as np
import trimesh

REPO=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(REPO))
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.utils.io import read_json,write_json
from adsl.core.export.export_assembly import evaluated


def mesh_stats(mesh):
    counts=np.bincount(mesh.edges_unique_inverse,minlength=len(mesh.edges_unique))
    boundary=mesh.vertices[mesh.edges_unique[counts==1]]
    bad=mesh.triangles[mesh.area_faces<=0]
    return dict(vertices=len(mesh.vertices),triangles=len(mesh.faces),
        zero_area_faces=len(bad),boundary_edge_count=int((counts==1).sum()),
        nonmanifold_edge_count=int((counts>2).sum()),
        zero_area_triangles_mm=bad.tolist(),boundary_edges_mm=boundary.tolist(),
        bounds_mm=mesh.bounds.tolist())


def snapshot(obj,folder,unit):
    folder.mkdir(parents=True)
    # Polygon topology BEFORE calc_loop_triangles, as well as its tessellation.
    v=np.array([tuple(obj.matrix_world@p.co) for p in obj.data.vertices])*unit
    unique,inv=np.unique(v,axis=0,return_inverse=True)
    polygons=[inv[list(p.vertices)].tolist() for p in obj.data.polygons]
    edges={}
    for poly in polygons:
        for a,b in zip(poly,poly[1:]+poly[:1]):
            key=tuple(sorted((a,b)));edges[key]=edges.get(key,0)+1
    boundary=[unique[list(e)].tolist() for e,n in edges.items() if n==1]
    zero_polys=[]
    for i,poly in enumerate(polygons):
        points=unique[poly]
        if not np.any(np.cross(points,np.roll(points,-1,axis=0)).sum(axis=0)):
            zero_polys.append(i)
    write_json(folder/'polygons.json',dict(vertices_mm=unique.tolist(),polygons=polygons,
        boundary_edges_mm=boundary,zero_area_polygon_ids=zero_polys))
    obj.data.calc_loop_triangles()
    faces=inv[np.array([tuple(t.vertices) for t in obj.data.loop_triangles],dtype=int)]
    mesh=trimesh.Trimesh(unique,faces,process=False)
    np.savez(folder/'triangles.npz',vertices=mesh.vertices,faces=mesh.faces)
    row=mesh_stats(mesh)
    row.update(object_name=obj.name,semantic_path=obj.parent.get('adsl_path') if obj.parent else None,
        polygon_boundary_edge_count=len(boundary),polygon_nonmanifold_edge_count=sum(n>2 for n in edges.values()),
        zero_area_polygons=len(zero_polys),polygon_count=len(polygons),snapshot=str(folder))
    write_json(folder/'summary.json',row)
    return row


def main():
    parser=argparse.ArgumentParser(__doc__)
    for key in ('source','manifest','output'):parser.add_argument('--'+key,type=Path,required=True)
    parser.add_argument('--part',required=True)
    args=parser.parse_args();out=args.output;out.mkdir(parents=True,exist_ok=True)
    source_hash=sha256_file(args.source);manifest=read_json(args.manifest)
    assert source_hash==manifest['source_sha256']
    started=time.monotonic()
    ns=runpy.run_path(str(args.source),run_name='__adsl_generated__')
    assembly=ns['assembly'];unit=assembly.mm_per_unit
    assert unit==manifest['mm_per_unit']
    glb=importlib.import_module('adsl.core.export.export_glb')
    original=glb._apply_boolean;rows=[]
    def observed(base,other,operation):
        index=len(rows);folder=out/f'boolean_{index:02d}'
        row=dict(operation=operation,before_base=snapshot(base,folder/'before_base',unit),
            before_operand=snapshot(other,folder/'before_operand',unit))
        rows.append(row)
        original(base,other,operation)  # unmodified FAST/EXACT policy
        row['after']=snapshot(base,folder/'after',unit)
        write_json(out/'boolean_stages.json',rows)
    glb._apply_boolean=observed
    try:
        mesh,_,metadata=evaluated(assembly.parts[args.part],out/'evaluated.glb',unit,
            keep_materials=True,validate_geometry=False)
    finally:
        glb._apply_boolean=original
    np.savez(out/'before_serialization.npz',vertices=mesh.vertices,faces=mesh.faces)
    before=mesh_stats(mesh)
    print_mesh=mesh.copy();matrix=np.eye(4);matrix[2,3]=-mesh.bounds[0,2]
    print_mesh.apply_transform(matrix);print_mesh.export(out/'diagnostic.stl')
    loaded=trimesh.load_mesh(out/'diagnostic.stl',process=False)
    loaded.apply_transform(np.linalg.inv(matrix))
    v,inv=np.unique(loaded.vertices,axis=0,return_inverse=True)
    loaded=trimesh.Trimesh(v,inv[loaded.faces],process=False)
    after=mesh_stats(loaded)
    part=next(p for p in manifest['parts'] if p['id']==args.part)
    from adsl.core.assembly_topology import read_print_mesh
    saved=read_print_mesh(args.manifest.parent/part['stl'],part['print_transform_mm'])
    # Exact triangles in order, not a fuzzy repair or acceptance criterion.
    match=bool(np.array_equal(saved.triangles,loaded.triangles))
    assert sha256_file(args.source)==source_hash
    result=dict(source=str(args.source),source_sha256=source_hash,part_id=args.part,
        manifest=str(args.manifest),mm_per_unit=unit,boolean_stages=rows,
        before_serialization=before,after_serialization=after,
        matches_saved_final_triangles=match,metadata=metadata,
        elapsed_seconds=time.monotonic()-started,source_modified=False,
        conclusion='Stage evidence only; no model repair or changed Boolean policy.')
    write_json(out/'stages.json',result)
    write_json(out/'result.json',dict(checker='stage_diagnostic',status='PASS',
        summary='Diagnostic completed; PASS means capture completed, not geometry valid',
        metrics={'elapsed_seconds':result['elapsed_seconds'],'matches_saved_final_triangles':match}))
    print(dict(part=args.part,before=before['zero_area_faces'],after=after['zero_area_faces'],
        matches_saved=match,booleans=len(rows)),flush=True)


if __name__=='__main__':main()
