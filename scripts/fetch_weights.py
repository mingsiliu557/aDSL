"""Explicit post-gate snapshot download; never called by imports/tests."""
from pathlib import Path
import json
import yaml
from snap3d.pipeline import require_gate

def main():
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--gate',required=True);a=p.parse_args()
    require_gate(a.gate,yaml.safe_load(Path('configs/default.yaml').read_text()))
    from huggingface_hub import snapshot_download
    lock=json.loads(Path('upstream.lock.json').read_text())
    for name,row in lock.items():
        path=snapshot_download(row['weight_repo'],revision=row['weight_revision'],local_dir=str(Path('models')/name),
            ignore_patterns=['*.md','*.png','*.jpg','*.mp4','hunyuan3d-paint*/*'] if name=='Hunyuan3D-2' else None)
        from snap3d.common import sha,write_json
        directory=Path(path)
        files={str(f.relative_to(directory)):sha(f) for f in directory.rglob('*') if f.is_file() and '.cache' not in f.parts and f.name!='.snap3d_snapshot.json'}
        write_json(directory/'.snap3d_snapshot.json',dict(weight_repo=row['weight_repo'],weight_revision=row['weight_revision'],files=files))
        print(name,row['weight_revision'],path)
    # Ancillary background/Sonata checkpoint binding is a separate prerequisite;
    # do not silently download whatever an upstream default selects.
if __name__=='__main__':main()
