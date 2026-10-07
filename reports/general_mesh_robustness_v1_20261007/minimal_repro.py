"""Replay the reduced original operand data; do not change its geometry.

The expected outcome on manifold3d 3.5.2 is a valid internal solid and an
explicit TARGET_PRECISION_UNREPRESENTABLE result. This is a failure diagnostic,
not a successful GLB export or a proof of the globally smallest reproduction.
"""
from pathlib import Path
import json

import numpy as np

from adsl.core import Asset, boolean_union
from adsl.core.export.mesh64 import evaluate_shape
from adsl.core.export.mesh_validity import MeshEvaluationError, mesh_metrics, target_mesh


def main():
    specs = json.loads(Path(__file__).with_name('minimal_operands.json').read_text())
    operands = []
    for spec in specs:
        if spec['children'] or spec['joints']:
            raise ValueError('This reduced fixture contains only primitive leaves')
        shape = Asset(spec['label'])
        for primitive in spec['primitives']:
            shape.add_primitive(primitive)
        operands.append(shape)
    piece = evaluate_shape(boolean_union(*operands)).pieces[0]
    raw = piece.solid.to_mesh64()
    result = {
        'operand_count': len(operands),
        'internal_evaluation': mesh_metrics(raw.vert_properties[:, :3], raw.tri_verts),
        'volume_scene_units3': float(piece.solid.volume()),
    }
    affine = piece.transform.copy()
    affine[:3, 3] = 0
    try:
        _, _, conversion = target_mesh(piece.solid.transform(np.ascontiguousarray(affine[:3])))
        result['target_precision'] = conversion
    except MeshEvaluationError as error:
        result['target_precision'] = {'status': 'FAIL', 'diagnostic': error.diagnostic}
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
