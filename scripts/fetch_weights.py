"""Explicit pinned snapshot download; never called by imports/tests."""
from pathlib import Path
import json

def main():
    import argparse
    p=argparse.ArgumentParser();p.parse_args()
    from huggingface_hub import snapshot_download
    lock=json.loads(Path('upstream.lock.json').read_text())
    for name,row in lock.items():
        patterns=(['hunyuan3d-dit-v2-0/config.yaml','hunyuan3d-dit-v2-0/model.fp16.safetensors',
                   'config.json','LICENSE','NOTICE'] if name=='Hunyuan3D-2' else
                  ['config.json','model/*','conditioner/*','shapevae/*','p3sam/*','scheduler/*','LICENSE.txt','NOTICE.txt'])
        path=snapshot_download(row['weight_repo'],revision=row['weight_revision'],
            local_dir=str(Path('models')/name),allow_patterns=patterns,max_workers=4)
        from snap3d.common import sha,write_json
        directory=Path(path)
        files={str(f.relative_to(directory)):sha(f) for f in directory.rglob('*') if f.is_file() and '.cache' not in f.parts and f.name!='.snap3d_snapshot.json'}
        write_json(directory/'.snap3d_snapshot.json',dict(weight_repo=row['weight_repo'],weight_revision=row['weight_revision'],files=files))
        print(name,row['weight_revision'],path)
    # The ancillary revision is resolved once during preparation and committed.
    # Subsequent invocations never follow an upstream main/default checkpoint.
    auxiliary=json.loads(Path('auxiliary.lock.json').read_text())
    row=auxiliary['sonata']
    from huggingface_hub import hf_hub_download
    hf_hub_download(row['weight_repo'],filename=row['filename'],revision=row['weight_revision'],
        local_dir='models/sonata')
    import pooch
    row=auxiliary['u2net']
    pooch.retrieve(row['url'],known_hash='sha256:'+row['sha256'],path='models/rembg',fname='u2net.onnx')
    from snap3d.common import verify_auxiliary
    verify_auxiliary('sonata');verify_auxiliary('u2net')
if __name__=='__main__':main()
