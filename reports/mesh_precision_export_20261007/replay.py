"""Offline mesh evaluation and file-validation replay of unchanged fixtures.

No model calls, benchmark execution or FEA. Always write to a fresh directory.
"""
import argparse
import hashlib
import importlib
import json
import os
from pathlib import Path
import runpy
import shutil
import subprocess
import sys
import time

import numpy as np
import trimesh

from adsl.core import Asset, boolean_union
from adsl.core.export.mesh_validity import mesh_metrics, validate_written_mesh
from adsl.core.assembly_topology import part_measurement
from adsl.agents.utils.execution import execute_asset_source


REPO = Path(__file__).resolve().parents[2]
EXPORT = importlib.import_module('adsl.core.export.export_assembly')


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def save(path, value):
    Path(path).write_text(json.dumps(value, indent=2, default=str))


def render(path, root):
    command = [sys.executable, '-m', 'adsl.tools.render', '--glb-path', str(path),
        '--output-dir', str(root/'views'), '--width', '512', '--height', '512',
        '--view-layout', 'review_eight', '--num-camera-per-layer', '8',
        '--render-samples', '32', '--render-threads', '8', '--material-mode', 'neutral']
    started = time.monotonic()
    with (root/'render.log').open('w') as log:
        try:
            environment = dict(os.environ, ADSL_RENDER_ENGINE='CYCLES', CUDA_VISIBLE_DEVICES='')
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180, env=environment)
            images = sorted((root/'views').glob('*.png'))
            record = dict(status='PASS' if result.returncode==0 and len(images)==8 else 'FAIL',
                          returncode=result.returncode, images=[str(p) for p in images])
        except subprocess.TimeoutExpired:
            record = dict(status='NOT_EVALUATED', reason='CPU render timeout at 180 seconds')
    record.update(elapsed_seconds=time.monotonic()-started, command=command, device='CPU')
    save(root/'render.json', record)
    return record


def fixture(name, shape, root, *, render_views=False):
    root.mkdir()
    start = time.monotonic()
    mesh, solid, diag = EXPORT.evaluated(shape, root/'scene.glb', 1., keep_materials=True)
    evaluation_seconds = time.monotonic()-start
    stl = root/'canonical.stl'
    mesh.export(stl, file_type='stl_ascii')
    actual, restored, readback = validate_written_mesh(stl, expected_components=diag['canonical_mesh']['material_components'])
    part, _ = part_measurement(actual, name)
    record = dict(name=name, evaluation_seconds=evaluation_seconds,
        canonical_volume_mm3=float(solid.volume()), canonical_metrics=mesh_metrics(mesh.vertices,mesh.faces),
        internal_evaluation=diag['internal_evaluation'], display_status=diag['display_status'],
        target_precision=diag['target_precision'], stl_file_validation=readback,
        stl_volume_mm3=float(restored.volume()), part_checker=part,
        files_sha256={p.name:sha(p) for p in root.iterdir() if p.suffix in ('.stl','.glb')})
    save(root/'result.json', record)
    if render_views and diag['display_status']=='PASS':
        record['render'] = render(root/'scene.glb', root)
        save(root/'result.json', record)
    return record


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--only', choices=('fixtures','lamp','all'), default='all')
    parser.add_argument('--render', action='store_true', help='Optional CPU display preview, no model review')
    args = parser.parse_args()
    output = args.output.resolve(); output.mkdir(parents=True, exist_ok=False)
    started = time.monotonic()
    lamp_source = REPO/'reports/benchmark_six_boolean_failure_20261007/source.py'
    trunk_source = REPO/'tests/fixtures/elephant_trunk.py'
    record = dict(code_sha=subprocess.check_output(['git','rev-parse','HEAD'],cwd=REPO,text=True).strip(),
        working_diff_sha256=hashlib.sha256(subprocess.check_output(['git','diff'],cwd=REPO)).hexdigest(),
        implementation_files_sha256={str(p.relative_to(REPO)):sha(p) for p in
            list((REPO/'adsl-core/core/export').glob('*.py'))+
            [REPO/'adsl-agents/utils/asset_executor.py',REPO/'adsl-agents/utils/execution.py']},
        source_sha256={'lamp':sha(lamp_source),'trunk':sha(trunk_source)}, fixtures={})
    save(output/'provenance.json', record)
    if args.only in ('fixtures','all'):
        specs = json.loads((REPO/'reports/general_mesh_robustness_v1_20261007/minimal_operands.json').read_text())
        operands=[]
        for spec in specs:
            leaf=Asset(spec['label'])
            for primitive in spec['primitives']:
                leaf.add_primitive(primitive)
            operands.append(leaf)
        ns=runpy.run_path(str(lamp_source))
        shapes={'three_operand':boolean_union(*operands),
                'original_arm':ns['CurvedArm'](ns['UpperStructure']().offset_lamp_head),
                'original_trunk':runpy.run_path(str(trunk_source))['scene']}
        for name,shape in shapes.items():
            record['fixtures'][name]=fixture(name,shape,output/name,render_views=args.render)
            save(output/'summary.json',record)
    if args.only in ('lamp','all'):
        root=output/'original_lamp'; root.mkdir()
        copied=root/'source.py'; shutil.copy2(lamp_source,copied)
        config=json.loads((REPO/'reports/general_mesh_robustness_v1_20261007/frozen_geometry_config.json').read_text())
        save(root/'frozen_geometry_config.json',config)
        start=time.monotonic()
        try:
            execution=execute_asset_source(copied, root/'asset', render=False, export_urdf=False,
                                          timeout=120, fixed_assembly=config)
        except Exception as error:
            record['lamp']={'execution_status':'FAIL','reason':f'{type(error).__name__}: {error}',
                            'elapsed_seconds':time.monotonic()-start}
            save(output/'summary.json',record)
            raise
        (root/'execution_stdout.log').write_text(execution.stdout)
        (root/'execution_stderr.log').write_text(execution.stderr)
        manifest=json.loads((execution.output_root/'assembly/assembly_manifest.json').read_text())
        row=dict(execution_status='PASS',elapsed_seconds=time.monotonic()-start,
                 manifest=str(execution.output_root/'assembly/assembly_manifest.json'),
                 geometry_status=manifest['status'], manufacturing_status=manifest['manufacturing_status'],
                 display_status=manifest['display_status'], export_status=manifest['export_status'],
                 part_ids=[p['id'] for p in manifest['parts']],connection_ids=[c['id'] for c in manifest['connections']])
        record['lamp']=row; save(output/'summary.json',record)
        row['stl_readback']={}
        for part in manifest['parts']:
            actual, restored, readback=validate_written_mesh(execution.output_root/'assembly'/part['stl'])
            row['stl_readback'][part['id']]=dict(validation=readback,volume_mm3=float(restored.volume()),
                                                metrics=mesh_metrics(actual.vertices,actual.faces))
        if args.render and manifest['scene_glb']:
            row['render']=render(execution.output_root/'assembly'/manifest['scene_glb'],root)
        else:
            row['render']={'status':'NOT_EVALUATED','reason':'Mesh-only scope or no validated full display GLB'}
    record['elapsed_seconds']=time.monotonic()-started
    record['unchanged_sources']={name:sha(path)==record['source_sha256'][name]
                               for name,path in [('lamp',lamp_source),('trunk',trunk_source)]}
    save(output/'summary.json',record)
    print(json.dumps(record,indent=2,default=str))


if __name__=='__main__':
    main()
