from pathlib import Path
import trimesh
from .common import sha,write_json

def export_parts(parts, directory, *, metadata=None):
    directory=Path(directory);directory.mkdir(parents=True,exist_ok=True)
    rows=[];scene=trimesh.Scene()
    for i,mesh in sorted(parts.items()):
        path=directory/f'{i}.stl';mesh.export(path)
        # PLY preserves per-part topology without STL duplicate vertices.
        mesh.export(directory/f'{i}.ply')
        rows.append(dict(part_id=i,stl=path.name,stl_sha256=sha(path),ply=f'{i}.ply'))
        scene.add_geometry(mesh,node_name=i,geom_name=i)
    scene.export(directory/'assembly.glb')
    manifest=dict(parts=rows,assembly_glb='assembly.glb',assembly_sha256=sha(directory/'assembly.glb'),metadata=metadata or {})
    write_json(directory/'manifest.json',manifest);return manifest

def load_parts(directory):
    import json
    directory=Path(directory);manifest=json.loads((directory/'manifest.json').read_text())
    result={}
    for row in manifest['parts']:
        if sha(directory/row['stl'])!=row['stl_sha256']:raise ValueError('ASSET_HASH_MISMATCH')
        # Prefer hash-bound STL; exact duplicate welding is serialization decoding.
        result[row['part_id']]=trimesh.load_mesh(directory/row['stl'],process=True)
    return result
