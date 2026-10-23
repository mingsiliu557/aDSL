"""Offline regression of the actual frozen evaluator renderer launcher."""
import ast
import json
import os
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

from experiments.benchmark_six.gpu_render_adapter import gpu_evaluator_source

PROJECT = Path(__file__).resolve().parents[1]
HERE = PROJECT / 'experiments/benchmark_six/gpu_render_environment'


def render_function():
    source=gpu_evaluator_source((PROJECT/'experiments/benchmark_six/evaluate.py').read_text())
    function=next(n for n in ast.parse(source).body if isinstance(n,ast.FunctionDef) and n.name=='render_final')
    scope=dict(Path=Path,os=os,subprocess=subprocess,sys=sys,
        write_json=lambda p,d: p.write_text(json.dumps(d)))
    exec(compile(ast.Module(body=[function],type_ignores=[]),'<render_final>','exec'),scope)
    return scope['render_final']


def test_evaluator_keeps_allocated_gpu_and_native_cli(monkeypatch,tmp_path):
    monkeypatch.setenv('CUDA_VISIBLE_DEVICES','2')
    calls=[]
    def run(command,**kwargs):
        calls.append((command,kwargs['env']))
        directory=Path(command[command.index('--output-dir')+1])
        for index in range(1,9):(directory/f'render_{index:04d}.png').write_bytes(b'fixture')
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(subprocess,'run',run)
    result=render_function()(tmp_path/'saved.glb',tmp_path/'views')
    assert result['status']=='PASS'
    assert len(result['native_views'])==len(result['neutral_views'])==8
    assert [c[c.index('--material-mode')+1] for c,_ in calls]==['native','neutral']
    for command,env in calls:
        assert command[2]=='from adsl.tools.render import main; main()'
        assert env['CUDA_VISIBLE_DEVICES']=='2'
        assert command[command.index('--view-layout')+1]=='review_eight'
        assert command[command.index('--render-samples')+1]=='64'


def test_missing_views_are_not_reported_as_success(monkeypatch,tmp_path):
    monkeypatch.setattr(subprocess,'run',lambda *a,**k:SimpleNamespace(returncode=0))
    result=render_function()(tmp_path/'saved.glb',tmp_path/'views')
    assert result['status']=='INDETERMINATE'
    assert result['native_views']==result['neutral_views']==[]


def test_normal_import_retains_both_hooks_and_old_runpy_reproduces(tmp_path):
    for package in ['adsl','adsl/tools','bpyrenderer']:
        directory=tmp_path/package;directory.mkdir(parents=True,exist_ok=True)
        (directory/'__init__.py').write_text('')
    (tmp_path/'bpyrenderer/engine.py').write_text(
        'def init_render_engine(*a,**k): pass\noriginal_init=init_render_engine\n')
    (tmp_path/'adsl/tools/render.py').write_text('''import json,os
from bpyrenderer import engine
def render_multiview(*a,**k): pass
original_render=render_multiview
def main():
    print(json.dumps(dict(render_hook=render_multiview is not original_render,
        engine_hook=engine.init_render_engine is not engine.original_init,
        cuda=os.environ.get('CUDA_VISIBLE_DEVICES'))))
if __name__=='__main__': main()
''')
    env={**os.environ,'PYTHONPATH':str(HERE)+os.pathsep+str(tmp_path),
         'ADSL_CYCLES_BATCH_GPU':'1','CUDA_VISIBLE_DEVICES':'2'}
    old=subprocess.run([sys.executable,'-c',
        "import runpy; runpy.run_module('adsl.tools.render',run_name='__main__')"],
        cwd=tmp_path,env=env,capture_output=True,text=True)
    assert old.returncode!=0 and "'Loader' object has no attribute 'get_code'" in old.stderr
    new=subprocess.run([sys.executable,'-c','from adsl.tools.render import main; main()'],
        cwd=tmp_path,env=env,capture_output=True,text=True,check=True)
    assert json.loads(new.stdout)==dict(render_hook=True,engine_hook=True,cuda='2')
