"""Run in the Hunyuan3D-2 environment; never imports aDSL."""
from pathlib import Path
import argparse,json,sys,time
from .common import verify_snapshot,sha,write_json

def generate(image, *, weights, output, seed=42):
    import torch
    from PIL import Image
    from hy3dgen.shapegen import Hunyuan3DDiTFlowMatchingPipeline
    from hy3dgen.rembg import BackgroundRemover
    weights=Path(weights)
    if not weights.is_dir(): raise ValueError('LOCAL_SNAPSHOT_REQUIRED')
    identity=verify_snapshot(weights,'Hunyuan3D-2')
    torch.manual_seed(seed)
    source=Image.open(image).convert('RGBA')
    # Fixed upstream background remover; its checkpoint must also be local/pinned.
    import os
    rembg_home=Path(os.environ.get('U2NET_HOME','models/rembg'))
    if not (rembg_home/'u2net.onnx').is_file(): raise ValueError('PINNED_BACKGROUND_MODEL_REQUIRED')
    os.environ['U2NET_HOME']=str(rembg_home.resolve())
    source=BackgroundRemover()(source)
    output=Path(output);output.mkdir(parents=True,exist_ok=False)
    source.save(output/'preprocessed.png');start=time.monotonic()
    pipeline=Hunyuan3DDiTFlowMatchingPipeline.from_pretrained(str(weights),subfolder='hunyuan3d-dit-v2-0',variant='fp16',use_safetensors=True)
    opts=dict(num_inference_steps=50,guidance_scale=5.5,octree_resolution=256)
    meshes=pipeline(image=source,generator=torch.Generator(device='cuda').manual_seed(seed),**opts)
    if len(meshes)!=1: raise ValueError('EXPECTED_ONE_WHOLE_MESH')
    mesh=meshes[0];mesh.export(output/'whole.ply');mesh.export(output/'whole.glb')
    write_json(output/'generation.json',dict(upstream=identity,seed=seed,input_sha256=sha(image),preprocessed_sha256=sha(output/'preprocessed.png'),
        background_model_sha256=sha(rembg_home/'u2net.onnx'),whole_sha256=sha(output/'whole.ply'),options=opts,elapsed_s=time.monotonic()-start,gpu=torch.cuda.get_device_name()))

def main():
    p=argparse.ArgumentParser();p.add_argument('--image',required=True);p.add_argument('--weights',required=True);p.add_argument('--output',required=True);p.add_argument('--seed',type=int,default=42);p.add_argument('--gate',required=True)
    a=p.parse_args()
    import yaml
    from .pipeline import require_gate
    require_gate(a.gate,yaml.safe_load(Path('configs/default.yaml').read_text()))
    generate(a.image,weights=a.weights,output=a.output,seed=a.seed)
if __name__=='__main__':main()
