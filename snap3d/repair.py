"""Close open surfaces with standard mesh tools; record the intervention."""
import numpy as np
import trimesh


def repair(mesh, *, D, config):
    result = mesh.copy()
    record = dict(repaired=False, original_volume_mm3=float(mesh.volume) if mesh.is_volume else None,
                  original_watertight=bool(mesh.is_watertight), methods=[])
    if mesh.is_watertight:
        return result, record
    original_area = float(mesh.area)
    trimesh.repair.fill_holes(result)
    record['methods'].append('trimesh.fill_holes')
    if not result.is_watertight:
        import pymeshfix
        tool = pymeshfix.MeshFix(result.vertices, result.faces)
        tool.repair(joincomp=False, remove_smallest_components=False)
        result = trimesh.Trimesh(tool.points, tool.faces, process=False)
        record['methods'].append('pymeshfix.repair')
    if not np.isfinite(result.vertices).all():
        raise ValueError('NONFINITE_REPAIRED_MESH')
    if not result.is_watertight:
        raise ValueError('REPAIR_NOT_CLOSED')
    record.update(repaired=True, final_watertight=True,
                  final_volume_mm3=float(result.volume),
                  area_change_mm2=float(result.area) - original_area)
    return result, record
