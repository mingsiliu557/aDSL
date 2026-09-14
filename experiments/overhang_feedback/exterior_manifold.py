"""Detector-copy union; caller owns the 300-second subprocess budget.

No welding, repair, tolerance adjustment, or automatic backend fallback.
"""
from __future__ import annotations

import importlib.metadata
import json
from pathlib import Path
import sys

import numpy as np


def merge(payload: list[dict], diagnostics: Path) -> dict:
    import manifold3d as mf

    if not payload:
        raise ValueError("empty exterior input")
    objects, statuses = [], []
    for i, row in enumerate(payload):
        obj = mf.Manifold(mf.Mesh64(
            np.ascontiguousarray(row["vertices"], dtype=np.float64),
            np.ascontiguousarray(row["faces"], dtype=np.uint64)))
        status = obj.status()
        statuses.append({"operand": i, "status": str(status),
                         "tolerance_mm": obj.get_tolerance(), "welding_applied": False})
        diagnostics.write_text(json.dumps(statuses, indent=2))
        if status != mf.Error.NoError:
            raise ValueError(f"Manifold rejected operand {i}: {status}; see {diagnostics.name}")
        objects.append(obj)
    result = mf.Manifold.batch_boolean(objects, mf.OpType.Add)
    if result.status() != mf.Error.NoError:
        raise ValueError(f"Manifold union failed: {result.status()}")
    output = result.to_mesh64()
    return {"vertices": np.asarray(output.vert_properties)[:, :3].tolist(),
            "faces": np.asarray(output.tri_verts).tolist(),
            "manifold_version": importlib.metadata.version("manifold3d"),
            "tolerance_mm": result.get_tolerance(), "backend": "manifold_union"}


if __name__ == "__main__":
    target = Path(sys.argv[2])
    result = merge(json.loads(Path(sys.argv[1]).read_text()),
                   target.with_name("exterior_input_acceptance.json"))
    target.write_text(json.dumps(result))
