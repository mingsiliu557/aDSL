"""Independent, bounded fTetWild comparison. Never imports production checkers."""
from pathlib import Path
import argparse
import hashlib
import json
import os
import resource
import signal
import subprocess
import sys
import time

import numpy as np
import trimesh
from scipy.sparse import coo_matrix
from scipy.sparse.csgraph import connected_components

REPO = Path(__file__).resolve().parents[2]
OUT = REPO / 'local_experiment/ftetwild_20260913'
BATCH = REPO / 'local_experiment/topology_standing_fea_12_cpu_20260912T100549Z'
CASES = [('SF27', 'ours', 'fea_tower_speaker.json'),
         ('SF03', 'ours', 'fea_chair_stool.json'),
         ('SF20', 'adsl', 'fea_floor_lamp.json')]
ENVELOPE_M = 0.0001


def dump(path, value):
    path.write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def load_meshes(path, scale):
    scene = trimesh.load(path, process=False)
    meshes = []
    # glTF Y-up -> authored/FEA Z-up; proper rotation, determinant +1.
    rotation = np.array([[1, 0, 0], [0, 0, -1], [0, 1, 0]])
    for name in sorted(scene.graph.nodes_geometry):
        transform, key = scene.graph[name]
        m = scene.geometry[key].copy()
        m.apply_transform(transform)
        m.vertices = (np.asarray(m.vertices) @ rotation.T) * scale
        meshes.append(m)
    return meshes


def prepare(case, arm, config_name):
    folder = OUT / f'{case}_{arm}'
    folder.mkdir(exist_ok=False)
    asset = BATCH / 'evaluation' / arm / case / 'attempt_01/asset'
    top_path = asset.parent / 'checkers/topology/raw/result.json'
    topology = json.loads(top_path.read_text())
    config_path = REPO / 'experiments/standing_fea_30/configs' / config_name
    config = json.loads(config_path.read_text())
    factor = config['scale']['target_m'] / topology['scale']['source_scene_units']
    meshes = load_meshes(asset / 'render/scene.glb', factor)
    soup = trimesh.util.concatenate(meshes)
    # No welding, fixing normals, filling holes, simplification or Boolean preprocessing.
    soup.export(folder / 'input_m.off')
    np.savez(folder / 'input.npz', vertices=soup.vertices, faces=soup.faces)
    diagonal = float(np.linalg.norm(soup.extents))
    gaps = []
    for violation in topology['violations']:
        relation = violation.get('relation', {})
        if relation.get('contact_kind') == 'gap':
            r = dict(relation)
            ratio = factor / topology['scale_m_per_scene_unit']
            r['distance_m'] *= ratio
            r['closest_points_m'] = (np.array(r['closest_points_m']) * ratio).tolist()
            gaps.append(r)
    sources = [asset / 'render/scene.glb', asset / 'analysis_geometry.json',
               BATCH / 'workspaces' / arm / case / 'source.py', config_path, top_path]
    info = dict(case=case, arm=arm, source_glb=str(sources[0]),
                source_hashes={str(p): sha(p) for p in sources},
                scale_m_per_scene_unit=factor, target_height_m=config['scale']['target_m'],
                bounds_m=soup.bounds.tolist(), diagonal_m=diagonal,
                envelope_m=ENVELOPE_M, epsilon_relative=ENVELOPE_M / diagonal,
                edge_length_m=config['scale']['target_m'] * .04,
                edge_length_r=config['scale']['target_m'] * .04 / diagonal,
                max_its=80, stop_quality=10, max_threads=2,
                meshing_timeout_s=300, validation_timeout_s=180, address_space_gib=6,
                input_vertices=len(soup.vertices), input_triangles=len(soup.faces),
                input_mesh_objects=len(meshes), expected_OCC_components=topology['component_count'],
                input_surface_components=len(soup.split(only_watertight=False)),
                input_watertight_objects=sum(m.is_watertight for m in meshes),
                input_signed_volume_sum_m3=float(sum(m.volume for m in meshes)),
                volume_sum_warning='Overlaps counted multiply; not assembled union volume.',
                gap_evidence=gaps)
    dump(folder / 'manifest.json', info)
    return folder


def mesh(folder):
    import wildmeshing
    import meshio
    info = json.loads((folder / 'manifest.json').read_text())
    a = np.load(folder / 'input.npz')
    start = time.monotonic()
    mesher = wildmeshing.Tetrahedralizer(
        stop_quality=10, max_its=80, max_threads=2,
        epsilon=info['epsilon_relative'], edge_length_r=info['edge_length_r'],
        skip_simplify=False, coarsen=True)
    mesher.set_mesh(a['vertices'], a['faces'].astype(np.int32))
    mesher.tetrahedralize()
    output = mesher.get_tet_mesh(smooth_open_boundary=False, floodfill=False,
                               use_input_for_wn=True, manifold_surface=False,
                               correct_surface_orientation=False, all_mesh=False)
    vertices, tets = np.asarray(output[0]), np.asarray(output[1])
    np.savez(folder / 'tetra.npz', vertices=vertices, tets=tets)
    meshio.write(folder / 'tetra.vtu', meshio.Mesh(vertices, [('tetra', tets)]))
    dump(folder / 'mesh_output.json', dict(elapsed_s=time.monotonic()-start,
                                         nodes=len(vertices), tets=len(tets)))


def tet_metrics(vertices, tets):
    xyz = vertices[tets]
    jac = np.linalg.det(np.stack([xyz[:, i] - xyz[:, 0] for i in [1, 2, 3]], axis=2))
    # Report orientation as returned. No reorientation or removal to hide bad tets.
    faces = tets[:, [[1, 2, 3], [0, 3, 2], [0, 1, 3], [0, 2, 1]]].reshape(-1, 3)
    _, inv, counts = np.unique(np.sort(faces, axis=1), axis=0, return_inverse=True, return_counts=True)
    boundary = faces[counts[inv] == 1]
    owners = np.repeat(np.arange(len(tets)), 4)
    order = np.argsort(inv)
    pair = order[:-1][inv[order[:-1]] == inv[order[1:]]]
    pair2 = order[1:][inv[order[:-1]] == inv[order[1:]]]
    graph = coo_matrix((np.ones(len(pair)), (owners[pair], owners[pair2])), shape=(len(tets), len(tets)))
    n, labels = connected_components(graph, directed=False)
    scale = float(np.linalg.norm(np.ptp(vertices, axis=0)))
    tiny = np.finfo(float).eps * scale**3 * 64
    result = dict(zero_jacobian=int(np.count_nonzero(jac == 0)),
                  negative_jacobian=int(np.count_nonzero(jac < 0)),
                  nonfinite_jacobian=int(np.count_nonzero(~np.isfinite(jac))),
                  near_zero_positive_warning=int(np.count_nonzero((jac > 0) & (jac <= tiny))),
                  near_zero_warning_threshold_m3=tiny, min_jacobian_m3=float(jac.min()),
                  volume_m3=float(jac.sum()/6), absolute_volume_m3=float(np.abs(jac).sum()/6),
                  face_connected_solid_components=int(n), nonmanifold_faces=int(np.count_nonzero(counts > 2)),
                  component_volumes_m3=np.bincount(labels, weights=np.abs(jac)/6).tolist(),
                  valid=bool(len(tets) and np.all(np.isfinite(jac)) and np.all(jac > 0) and np.all(counts <= 2)))
    return result, boundary, labels


def inside_union(meshes, points):
    inside = np.zeros(len(points), dtype=bool)
    for m in meshes:
        idx = np.flatnonzero(~inside & np.all(points >= m.bounds[0], axis=1) & np.all(points <= m.bounds[1], axis=1))
        for chunk in np.array_split(idx, max(1, len(idx)//500+1)):
            if len(chunk):
                inside[chunk] |= m.contains(points[chunk])
    return inside


def proximity(mesh, points):
    values = []
    for start in range(0, len(points), 100):
        values.extend(trimesh.proximity.closest_point(mesh, points[start:start+100])[1])
    a = np.array(values)
    return dict(samples=len(a), max_m=float(a.max()), p95_m=float(np.percentile(a, 95)), mean_m=float(a.mean()))


def validate(folder):
    info = json.loads((folder / 'manifest.json').read_text())
    a = np.load(folder / 'tetra.npz'); v, t = a['vertices'], a['tets']
    result, faces, labels = tet_metrics(v, t)
    dump(folder / 'element_validation.json', result)
    np.save(folder / 'tet_component_labels.npy', labels)
    boundary = trimesh.Trimesh(v, faces, process=False)
    boundary.export(folder / 'boundary.ply')
    meshes = load_meshes(info['source_glb'], info['scale_m_per_scene_unit'])
    # glTF duplicates positions for flat normals/material seams. For reference
    # queries only, reindex EXACTLY identical positions within each object.
    # No position changes, tolerance welding, cross-object merging or gap filling.
    for m in meshes:
        unique, inverse = np.unique(m.vertices, axis=0, return_inverse=True)
        original_faces = np.array(m.faces)
        m.vertices = unique
        m.faces = inverse[original_faces]
    soup = trimesh.util.concatenate(meshes)
    rng = np.random.default_rng(20260913)
    # Sampling is evidence, NOT certified Hausdorff distance or exact union volume.
    np.random.seed(20260913)
    p, idx = trimesh.sample.sample_surface(soup, 4000)
    offset = info['diagonal_m'] * 1e-7
    all_closed = all(m.is_watertight and m.is_winding_consistent and m.volume > 0 for m in meshes)
    result['reference_all_objects_closed_oriented'] = all_closed
    result['reference_exact_reindex_closed_objects'] = sum(m.is_watertight for m in meshes)
    if all_closed:
        outside = ~inside_union(meshes, p + soup.face_normals[idx]*offset)
        p = p[outside]
        result['sampled_exposed_input_to_output'] = proximity(boundary, p)
        probes = rng.uniform(soup.bounds[0], soup.bounds[1], (30000, 3))
        contained = inside_union(meshes, probes)
        box_volume = float(np.prod(soup.extents)); fraction = float(contained.mean())
        estimate = fraction*box_volume
        uncertainty = 1.96*np.sqrt(fraction*(1-fraction)/len(probes))*box_volume
        result['input_union_volume_MC_m3'] = estimate
        result['input_union_volume_MC_95_halfwidth_m3'] = float(uncertainty)
        result['volume_relative_change_estimate'] = result['absolute_volume_m3']/estimate-1 if estimate else None
    else:
        result['reference_warning'] = 'Input has open/inverted objects; union reference is not verified.'
    q, _ = trimesh.sample.sample_surface(boundary, 4000)
    result['sampled_output_to_input_soup'] = proximity(soup, q)
    result['boundary_watertight'] = bool(boundary.is_watertight)
    result['output_bounds_m'] = boundary.bounds.tolist()
    # Locate tets immediately inside each previously measured pair of gap surfaces.
    # Nearest surface components are recorded; no assumption that surface components == solids.
    gap_checks = []
    face_owners = {}
    for ti, tet in enumerate(t):
        for face in [tet[[1,2,3]], tet[[0,3,2]], tet[[0,1,3]], tet[[0,2,1]]]:
            face_owners[tuple(sorted(face))] = ti
    for g in info['gap_evidence']:
        points = np.array(g['closest_points_m'])
        closest, distances, indices = trimesh.proximity.closest_point(boundary, points)
        components = [int(labels[face_owners[tuple(sorted(faces[i]))]]) for i in indices]
        same = components[0] == components[1]
        gap_checks.append(dict(left=g['left_entity_id'], right=g['right_entity_id'],
                               input_gap_m=g['distance_m'], nearest_boundary_distance_m=distances.tolist(),
                               projected_gap_m=float(np.linalg.norm(closest[1]-closest[0])),
                               output_solid_component_ids=components, same_output_component=same,
                               mapping_reliable=bool(np.max(distances) <= ENVELOPE_M)))
    result['gap_checks'] = gap_checks
    result['geometry_acceptance'] = 'UNVERIFIED'
    reasons = []
    if not result['valid']: reasons.append('invalid tetrahedra or nonmanifold faces')
    if result['face_connected_solid_components'] != info['expected_OCC_components']:
        reasons.append('solid component count differs from OCC baseline (representation mismatch possible)')
    if result['sampled_output_to_input_soup']['max_m'] > ENVELOPE_M:
        reasons.append('sampled output boundary exceeds fixed envelope')
    if all_closed and result['sampled_exposed_input_to_output']['max_m'] > ENVELOPE_M:
        reasons.append('sampled exposed input boundary exceeds fixed envelope')
    if any(g['mapping_reliable'] and g['same_output_component'] for g in gap_checks):
        reasons.append('previously disconnected gap pair maps into same output solid component')
    if reasons: result['geometry_acceptance'] = 'REJECTED_OR_REQUIRES_REVIEW'
    result['reasons'] = reasons
    result['fea'] = 'NOT_RUN: geometry/connectivity acceptance must be established first; C3D4 cannot substitute for original C3D10.'
    result['source_hashes_unchanged'] = all(sha(Path(p)) == h for p,h in info['source_hashes'].items())
    dump(folder / 'validation.json', result)


def bounded(folder, phase, seconds):
    def limits():
        resource.setrlimit(resource.RLIMIT_AS, (6*1024**3, 6*1024**3))
        os.sched_setaffinity(0, sorted(os.sched_getaffinity(0))[:2])
    env = dict(os.environ, OMP_NUM_THREADS='2', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    command = [sys.executable, '-u', str(Path(__file__).resolve()), '--phase', phase, '--folder', str(folder)]
    start = time.monotonic()
    with (folder / f'{phase}.log').open('x') as log:
        p = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT, env=env,
                             start_new_session=True, preexec_fn=limits)
        try:
            code = p.wait(timeout=seconds)
            status = 'COMPLETED' if code == 0 else 'ERROR'
        except subprocess.TimeoutExpired:
            print(f'{folder.name}/{phase}: hard timeout; terminating process group', flush=True)
            os.killpg(p.pid, signal.SIGTERM)
            try: p.wait(timeout=5)
            except subprocess.TimeoutExpired: os.killpg(p.pid, signal.SIGKILL); p.wait()
            code, status = p.returncode, 'TIMEOUT'
    record = dict(command=command, status=status, exit_code=code, elapsed_s=time.monotonic()-start,
                  timeout_s=seconds, address_space_gib=6, cpu_count=2)
    dump(folder / f'{phase}_process.json', record)
    print(folder.name, phase, record['status'], round(record['elapsed_s'], 2), flush=True)
    return code == 0


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--phase', choices=['mesh','validate'])
    parser.add_argument('--folder', type=Path)
    args = parser.parse_args()
    if args.phase:
        {'mesh': mesh, 'validate': validate}[args.phase](args.folder)
    else:
        for case, arm, config in CASES:
            folder = prepare(case, arm, config)
            print('START', folder.name, flush=True)
            if bounded(folder, 'mesh', 300):
                bounded(folder, 'validate', 180)
