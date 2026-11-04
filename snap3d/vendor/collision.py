# Vendored unchanged from mingsiliu557/aDSL f65b0dbb9c0e3a48d45f870cdb7091949aa2472a
# Source: adsl-agents/assembly_standing.py
import numpy as np


def collision_proxies(mesh, config):
    import coacd
    import trimesh
    coacd.set_log_level('warn')
    # CoACD real_metric documents metres; keep its native numerical scale.
    params=dict(config)
    forbidden={'preprocess_mode','real_metric','extrude','decimate','apx_mode'} & params.keys()
    if forbidden: raise ValueError('CoACD preprocessing/repair and approximation mode are frozen by adapter')
    params['threshold']=params.pop('threshold_mm')*.001
    values=coacd.run_coacd(coacd.Mesh(np.asarray(mesh.vertices)*.001,np.asarray(mesh.faces)),
        preprocess_mode='off',real_metric=True,extrude=False,decimate=False,apx_mode='ch',**params)
    proxies=[trimesh.Trimesh(np.asarray(v)*1000,f,process=False) for v,f in values]
    if not proxies or any(not p.is_volume for p in proxies):
        raise ValueError('COLLISION_PROXY_UNVERIFIED: non-volume convex proxy')
    return proxies
