"""X-Part light wrapper; records and rejects silently omitted parts."""
from pathlib import Path
import argparse,time
from .common import verify_snapshot,scene_parts,write_json,sha
from .export import export_parts

def decompose(mesh_path, *, weights, output, seed=42):
    import torch
    from partgen.partformer_pipeline import PartFormerPipeline
    if not Path(weights).is_dir():raise ValueError('LOCAL_SNAPSHOT_REQUIRED')
    identity=verify_snapshot(weights,'Hunyuan3D-Part')
    torch.manual_seed(seed)
    pipeline=PartFormerPipeline.from_pretrained(str(weights));pipeline.to(device='cuda',dtype=torch.float32)
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
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    parts=scene_parts(scene)
    metadata=dict(upstream=identity,input_sha256=sha(mesh_path),options=opts,export_attempts=attempts,upstream_fix_mesh=True,
                  elapsed_s=time.monotonic()-start,gpu=torch.cuda.get_device_name())
    export_parts(parts,output,metadata=metadata)
    if any(x['status']!='PASS' for x in attempts) or len(parts)!=len(attempts):
        raise ValueError('UPSTREAM_PART_EXPORT_INCOMPLETE')

def main():
    p=argparse.ArgumentParser();p.add_argument('--mesh',required=True);p.add_argument('--weights',required=True);p.add_argument('--output',required=True);p.add_argument('--seed',type=int,default=42);p.add_argument('--gate',required=True)
    a=p.parse_args()
    import yaml
    from .pipeline import require_gate
    require_gate(a.gate,yaml.safe_load(Path('configs/default.yaml').read_text()))
    decompose(a.mesh,weights=a.weights,output=a.output,seed=a.seed)
if __name__=='__main__':main()
