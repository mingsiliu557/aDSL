---
license: mit
task_categories:
  - other
tags:
  - 3d
  - mesh
  - toys4k
  - ply
---

# Toys4K Meshes

Exported triangle meshes from the [Toys4K](https://github.com/rehg-lab/toys4k) dataset.

## Layout

```
{category}/{instance_id}/mesh.ply
```

- **105** object categories (e.g. `chair`, `airplane`, `car`)
- **4000** instances, one `mesh.ply` per instance

## Format

Each `mesh.ply` is a binary or ASCII PLY mesh produced from the source blend assets.

## Usage

```python
from pathlib import Path
from huggingface_hub import snapshot_download

root = Path(snapshot_download("Yang2001/toys4k_meshes", repo_type="dataset"))
mesh = root / "chair" / "chair_000" / "mesh.ply"
```

## Citation

If you use Toys4K, please cite the original Toys4K paper and dataset.
