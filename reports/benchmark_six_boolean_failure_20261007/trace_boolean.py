from pathlib import Path
import hashlib
import importlib
import inspect
import json
import runpy
import shutil
import sys
import time
import traceback

import bpy
import bmesh
import numpy as np

ROOT = Path(__file__).resolve().parent
SOURCE = Path('/jiigan-hp/lms/aDSL/experiment/benchmark_six_main_20261007T045229Z/jobs/ABO_B075X2XZDD/ours/generation/native/source.py')
eg = importlib.import_module('adsl.core.export.export_glb')
ROOT.mkdir(parents=True, exist_ok=True)
shutil.copyfile(SOURCE, ROOT / 'source.py')
events, groups, stack = [], [], []
t0 = time.monotonic()

def save():
    (ROOT / 'trace.json').write_text(json.dumps(dict(
        source=str(SOURCE), source_sha256=hashlib.sha256(SOURCE.read_bytes()).hexdigest(),
        exporter_file=eg.__file__, elapsed_seconds=time.monotonic()-t0,
        groups=groups, events=events), indent=2, default=str))

def log(event, **values):
    events.append(dict(event=event, elapsed_seconds=time.monotonic()-t0,
                       shape_stack=list(stack), **values))
    save()

def stats(v, f):
    v, f = np.asarray(v), np.asarray(f, dtype=int)
    uv, inv = np.unique(v, axis=0, return_inverse=True)
    fs = inv[f]
    edges = np.concatenate((fs[:,[0,1]], fs[:,[1,2]], fs[:,[2,0]]))
    unique_edges, ids, counts = np.unique(np.sort(edges, axis=1), axis=0,
                                         return_inverse=True, return_counts=True)
    winding = np.bincount(ids, weights=np.where(edges[:,0] < edges[:,1], 1, -1))
    areas2 = np.linalg.norm(np.cross(v[f[:,1]]-v[f[:,0]], v[f[:,2]]-v[f[:,0]]),axis=1)
    defects = dict(vertices=len(v), triangles=len(f), exact_coordinate_duplicates=len(v)-len(uv),
       zero_area_triangles=int((areas2 == 0).sum()),
       duplicate_faces=len(fs)-len(np.unique(np.sort(fs,axis=1),axis=0)),
       boundary_edges=int((counts==1).sum()), nonmanifold_edges=int((counts>2).sum()),
       inconsistent_edges=int(((counts==2)&(winding!=0)).sum()),
       smallest_positive_triangle_area=float(areas2[areas2>0].min()/2) if (areas2>0).any() else None)
    bad = unique_edges[(counts != 2) | (winding != 0)]
    if len(bad):
        pts = uv[np.unique(bad)]
        defects.update(defect_bounds=[pts.min(axis=0).tolist(), pts.max(axis=0).tolist()],
                       defect_edge_samples=uv[bad[:20]].tolist())
    return defects

original_build = eg._build_shape
def build(shape, world_xform=None, **kw):
    entry = dict(label=shape.label, path=kw.get('path'), node_name=kw.get('node_name'),
                 primitive_types=[p['type'] for p in shape.iter_local_primitives()])
    stack.append(entry)
    try:
        return original_build(shape, world_xform, **kw)
    finally:
        stack.pop()
eg._build_shape = build

original_group = eg._apply_boolean_group
def group(base, others, operation):
    gid = len(groups)
    row = dict(group_id=gid, operation=operation, shape_stack=list(stack),
               operand_count=1+len(others), base_name=base.name,
               operands=[dict(name=o.name, vertices=len(o.data.vertices), polygons=len(o.data.polygons))
                         for o in [base,*others]])
    groups.append(row)
    started = time.monotonic()
    try:
        result = original_group(base, others, operation)
        row.update(status='SUCCESS', properties={k:base[k] for k in base.keys() if k.startswith('adsl_')})
        return result
    except Exception as exc:
        row.update(status='FAILED', error=str(exc))
        raise
    finally:
        row['elapsed_seconds'] = time.monotonic()-started
        save()
eg._apply_boolean_group = group

original_defects = eg._mesh_defects
def defects(data):
    result = original_defects(data)
    if result:
        v,f,_ = eg._mesh_triangles(data, eg.mathutils.Matrix.Identity(4))
        name = 'defect_%02d' % len(events)
        np.savez(ROOT / (name+'.npz'), vertices=v, triangles=f)
        log('mesh_defects', mesh_name=data.name, defects=result,
            stats=stats(v,f), snapshot=name+'.npz')
    return result
eg._mesh_defects = defects

original_normalize = eg._normalize_zero_area_tessellation
def normalize(obj):
    try:
        return original_normalize(obj)
    except Exception as exc:
        v,f,_ = eg._mesh_triangles(obj.data, eg.mathutils.Matrix.Identity(4))
        prefix = 'exact_boolean_failed'
        np.savez(ROOT/(prefix+'.npz'),vertices=v,triangles=f)
        log('exact_boolean_normalization_failed', object=obj.name,error=str(exc),
            stats=stats(v,f),polygon_count=len(obj.data.polygons),snapshot=prefix+'.npz')
        raise
eg._normalize_zero_area_tessellation = normalize

original_recover = eg._recover_boolean
def recover(base, operands, operation):
    mf=importlib.import_module('manifold3d')
    inputs=[]
    for o in operands:
        v,f,_=eg._mesh_triangles(o.data,o.matrix_world)
        uv,inv=np.unique(v,axis=0,return_inverse=True)
        s=mf.Manifold(mf.Mesh64(uv,inv[f].astype(np.uint64)))
        inputs.append(dict(object=o.name,stats=stats(v,f),
            manifold_status=str(s.status()),empty=s.is_empty(),volume=s.volume()))
    log('recovery_operands', operands=inputs)
    return original_recover(base,operands,operation)
eg._recover_boolean = recover

recover_code = original_recover.__code__
def tracer(frame, event, arg):
    if frame.f_code is not recover_code:
        return None
    loc = frame.f_locals
    if event == 'call':
        log('recover_start', operation=loc['operation'],
            base=loc['base'].name, operand_count=len(loc['operands']))
    elif event == 'line' and frame.f_lineno == 302:
        ref = loc['reference']
        log('manifold_reference', status=str(ref.status()), empty=ref.is_empty(),
            volume=ref.volume(), surface_area=ref.surface_area(), components=len(ref.decompose()),
            raw_stats=stats(loc['rv'],loc['rf']), quantum=loc['quantum'],
            displacement_bound=loc['displacement_bound'])
        np.savez(ROOT / 'reference.npz', vertices=loc['rv'],triangles=loc['rf'])
    elif event == 'line' and frame.f_lineno == 317:
        world, local, raw, data = loc['world'],loc['local'],loc['raw'],loc['data']
        v32 = np.asarray([tuple(v.co) for v in data.vertices])
        f = np.asarray(raw.tri_verts)
        prefix = 'attempt_%s' % str(loc['tolerance']).replace('.','_')
        np.savez(ROOT / (prefix+'_preweld.npz'), world64=world, local64=local,
                 local32=v32, triangles=f)
        collapsed = []
        _, inv, counts = np.unique(v32, axis=0, return_inverse=True, return_counts=True)
        for c in np.flatnonzero(counts > 1)[:30]:
            ids = np.flatnonzero(inv==c)
            points=local[ids]
            collapsed.append(dict(indices=ids.tolist(),float32=v32[ids[0]].tolist(),
                 float64=points.tolist(), max_separation=float(np.linalg.norm(points[:,None]-points[None,:],axis=2).max())))
        log('recovery_target_pre_weld', tolerance=loc['tolerance'],
            manifold_status=str(loc['solid'].status()), volume=loc['solid'].volume(),
            stats64=stats(local,f), stats32=stats(v32,f), collapsed_vertex_groups=collapsed,
            max_rounding_displacement=float(np.linalg.norm(local-v32,axis=1).max()),
            snapshot=prefix+'_preweld.npz')
    elif event == 'exception' and str(arg[1]) == 'target precision weld is not manifold':
        bm = loc['bm']
        bm.verts.index_update(); bm.edges.index_update(); bm.faces.index_update()
        edges=[e for e in bm.edges if not(e.is_manifold and e.is_contiguous)]
        verts=[v for v in bm.verts if not v.is_manifold]
        pts=np.asarray([tuple(v.co) for e in edges for v in e.verts]+[tuple(v.co) for v in verts])
        log('target_precision_not_manifold', tolerance=loc['tolerance'],
            exact_welds=len(loc['targetmap']), vertices=len(bm.verts),edges=len(bm.edges),faces=len(bm.faces),
            boundary_edges=sum(e.is_boundary for e in bm.edges),
            wire_edges=sum(e.is_wire for e in bm.edges),
            nonmanifold_edges=sum(not e.is_manifold for e in bm.edges),
            noncontiguous_edges=sum(not e.is_contiguous for e in bm.edges),
            nonmanifold_vertices=len(verts),
            defect_bounds=[pts.min(axis=0).tolist(),pts.max(axis=0).tolist()] if len(pts) else None,
            defect_edges=[dict(vertices=[tuple(v.co) for v in e.verts],linked_faces=len(e.link_faces),
                boundary=e.is_boundary, manifold=e.is_manifold,contiguous=e.is_contiguous,
                length=e.calc_length()) for e in edges[:40]],
            defect_vertices=[dict(co=tuple(v.co),linked_edges=len(v.link_edges),
                linked_faces=len(v.link_faces)) for v in verts[:40]])
        prefix='attempt_%s' % str(loc['tolerance']).replace('.','_')
        np.savez(ROOT/(prefix+'_postweld.npz'),vertices=np.asarray([tuple(v.co) for v in bm.verts]),
                 faces=np.asarray([[v.index for v in f.verts] for f in bm.faces],dtype=object))
    return tracer

sys.settrace(tracer)
try:
    ns=runpy.run_path(str(ROOT/'source.py'))
    bpy.ops.wm.read_factory_settings(use_empty=True)
    shape=ns['assembly'].parts['upper_structure']
    log('start_build', part_id='upper_structure', shape_label=shape.label)
    objs=eg._build_shape(shape, path='upper_structure')
    log('build_succeeded', object_names=[o.name for o in objs])
except Exception as exc:
    log('build_failed', error=str(exc), traceback=traceback.format_exc())
finally:
    sys.settrace(None)
    save()
print(json.dumps(dict(output=str(ROOT),elapsed_seconds=time.monotonic()-t0,
                     group_count=len(groups),final_event=events[-1]['event'])))
