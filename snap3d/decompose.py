"""X-Part light wrapper; records omitted parts without stopping a readable export."""
from pathlib import Path
import argparse,time
from contextlib import contextmanager
import importlib
from .common import verify_snapshot,verify_auxiliary,scene_parts,write_json,sha
from .export import export_parts

@contextmanager
def pinned_sonata(path):
    """Upstream P3-SAM hardcodes a main-revision download and a root-only cache.

    Redirect its existing loader to the pinned local checkpoint; restore aliases.
    No third-party source, architecture, weights or inference parameters change.
    """
    originals=[]
    def local_loader(original):
        def load(name='sonata', *args, **kwargs):
            if name=='sonata':name=str(path)
            return original(name,*args,**kwargs)
        return load
    try:
        for name in ['models.sonata','partgen.models.sonata']:
            module=importlib.import_module(name)
            originals.append((module,module.load))
            module.load=local_loader(module.load)
        yield
    finally:
        for module,original in reversed(originals):module.load=original


def decompose(mesh_path, *, weights, output, seed=42):
    import torch
    from partgen.partformer_pipeline import PartFormerPipeline
    weights=Path(weights).resolve()
    if not weights.is_dir():raise ValueError('LOCAL_SNAPSHOT_REQUIRED')
    identity=verify_snapshot(weights,'Hunyuan3D-Part')
    torch.manual_seed(seed)
    sonata,sonata_identity=verify_auxiliary('sonata')
    with pinned_sonata(sonata):
        pipeline=PartFormerPipeline.from_pretrained(str(weights))
    pipeline.to(device='cuda',dtype=torch.float32)
    original=pipeline._export;attempts=[]
    def capture(*args,**kwargs):
        try:
            result=original(*args,**kwargs)
            if any(x is None for x in result): raise ValueError('EMPTY_EXPORTED_PART')
            attempts.append(dict(status='PASS'));return result
        except Exception as exc:
            attempts.append(dict(status='FAIL',reason=str(exc)));raise
    pipeline._export=capture
    start=time.monotonic()
    opts=dict(seed=seed,num_inference_steps=50,octree_resolution=512,output_type='trimesh',guidance_scale=-1.,dual_guidance=True,dual_guidance_scale=10.5)
    scene,_=pipeline(mesh_path=str(mesh_path),generator=torch.Generator(device='cuda').manual_seed(seed),**opts)
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    parts=scene_parts(scene)
    metadata=dict(upstream=identity,sonata_identity=sonata_identity,input_sha256=sha(mesh_path),options=opts,export_attempts=attempts,upstream_fix_mesh=True,
                  elapsed_s=time.monotonic()-start,gpu=torch.cuda.get_device_name())
    incomplete=any(x['status']!='PASS' for x in attempts) or len(parts)!=len(attempts)
    metadata['warnings']=['UPSTREAM_PART_EXPORT_INCOMPLETE'] if incomplete else []
    export_parts(parts,output,metadata=metadata)

def main():
    p=argparse.ArgumentParser();p.add_argument('--mesh',required=True);p.add_argument('--weights',required=True);p.add_argument('--output',required=True);p.add_argument('--seed',type=int,default=42)
    a=p.parse_args()
    decompose(a.mesh,weights=a.weights,output=a.output,seed=a.seed)
if __name__=='__main__':main()
