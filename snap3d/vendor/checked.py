# Vendored unchanged from mingsiliu557/aDSL f65b0dbb9c0e3a48d45f870cdb7091949aa2472a
# Source: adsl-core/core/assembly_topology.py
import manifold3d as mf


def checked(solid):
    if solid.status() != mf.Error.NoError:
        raise ValueError(f'Manifold query failed: {solid.status()}')
    return solid
