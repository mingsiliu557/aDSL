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
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(''.join(json.dumps(r, ensure_ascii=False) + '\n' for r in values))

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

def cache_key(c, raw_sha):
    # Includes all benchmark scripts and actual imported checker/cleanup implementations.
    import adsl.agents.partition_score as score
    import adsl.agents.assembly_standing as standing
    import adsl.agents.assembly_overhang as overhang
    files = list(Path(__file__).parent.glob('*.py')) + [Path(m.__file__) for m in (score, standing, overhang)]
    files.append(Path(c['project_root'])/'adsl-core/core/export/export_glb.py')
    project=Path(c['project_root'])
    files += [project/name for name in ('adsl-agents/assembly_physics.py','adsl-agents/checkers.py','adsl-core/core/assembly_topology.py','adsl-core/core/export/export_assembly.py','adsl-core/tools/render.py')]
    if c.get('vlm',{}).get('profile'): files.append(Path(c['vlm']['profile']))
    data = dict(version=VERSION, raw_sha256=raw_sha, config=c,
                code={str(p): sha(p) for p in files})
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
