# Vendored from mingsiliu557/aDSL 9e1f01dc32812bf649dc2f58fdc027c7c3dd8d7d
# Source: adsl-core/core/assembly_topology.py; only dependency of the copied xml_model import.
import numpy as np


def mm_matrix(value, mm_per_unit):
    matrix=np.array(value,dtype=float,copy=True)
    if matrix.shape!=(4,4) or not np.isfinite(matrix).all() or not np.allclose(matrix[3],[0,0,0,1]):
        raise ValueError('invalid rigid transform')
    if not np.allclose(matrix[:3,:3].T@matrix[:3,:3],np.eye(3),atol=1e-8,rtol=0) or np.linalg.det(matrix[:3,:3])<0:
        raise ValueError('non-rigid or reflected assembly transform')
    matrix[:3,3]*=mm_per_unit
    return matrix
