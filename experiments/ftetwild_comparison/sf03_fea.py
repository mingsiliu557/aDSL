"""Conditional SF03-only C3D10 integration, never a production backend."""
import importlib.util
import json
import os
from pathlib import Path
import resource
import signal
import subprocess
import sys
import time

import numpy as np
import run as compare

FOLDER = compare.OUT / 'SF03_ours'
EDGES = np.array([[0,1], [1,2], [2,0], [0,3], [1,3], [2,3]])


def volume_of_boxes(meshes):
    for m in meshes:
        assert len(m.faces) == 12
        assert all(len(np.unique(m.vertices[:,i])) == 2 for i in range(3))
        assert np.isclose(m.volume, np.prod(m.extents), rtol=1e-10)
    axes = [np.unique(np.concatenate([m.vertices[:,i] for m in meshes])) for i in range(3)]
    points = np.stack(np.meshgrid(*[(a[1:]+a[:-1])/2 for a in axes], indexing='ij'), axis=-1).reshape(-1,3)
    weights = np.prod(np.stack(np.meshgrid(*[np.diff(a) for a in axes], indexing='ij'), axis=-1), axis=-1).ravel()
    inside = np.zeros(len(points), bool)
    for m in meshes:
        inside |= np.all(points > m.bounds[0], axis=1) & np.all(points < m.bounds[1], axis=1)
    return float(weights[inside].sum())


def quadratic_mesh(v, t):
    edges, inv = np.unique(np.sort(t[:, EDGES].reshape(-1,2), axis=1), axis=0, return_inverse=True)
    vertices = np.vstack([v, v[edges].mean(axis=1)])
    elements = np.hstack([t, inv.reshape(-1,6)+len(v)])
    # CalculiX 2.23 shape10tet.f: nodes 5..10 are edges 12,23,31,14,24,34.
    assert np.array_equal(vertices[elements[:,4:]], vertices[elements[:,:4]][:,EDGES].mean(axis=2))
    bary_grads = np.array([[-1.,-1,-1], [1,0,0], [0,1,0], [0,0,1]])
    corner_jac = np.linalg.det(np.stack([v[t[:,i]]-v[t[:,0]] for i in [1,2,3]],axis=2))
    minima = []
    # Four integration points; linear-sided quadratic mapping must have constant J.
    for i in range(4):
        bary = np.full(4, 0.1381966011250105); bary[i] = 0.5854101966249685
        gradients = np.vstack([(4*bary[:,None]-1)*bary_grads,
                               [4*(bary[a]*bary_grads[b]+bary[b]*bary_grads[a]) for a,b in EDGES]])
        jac = np.linalg.det(np.einsum('nki,kj->nij',vertices[elements],gradients))
        assert np.all(jac > 0) and np.allclose(jac, corner_jac, rtol=1e-8, atol=1e-16)
        minima.append(float(jac.min()))
    return vertices, elements, minima


def worker():
    start = time.monotonic()
    info = json.loads((FOLDER/'manifest.json').read_text())
    validation = json.loads((FOLDER/'validation.json').read_text())
    assert validation['valid'] and not validation['reasons']
    assert validation['reference_all_objects_closed_oriented']
    meshes = compare.load_meshes(info['source_glb'], info['scale_m_per_scene_unit'])
    exact_volume = volume_of_boxes(meshes)
    volume_error = abs(validation['volume_m3']-exact_volume)
    # Fixed envelope-derived conservative volume screen, not a changed physical threshold.
    volume_bound = compare.ENVELOPE_M * sum(m.area for m in meshes)
    assert volume_error <= volume_bound
    gate = dict(status='ACCEPTABLE_FOR_LIMITED_FEA', exact_input_union_volume_m3=exact_volume,
                relative_volume_change=validation['volume_m3']/exact_volume-1,
                envelope_times_input_area_m3=volume_bound,
                boundary_evidence='sampled, not a certified Hausdorff bound',
                component_count=validation['face_connected_solid_components'])
    out = FOLDER/'fea'; out.mkdir(exist_ok=False)
    compare.dump(out/'geometry_gate.json', gate)
    a = np.load(FOLDER/'tetra.npz')
    v, t, jac = quadratic_mesh(a['vertices'],a['tets'])
    np.savez(out/'c3d10.npz',vertices=v,elements=t)
    spec = importlib.util.spec_from_file_location('comparison_fea_helpers', compare.REPO/'experiments/load_bearing_structural_performance/analyze.py')
    fea = importlib.util.module_from_spec(spec); sys.modules[spec.name] = fea; spec.loader.exec_module(fea)
    config_path = compare.REPO/'experiments/standing_fea_30/configs/fea_chair_stool.json'
    cfg = json.loads(config_path.read_text())
    baseline = json.loads((compare.BATCH/'evaluation/ours/SF03/attempt_01/checkers/fea/raw/result.json').read_text())
    assert compare.sha(Path(info['source_glb'])) == baseline['input_scene_glb_sha256']
    assert cfg['material'] == baseline['material']
    assert info['scale_m_per_scene_unit'] == baseline['scale']['factor_m_per_scene_unit']
    nodes = {i+1:p for i,p in enumerate(v)}
    elements = {i+1:(row+1).tolist() for i,row in enumerate(t)}
    assert fea.mesh_invalid_report(nodes,elements,{}) is None
    height = cfg['scale']['target_m']; zmin = v[:,2].min()
    support = [i+1 for i,p in enumerate(v) if p[2] <= zmin+max(height*1e-6,1e-9)]
    assert len(support)>=3
    parts = baseline['geometry_gate']['topology']['parts']
    loads, failures = fea.resolve_regions_manifest(nodes,parts,cfg['functional_loads'],height*.04)
    assert not failures and len(loads)==len(cfg['functional_loads'])
    baseline_fine = next(row for row in baseline['mesh_levels'] if row['mesh_level']=='fine')
    mapping = dict(element_type='C3D10', straight_sided=True, nodes=len(v), elements=len(t),
                   midside_edges_1_based=(EDGES+1).tolist(), quadrature_min_jacobian_m3=jac,
                   ordering_reference='/vepfs_default/chanxueyan/lhp/lms/fea_runtime/calculix-2.23-source/shape10tet.f',
                   original_material=cfg['material'], scale=cfg['scale'], support_node_ids=support,
                   support_rule='z <= min_z + max(height*1e-6, 1e-9), all translations fixed',
                   loads=loads, baseline_fine_load_regions=baseline_fine['analyses']['functional']['load_regions'])
    compare.dump(out/'region_mapping.json',mapping)
    for load in loads:
        original = next(x for x in mapping['baseline_fine_load_regions'] if x['name']==load['name'])
        assert original['matched_geometries']==load['matched_geometries']
        assert np.allclose(original['semantic_bounds_m'],load['semantic_bounds_m'],rtol=0,atol=1e-12)
        assert original['force_n']==load['force_n']
    results = {}
    for name, selected, gravity in [('self_weight',[],True),('functional',loads,False)]:
        d=out/name; d.mkdir()
        deck=d/(name+'.inp')
        fea.write_deck(deck,nodes,elements,cfg['material'],support,selected,gravity)
        before=time.monotonic()
        # Same deck/solver, but write directly in the isolated local experiment;
        # outer process group owns the timeout and preserves partial logs/assets.
        with (d/'solver.log').open('w') as log:
            completed=subprocess.run([cfg['ccx'],name],cwd=d,stdout=log,stderr=subprocess.STDOUT,
                                     timeout=cfg['solver_timeout_seconds'],check=False)
        parsed=fea.parse_dat(d/(name+'.dat'))
        parsed.update(returncode=completed.returncode, elapsed_s=time.monotonic()-before,
                      status='SOLVED' if completed.returncode==0 and parsed['max_displacement_m'] is not None else 'SOLVER_FAILED')
        stress=parsed.get('max_von_mises_pa')
        parsed['nominal_safety_factor']=cfg['material']['yield_strength_pa']/stress if stress else None
        parsed['screening_assessment']=fea.screening_assessment(parsed,height)
        results[name]=parsed
        compare.dump(out/'solver_results.json',dict(analyses=results,elapsed_s=time.monotonic()-start,
                     physical_verification='NOT_FULLY_VERIFIED: one mesh level only; no convergence/pass-rate claim'))


if __name__=='__main__':
    if '--worker' in sys.argv:
        worker()
    else:
        # Only after the three requested volume-mesh attempts have finished.
        assert all((compare.OUT/f'{c}_{a}'/'mesh_process.json').exists() for c,a,_ in compare.CASES)
        def limit():
            resource.setrlimit(resource.RLIMIT_AS,(6*1024**3,6*1024**3))
            os.sched_setaffinity(0,sorted(os.sched_getaffinity(0))[:2])
        env=dict(os.environ,OMP_NUM_THREADS='2',OPENBLAS_NUM_THREADS='1',MKL_NUM_THREADS='1')
        start=time.monotonic()
        with (FOLDER/'fea_process.log').open('x') as log:
            p=subprocess.Popen([sys.executable,'-u',__file__,'--worker'],stdout=log,stderr=subprocess.STDOUT,
                               env=env,start_new_session=True,preexec_fn=limit)
            try:
                code=p.wait(timeout=300); status='COMPLETED' if code==0 else 'ERROR'
            except subprocess.TimeoutExpired:
                os.killpg(p.pid,signal.SIGTERM)
                try:p.wait(timeout=5)
                except subprocess.TimeoutExpired:os.killpg(p.pid,signal.SIGKILL);p.wait()
                code=p.returncode;status='TIMEOUT'
        compare.dump(FOLDER/'fea_process.json',dict(status=status,exit_code=code,elapsed_s=time.monotonic()-start,
                     timeout_s=300,cpu_count=2,address_space_gib=6))
        print(status,code,flush=True)
