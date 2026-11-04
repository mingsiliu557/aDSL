# Vendored unchanged from mingsiliu557/aDSL f65b0dbb9c0e3a48d45f870cdb7091949aa2472a
# Source: adsl-core/core/export/export_assembly.py
import numpy as np


def mesh_solid(mesh, face_ids=None):
    from .mesh_validity import validate_mesh
    solid, _ = validate_mesh(mesh.vertices, mesh.faces, face_ids=face_ids)
    return solid

def solid_mesh(solid):
    import manifold3d as mf
    import trimesh
    if solid.status() != mf.Error.NoError:
        raise ValueError(f"Manifold Boolean failed: {solid.status()}")
    raw = solid.to_mesh64()
    return trimesh.Trimesh(np.asarray(raw.vert_properties)[:, :3], np.asarray(raw.tri_verts), process=False)
