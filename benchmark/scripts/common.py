"""Small filesystem/process adapters; production workflow remains unchanged."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
from datetime import datetime, timezone
from urllib.request import urlopen

VERSION = 1

def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1 << 20), b''):
            h.update(chunk)
    return h.hexdigest()

def dump(path, data):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(data, indent=2, ensure_ascii=False, default=str) + '\n')
    temp.replace(path)

def load(path): return json.loads(Path(path).read_text())
def now(): return datetime.now(timezone.utc).isoformat()
def config(path):
    c = load(path)
    c['root'] = str(Path(c['data_root']).expanduser() / 'selection_v1')
    return c

def rows(path):
    return [json.loads(line) for line in Path(path).read_text().splitlines() if line.strip()]

def write_rows(path, values):
    path=Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in values))
    temp.replace(path)

def download(url, path):
    path = Path(path)
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix(path.suffix + '.part')
        with urlopen(url, timeout=120) as response, temp.open('wb') as stream:
            while data := response.read(1 << 20): stream.write(data)
        temp.replace(path)
    return dict(url=url, path=str(path), sha256=sha(path), size_bytes=path.stat().st_size)

def cpu_env(c):
    env = dict(os.environ, CUDA_VISIBLE_DEVICES='', HIP_VISIBLE_DEVICES='', ROCR_VISIBLE_DEVICES='',
               OMP_NUM_THREADS='4', OPENBLAS_NUM_THREADS='1', LIBGL_ALWAYS_SOFTWARE='1',
               ADSL_RENDER_ENGINE='CYCLES', PYTHONDONTWRITEBYTECODE='1')
    env.pop('ADSL_GPU_RENDER_QUEUE', None)
    extra = c.get('mujoco_python_path')
    if extra: env['PYTHONPATH'] = extra + os.pathsep + env.get('PYTHONPATH', '')
    return env

def process(command, folder, timeout, c):
    from adsl.agents.utils.execution import _stop_process_group
    folder = Path(folder); folder.mkdir(parents=True, exist_ok=True)
    dump(folder/'command.json', command)
    start = __import__('time').monotonic()
    with (folder/'stdout.log').open('w') as out, (folder/'stderr.log').open('w') as err:
        p = subprocess.Popen(command, stdout=out, stderr=err, env=cpu_env(c), start_new_session=True)
        try:
            code = p.wait(timeout=timeout)
            result = dict(status='PASS' if code == 0 else 'ERROR', returncode=code)
        except subprocess.TimeoutExpired:
            _stop_process_group(p)
            result = dict(status='INDETERMINATE', reason='TIMEOUT')
        except BaseException:
            _stop_process_group(p)
            raise
    result['elapsed_seconds'] = __import__('time').monotonic()-start
    dump(folder/'process.json', result)
    return result

def valid_files(mapping, root=None):
    if not mapping: return False
    try:
        return all((Path(root)/p if root and not Path(p).is_absolute() else Path(p)).is_file()
                   and sha(Path(root)/p if root and not Path(p).is_absolute() else p)==h
                   for p,h in mapping.items())
    except OSError: return False


def stage_code(c, stage):
    """Hash source without importing bpy or native geometry in the parent."""
    project=Path(c.get('project_root',Path(__file__).parents[2])); code={}
    paths=['adsl-core/core/export/export_glb.py','adsl-core/core/assembly_topology.py',
           'adsl-core/core/export/export_assembly.py']
    if stage=='preflight':
        code['benchmark/scripts/reference_worker.py']=sha(Path(__file__).with_name('reference_worker.py'))
        paths+=['adsl-core/tools/render.py']
    elif stage=='measurement':
        paths+=['adsl-agents/'+name+'.py' for name in
                ('partition_score','assembly_standing','assembly_overhang','assembly_physics','checkers')]
    elif stage=='label': return {}
    else: raise ValueError(f'unknown cache stage: {stage}')
    for name in paths:
        path=project/name
        if path.is_file(): code[name]=sha(path)
    return code


def native_versions(c, stage):
    import importlib.metadata
    names=('numpy','trimesh','manifold3d')+ (('bpy','Pillow') if stage=='preflight' else ())
    versions={}
    for name in names:
        try: versions[name]=importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError: versions[name]='UNAVAILABLE'
    if stage=='measurement':
        extra=Path(c.get('mujoco_python_path','/nonexistent'))
        metadata=next(iter(extra.glob('mujoco-*.dist-info/METADATA')),None)
        if metadata:
            versions['mujoco']=next((s.split(': ',1)[1] for s in metadata.read_text().splitlines() if s.startswith('Version: ')),'UNKNOWN')
        else:
            try: versions['mujoco']=importlib.metadata.version('mujoco')
            except importlib.metadata.PackageNotFoundError: versions['mujoco']='UNAVAILABLE'
    return versions


def cache_descriptor(c, raw_sha=None, *, stage='preflight', inputs=None):
    if stage=='preflight':
        settings=dict(longest_extent_mm=c.get('longest_extent_mm',150),
                      render={k:c.get('render',{}).get(k) for k in ('width','height','samples','threads')},
                      pose_policy='source_up_ground_z_v1',view_layout='review_eight',input_view=2)
    elif stage=='measurement': settings=dict(physics=c.get('physics',{}))
    elif stage=='label': settings={}
    else: raise ValueError(f'unknown cache stage: {stage}')
    return dict(schema_version=2,stage=stage,raw_sha256=raw_sha,settings=settings,
                inputs=inputs or {},code=stage_code(c,stage),
                dependency_versions=native_versions(c,stage) if stage!='label' else {})


def cache_key(c, raw_sha=None, *, stage='preflight', inputs=None):
    return hashlib.sha256(json.dumps(cache_descriptor(c,raw_sha,stage=stage,inputs=inputs),sort_keys=True).encode()).hexdigest()
