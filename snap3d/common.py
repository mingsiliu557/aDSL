from pathlib import Path
import hashlib
import json
import numpy as np
import trimesh
from .vendor import mesh_solid, solid_mesh, checked

def sha(path):
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1024*1024), b''): h.update(block)
    return h.hexdigest()

def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()

def write_json(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')

def component_count(mesh):
    # Count positive boundary shells; negative cavity shells are not material islands.
    return sum(s.volume() > 0 for s in checked(mesh_solid(mesh)).decompose())

def union(solids):
    import manifold3d as mf
    return checked(mf.Manifold.batch_boolean(list(solids), mf.OpType.Add))

def normalize(mesh, size_mm=150., transform=None):
    result = mesh.copy()
    if transform is None:
        # Upstream Hunyuan is Y-up. Freeze this one common proper rotation.
        transform = np.eye(4); transform[:3,:3] = [[1,0,0],[0,0,-1],[0,1,0]]
        result.apply_transform(transform)
        scale = size_mm / np.max(result.extents)
        transform[:3,:3] *= scale
        result = mesh.copy(); result.apply_transform(transform)
        transform[2,3] = -result.bounds[0,2]
    result = mesh.copy(); result.apply_transform(transform)
    return result, np.asarray(transform), float(np.linalg.norm(result.extents))

def scene_parts(scene):
    if isinstance(scene, trimesh.Trimesh): return {'part_000':scene.copy()}
    parts = {}
    for i, node in enumerate(sorted(scene.graph.nodes_geometry)):
        matrix, key = scene.graph[node]
        mesh = scene.geometry[key].copy(); mesh.apply_transform(matrix)
        parts[f'part_{i:03d}'] = mesh
    if not parts: raise ValueError('EMPTY_SCENE')
    return parts


def code_identity():
    root=Path(__file__).resolve().parent
    return {str(p.relative_to(root)):sha(p) for p in sorted(root.rglob('*.py'))}

def verify_snapshot(directory, name):
    directory=Path(directory)
    record=json.loads((directory/'.snap3d_snapshot.json').read_text())
    lock=json.loads((Path(__file__).resolve().parent.parent/'upstream.lock.json').read_text())[name]
    if record['weight_revision']!=lock['weight_revision'] or record['weight_repo']!=lock['weight_repo']:
        raise ValueError('WEIGHT_REVISION_MISMATCH')
    if not record['files']:raise ValueError('EMPTY_WEIGHT_SNAPSHOT')
    for path,expected in record['files'].items():
        file=(directory/path).resolve()
        if not file.is_relative_to(directory.resolve()) or sha(file)!=expected:raise ValueError('WEIGHT_HASH_MISMATCH')
    return dict(git_commit=lock['git_commit'],weight_repo=record['weight_repo'],weight_revision=record['weight_revision'],snapshot_manifest_sha256=sha(directory/'.snap3d_snapshot.json'))
