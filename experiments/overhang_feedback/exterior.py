"""Detector-copy exterior extraction. No source edits or mesh-repair fallback.

Run in its own process; the caller owns the timeout. Uses the installed bpy.
"""
from __future__ import annotations

import json
from pathlib import Path
import sys


def main() -> None:
    import bpy
    import bmesh
    payload = json.loads(Path(sys.argv[1]).read_text())
    bpy.ops.wm.read_factory_settings(use_empty=True)
    objects = []
    for i, row in enumerate(payload):
        data = bpy.data.meshes.new(f"operand_{i}")
        data.from_pydata(row["vertices"], [], row["faces"])
        data.update()
        obj = bpy.data.objects.new(data.name, data)
        bpy.context.collection.objects.link(obj)
        objects.append(obj)
    if not objects:
        raise ValueError("empty exterior input")
    base = objects[0]
    bpy.context.view_layer.objects.active = base
    base.select_set(True)
    for i, other in enumerate(objects[1:], 1):
        print(f"EXACT union {i}/{len(objects)-1}", flush=True)
        modifier = base.modifiers.new("detector_union", "BOOLEAN")
        modifier.operation = "UNION"
        modifier.solver = "EXACT"
        modifier.object = other
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        bpy.data.objects.remove(other, do_unlink=True)
    # Explicitly triangulate Boolean n-gons. The loop-triangle cache can emit
    # collinear triangles at Boolean-created boundary vertices. Ear clipping
    # changes tessellation only; do not dissolve vertices or remove small faces.
    bm = bmesh.new()
    try:
        bm.from_mesh(base.data)
        bmesh.ops.triangulate(bm, faces=list(bm.faces), quad_method="BEAUTY", ngon_method="EAR_CLIP")
        bm.to_mesh(base.data)
    finally:
        bm.free()
    base.data.calc_loop_triangles()
    Path(sys.argv[2]).write_text(json.dumps({
        "vertices": [list(v.co) for v in base.data.vertices],
        "faces": [list(t.vertices) for t in base.data.loop_triangles],
        "blender_version": bpy.app.version_string,
        "triangulation": "bmesh_EAR_CLIP_v1",
    }))


if __name__ == "__main__":
    main()
