"""Offline comparison of three saved-asset UNIONs; never changes production CSG.

Capture exact Blender operands (including local transforms/materials) before the
selected operation, then replay FAST, EXACT and Manifold in separate bounded
processes. No model API, geometry repair, tolerance changes or automatic retries.
"""
from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import os
from pathlib import Path
import runpy
import subprocess
import sys
import time

import numpy as np
import trimesh

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO))
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.utils.io import read_json, write_json
from experiments.fixed_assembly_prompt.diagnose_mesh_stages import mesh_stats, snapshot


def load_mesh(path):
    with np.load(path, allow_pickle=False) as data:
        return trimesh.Trimesh(data['vertices'], data['faces'], process=False)


def save_mesh(path, mesh):
    np.savez(path, vertices=np.asarray(mesh.vertices, dtype=np.float64),
             faces=np.asarray(mesh.faces, dtype=np.int64))


def object_mesh(obj, unit):
    obj.data.calc_loop_triangles()
    vertices = np.array([tuple(obj.matrix_world @ v.co) for v in obj.data.vertices]) * unit
    unique, inverse = np.unique(vertices, axis=0, return_inverse=True)
    faces = inverse[np.array([tuple(t.vertices) for t in obj.data.loop_triangles])]
    mesh = trimesh.Trimesh(unique, faces, process=False)
    materials = []
    for material in obj.data.materials:
        bsdf = material.node_tree.nodes.get('Principled BSDF') if material.use_nodes else None
        materials.append(dict(name=material.name, rgba=list(bsdf.inputs['Base Color'].default_value)
                             if bsdf else list(material.diffuse_color)))
    indices = [obj.data.polygons[t.polygon_index].material_index for t in obj.data.loop_triangles]
    return mesh, [materials[i] for i in indices], len(vertices)-len(unique)


def quality(mesh):
    row = mesh_stats(mesh)
    row.update(finite=bool(np.isfinite(mesh.vertices).all()), closed=bool(mesh.is_watertight),
               oriented=bool(mesh.is_winding_consistent), volume_mm3=float(mesh.volume),
               surface_components=len(mesh.split(only_watertight=False, repair=False)))
    row['valid_surface_volume'] = bool(row['finite'] and row['closed'] and row['oriented']
                                     and row['zero_area_faces'] == 0 and mesh.volume > 0)
    return row


def material_areas(mesh, materials):
    result = {}
    for area, material in zip(mesh.area_faces, materials, strict=True):
        key = str(material['rgba'])
        result[key] = result.get(key, 0.0) + float(area)
    return result


class Captured(Exception):
    pass


def capture(config, out):
    import bpy
    from adsl.core.export.export_assembly import evaluated
    config = dict(config)
    source = Path(config['source'])
    source_hash = sha256_file(source)
    assembly = runpy.run_path(str(source), run_name='__adsl_generated__')['assembly']
    assert assembly.mm_per_unit == config['mm_per_unit']
    manifest = read_json(config['manifest'])
    assert manifest['source_sha256'] == source_hash
    glb = importlib.import_module('adsl.core.export.export_glb')
    original = glb._apply_boolean
    count = 0

    def observed(base, other, operation):
        nonlocal count
        index = count
        count += 1
        base_path = base.parent.get('adsl_path') if base.parent else ''
        selected = index == config.get('operation_index') if 'operation_index' in config else (
            operation == 'UNION' and config['base_path_contains'] in base_path)
        if not selected:
            return original(base, other, operation)
        assert operation == 'UNION'
        config.update(source_sha256=source_hash, operation=operation, operation_index=index,
                      object_names=[base.name, other.name], input_records=[])
        for name, obj in [('base', base), ('operand', other)]:
            mesh, materials, duplicates = object_mesh(obj, assembly.mm_per_unit)
            save_mesh(out/f'{name}.npz', mesh)
            write_json(out/f'{name}_materials.json', materials)
            record = dict(name=name, object=obj.name,
                          semantic_path=obj.parent.get('adsl_path') if obj.parent else None,
                          matrix_world=[list(row) for row in obj.matrix_world],
                          exact_duplicate_vertices_merged=duplicates, proximity_welding=False,
                          npz_sha256=sha256_file(out/f'{name}.npz'), quality=quality(mesh))
            config['input_records'].append(record)
            if config.get('old_stage'):
                old = load_mesh(Path(config['old_stage'])/f'before_{name}'/'triangles.npz')
                assert np.array_equal(mesh.triangles, old.triangles), 'not the original operands'
        # Preserve polygon topology, parent matrices and material slots exactly.
        bpy.ops.wm.save_as_mainfile(filepath=str(out/'operands.blend'), check_existing=False)
        config['blend_sha256'] = sha256_file(out/'operands.blend')
        write_json(out/'inputs.json', config)
        raise Captured()

    glb._apply_boolean = observed
    try:
        evaluated(assembly.parts[config['part']], out/'unused.glb', assembly.mm_per_unit,
                  keep_materials=True, validate_geometry=False)
    except Captured:
        pass
    finally:
        glb._apply_boolean = original
    assert (out/'inputs.json').is_file(), 'requested Boolean operation not found'
    assert sha256_file(source) == source_hash


def backend(inputs, solver, out):
    config = read_json(inputs/'inputs.json')
    assert sha256_file(inputs/'operands.blend') == config['blend_sha256']
    meshes = [load_mesh(inputs/f'{name}.npz') for name in ('base', 'operand')]
    started = time.perf_counter()
    extra = {}
    if solver in ('FAST', 'EXACT'):
        import bpy
        bpy.ops.wm.open_mainfile(filepath=str(inputs/'operands.blend'))
        bpy.context.scene.render.threads_mode = 'FIXED'
        bpy.context.scene.render.threads = 4
        objects = [bpy.data.objects[name] for name in config['object_names']]
        for obj, expected in zip(objects, meshes, strict=True):
            actual, _, _ = object_mesh(obj, config['mm_per_unit'])
            assert np.array_equal(actual.triangles, expected.triangles), 'replay changed input'
        base, other = objects
        bpy.context.view_layer.objects.active = base
        modifier = base.modifiers.new(name='OfflineUnion', type='BOOLEAN')
        modifier.operation = 'UNION'
        modifier.solver = solver
        modifier.object = other
        extra['settings'] = {k:getattr(modifier, k) for k in
                             ('double_threshold', 'use_self', 'use_hole_tolerant', 'material_mode')}
        prepared = time.perf_counter()
        bpy.ops.object.modifier_apply(modifier=modifier.name)
        computed = time.perf_counter()
        row = snapshot(base, out/'snapshot', config['mm_per_unit'])
        mesh, materials, _ = object_mesh(base, config['mm_per_unit'])
        extra['polygon_quality'] = {k:v for k,v in row.items() if 'polygon' in k}
        if solver == 'FAST' and config.get('old_stage'):
            old = load_mesh(Path(config['old_stage'])/'after'/'triangles.npz')
            extra['matches_prior_fast_triangles'] = bool(np.array_equal(old.triangles, mesh.triangles))
            assert extra['matches_prior_fast_triangles'], 'FAST did not reproduce original operation'
    else:
        import manifold3d as mf
        from adsl.core.export.export_assembly import solid_mesh
        solids, origins, status = [], [], []
        for name, mesh in zip(('base', 'operand'), meshes, strict=True):
            ids = np.arange(len(origins), len(origins)+len(mesh.faces), dtype=np.uint64)
            origins.extend(read_json(inputs/f'{name}_materials.json'))
            solid = mf.Manifold(mf.Mesh64(np.array(mesh.vertices, dtype=np.float64, order='C'),
                                         np.array(mesh.faces, dtype=np.uint64, order='C'), face_id=ids))
            status.append(dict(name=name, status=str(solid.status()), tolerance=solid.get_tolerance()))
            write_json(out/'input_status.json', status)
            if solid.status() != mf.Error.NoError:
                raise ValueError(f'{name}: {solid.status()}')
            solids.append(solid)
        prepared = time.perf_counter()
        merged = mf.Manifold.batch_boolean(solids, mf.OpType.Add)
        status_result = str(merged.status())  # force lazy native evaluation inside timed section
        raw = merged.to_mesh64()
        computed = time.perf_counter()
        mesh = solid_mesh(merged)
        materials = [origins[int(i)] for i in raw.face_id]
        extra.update(input_status=status, output_status=status_result,
                     settings={'Mesh64':True, 'tolerance_override':None, 'repair':False})
    save_mesh(out/'mesh.npz', mesh)
    write_json(out/'materials.json', materials)
    result = dict(solver=solver, quality=quality(mesh), material_surface_area_mm2=material_areas(mesh, materials),
                  input_preparation_seconds=prepared-started, boolean_seconds=computed-prepared,
                  elapsed_seconds=time.perf_counter()-started, input_config_sha256=sha256_file(inputs/'inputs.json'),
                  **extra)
    write_json(out/'measurement.json', result)


def shape_evidence(inputs, root):
    """Independent union-occupancy witnesses, not proof of every surface/detail."""
    config = read_json(inputs/'inputs.json')
    operands = [load_mesh(inputs/f'{name}.npz') for name in ('base', 'operand')]
    bounds = np.array([np.min([m.bounds[0] for m in operands], axis=0),
                       np.max([m.bounds[1] for m in operands], axis=0)])
    # Fixed numerical exclusion bound reused from the existing topology path.
    from adsl.core.assembly_topology import length_bound
    tolerance = max(length_bound(m) for m in operands)
    rng = np.random.default_rng(20260922)
    points = rng.uniform(bounds[0]-1, bounds[1]+1, (4096, 3))
    for mesh in operands:
        points = np.vstack([points, mesh.triangles_center-mesh.face_normals*(4*tolerance),
                            mesh.triangles_center+mesh.face_normals*(4*tolerance)])
    distances = [trimesh.proximity.closest_point_naive(m, points)[1] for m in operands]
    points = points[np.minimum(*distances) > tolerance]
    expected = np.logical_or(*(m.contains(points) for m in operands))
    # Independent exact volumes for the two box unions; the control's tab root
    # is wholly inside the 120 mm diameter plate over z=[0,1], area=10*10.
    if config['case'] == 'SF02':
        assert np.all(operands[1].bounds >= operands[0].bounds[0])
        assert np.all(operands[1].bounds <= operands[0].bounds[1])
        expected_volume = float(np.prod(np.diff(operands[0].bounds, axis=0)))
        volume_basis = 'grain box entirely contained in 22*22*75 leg box'
    elif config['case'] == 'SF03':
        expected_volume = 2*12*12*98+80*10*14-2*12*10*14
        volume_basis = 'two upright boxes plus lower rail minus two 12*10*14 intersections'
    else:
        assert np.allclose(operands[0].bounds, [[-60,-60,0],[60,60,8]])
        assert np.allclose(operands[1].bounds, [[-5,-5,-6],[5,5,1]])
        expected_volume = sum(m.volume for m in operands)-100
        volume_basis = 'saved tessellated plate + saved tab - 10*10*1 embedded root'
    rows = {}
    for solver in ('FAST', 'EXACT', 'MANIFOLD'):
        path = root/'checkers'/solver/'measurement.json'
        if not path.is_file():
            rows[solver] = {'available':False}
            continue
        row = read_json(path)
        mesh = load_mesh(path.parent/'mesh.npz')
        valid = row['quality']['valid_surface_volume']
        if valid:
            away = trimesh.proximity.closest_point_naive(mesh, points)[1] > tolerance
            actual = mesh.contains(points[away])
            row['occupancy_comparison'] = dict(points=int(away.sum()),
                missing_expected=int(np.count_nonzero(expected[away] & ~actual)),
                unexpected_added=int(np.count_nonzero(~expected[away] & actual)),
                seed=20260922, boundary_exclusion_mm=tolerance,
                method='union membership in unchanged original operands, plus face-normal offset probes; sampled evidence')
        else:
            row['occupancy_comparison'] = {'status':'NOT_EVALUATED_INVALID_OUTPUT'}
        row.update(expected_volume_mm3=float(expected_volume), volume_reference=volume_basis,
                   volume_difference_mm3=float(mesh.volume-expected_volume),
                   volume_difference_reliable=valid,
                   bounds_max_difference_mm=float(np.max(np.abs(mesh.bounds-bounds))))
        if config['case'] == 'SF02' and valid:
            a = np.vstack([mesh.vertices, mesh.triangles_center])
            b = np.vstack([operands[0].vertices, operands[0].triangles_center])
            row['leg_surface_distance_mm'] = float(max(
                trimesh.proximity.closest_point_naive(operands[0], a)[1].max(),
                trimesh.proximity.closest_point_naive(mesh, b)[1].max()))
        rows[solver] = row
    write_json(root/'comparison.json', dict(case=config['case'], inputs=str(inputs), backends=rows,
        limitation='Local UNION only; no full-part re-export, critic, topology re-score or claim of material/render equivalence.'))


def run(output):
    from adsl.agents.checkers import run_checker
    from adsl.agents.models import CheckerSpec
    from adsl.agents.utils.execution import ExecutionResult
    import bpy
    output.mkdir(parents=True, exist_ok=False)
    stages = REPO/'temp/assembly_mesh_followup_20260922/stages'
    configs = []
    for case, index in [('SF02', 4), ('SF03', 5)]:
        prior = stages/case/'checkers/stage_diagnostic'
        old = read_json(prior/'stages.json')
        configs.append(dict(case=case, source=old['source'], manifest=old['manifest'], part=old['part_id'],
                            mm_per_unit=old['mm_per_unit'], operation_index=index,
                            old_stage=str(prior/f'boolean_{index:02d}')))
    normal = REPO/'temp/assembly_topology_paired_fresh_20260921T173631Z/wo/SF07/generate/rounds/round_04/candidates/03_assembly_or_appearance'
    configs.append(dict(case='SF07_control', source=str(normal/'source.py'),
                        manifest=str(normal/'asset/assembly/assembly_manifest.json'), part='top_plate',
                        mm_per_unit=1.0, base_path_contains='glossy_circular_plate'))
    allowed = sorted(os.sched_getaffinity(0))[:4]
    os.sched_setaffinity(0, allowed)
    env = dict(os.environ, OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1', MKL_NUM_THREADS='1')
    passport = dict(commit=subprocess.check_output(['git','rev-parse','HEAD'], cwd=REPO,text=True).strip(),
                    python=sys.version, blender=bpy.app.version_string,
                    packages={p:importlib.metadata.version(p) for p in ('manifold3d','trimesh','numpy')},
                    cpu_affinity=allowed, native_threads=4, blas_threads=1, timeout_per_process_seconds=120,
                    cases=configs, API_calls=0, source_edits=False, production_backend_changed=False)
    write_json(output/'passport.json', passport)
    for config in configs:
        root = output/config['case']; root.mkdir()
        write_json(root/'config.json', config)
        execution = ExecutionResult(root, root/'unused.glb', None, (), '', '')
        def invoke(name, args):
            spec = CheckerSpec(name=name, required=False, timeout_seconds=120,
                command=[sys.executable, str(Path(__file__).resolve()), *args, '--output','{output_dir}'])
            start = time.monotonic()
            result = run_checker(spec, execution=execution, source_path=Path(config['source']),
                                 round_root=root, environment=env)
            write_json(root/f'{name}_process.json', dict(status=result.result.status,
                        wall_seconds=time.monotonic()-start, result=result.result.model_dump()))
            print(config['case'], name, result.result.status, round(time.monotonic()-start,3), flush=True)
            return result.result.status == 'PASS'
        if not invoke('capture', ['--mode','capture','--config',str(root/'config.json')]):
            continue
        inputs = root/'checkers/capture'
        for solver in ('FAST','EXACT','MANIFOLD'):
            invoke(solver, ['--mode','backend','--inputs',str(inputs),'--solver',solver])
        invoke('comparison', ['--mode','comparison','--inputs',str(inputs),'--case-root',str(root)])
        assert sha256_file(Path(config['source'])) == read_json(inputs/'inputs.json')['source_sha256']


def main():
    p = argparse.ArgumentParser(__doc__)
    p.add_argument('--mode',choices=['run','capture','backend','comparison'],default='run')
    for key in ('output','config','inputs','case-root'):p.add_argument('--'+key,type=Path)
    p.add_argument('--solver',choices=['FAST','EXACT','MANIFOLD'])
    args=p.parse_args()
    if args.mode == 'run':
        run(args.output.resolve()); return
    args.output.mkdir(parents=True,exist_ok=True)
    if args.mode == 'capture':capture(read_json(args.config),args.output)
    elif args.mode == 'backend':backend(args.inputs,args.solver,args.output)
    else:shape_evidence(args.inputs,args.case_root)
    write_json(args.output/'result.json', dict(checker=args.output.name,status='PASS',
        summary='Diagnostic completed; not an asset acceptance or physical PASS'))


if __name__ == '__main__':main()
