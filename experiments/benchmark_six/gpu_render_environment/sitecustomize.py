"""Batch-only native renderer adapter. Geometry and camera routines are unchanged."""
import importlib.abc
import importlib.machinery
import json
import os
from pathlib import Path
import subprocess
import sys
import time

if os.environ.get('ADSL_CYCLES_BATCH_GPU')=='1':
    class Loader(importlib.abc.Loader):
        def __init__(self,original,name):self.original,self.name=original,name
        def create_module(self,spec):
            f=getattr(self.original,'create_module',None)
            return f(spec) if f else None
        def exec_module(self,module):
            self.original.exec_module(module)
            if self.name=='bpyrenderer.engine':
                original=module.init_render_engine
                def init_render_engine(engine,render_samples=64):
                    import bpy
                    prefs=bpy.context.preferences.addons['cycles'].preferences
                    prefs.compute_device_type='CUDA';prefs.get_devices()
                    devices=[d for d in prefs.devices if d.type=='CUDA']
                    if len(devices)!=1:raise RuntimeError('Expected exactly one allocated CUDA render device')
                    for d in prefs.devices:d.use=d in devices
                    original('CYCLES',render_samples=int(os.environ.get('ADSL_RENDER_SAMPLES','64')))
                    bpy.context.scene.cycles.device='GPU'
                module.init_render_engine=init_render_engine
            else:
                original=module.render_multiview
                def render_multiview(*args,**kwargs):
                    import bpy
                    kwargs.update(width=int(os.environ.get('ADSL_RENDER_WIDTH','512')),
                        height=int(os.environ.get('ADSL_RENDER_HEIGHT','512')),
                        render_samples=int(os.environ.get('ADSL_RENDER_SAMPLES','64')),render_threads=2)
                    visible=os.environ['CUDA_VISIBLE_DEVICES'].split(',')[0]
                    monitor=subprocess.Popen(['nvidia-smi','pmon','-i',visible,'-d','1','-s','m'],
                        stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
                    started=time.time()
                    try:return original(*args,**kwargs)
                    finally:
                        monitor.terminate();samples,_=monitor.communicate(timeout=10)
                        mine=[r for r in samples.splitlines() if len(r.split())>2 and r.split()[1]==str(os.getpid())]
                        out=Path(os.environ['ADSL_RENDER_AUDIT_DIR']);out.mkdir(parents=True,exist_ok=True)
                        scene=bpy.context.scene
                        record=dict(pid=os.getpid(),started_at=started,elapsed_seconds=time.time()-started,
                            output_dir=str(kwargs.get('output_dir',args[0] if args else '')),
                            engine=scene.render.engine,cycles_device=scene.cycles.device,
                            width=scene.render.resolution_x,height=scene.render.resolution_y,
                            samples=scene.cycles.samples,allocated_gpu=visible,
                            allocated_gpu_process_confirmed=bool(mine),gpu_process_samples=mine,
                            renderer_source=module.__file__)
                        (out/f'{os.getpid()}_{time.time_ns()}.json').write_text(json.dumps(record,indent=2)+'\n')
                module.render_multiview=render_multiview
    class Finder(importlib.abc.MetaPathFinder):
        def find_spec(self,fullname,path=None,target=None):
            if fullname not in {'bpyrenderer.engine','adsl.tools.render'}:return None
            spec=importlib.machinery.PathFinder.find_spec(fullname,path)
            if spec and spec.loader:spec.loader=Loader(spec.loader,fullname)
            return spec
    sys.meta_path.insert(0,Finder())
