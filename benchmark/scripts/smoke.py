"""Finite native checks for reference adapter: stable, tipping, solid voxel semantics, cache/failure isolation."""
from pathlib import Path
import argparse
import numpy as np
import trimesh
from common import config, dump, load, sha, cache_key
from preflight_candidates import measure


def fixture(c, name, mesh):
    root=Path(c['root']);derived=root/'derived'/name;derived.mkdir(parents=True,exist_ok=True)
    mesh.apply_translation(-mesh.bounds[0]);np.savez(derived/'reference_whole.body.npz',vertices=mesh.vertices,faces=mesh.faces)
    stl=derived/'reference_whole.stl';mesh.export(stl,file_type='stl_ascii')
    dump(derived/'use_pose.json',dict(normalization_mm=np.eye(4).tolist(),fixture=name))
    source=derived/'reference_source.json';dump(source,dict(fixture=name,mesh_sha256=sha(stl)))
    body=derived/'reference_whole.body.npz';ident=np.eye(4).tolist()
    dump(derived/'assembly_manifest.json',dict(root_id='reference_whole',mm_per_unit=1,source_sha256=sha(source),connections=[],
        part_declarations=[dict(id='reference_whole',components=['reference_whole'],assembly_transform=ident)],
        parts=[dict(id='reference_whole',stl=stl.name,print_transform_mm=ident,assembly_transform=ident)],files_sha256={stl.name:sha(stl)},
        partition_reference_inputs=[dict(part_id='reference_whole',status='PASS',frame='part_local_mm',npz=body.name,sha256=sha(body),source_sha256=sha(source))]))
    dump(derived/'basic.json',dict(status='PASS',standing_eligible=True,standing_limitation=None))
    # Measurement adapter only needs glb path to find the adjacent manifest.
    (derived/'reference.glb').touch()
    return dict(case_id=name,raw_sha256=sha(stl))


def run(c, config_path):
    from adsl.agents.partition_score import occupied_voxels, vertical_gap_count, best_print_pose
    cube=trimesh.creation.box((20,20,20));cube.apply_translation([10,10,10])
    cells=occupied_voxels(cube,10,[0,0,0])
    assert len(cells)==8 and vertical_gap_count(cells)==0
    posed=best_print_pose(cube,dict(voxel_pitch_mm=10))
    assert len(posed['orientations'])==24 and posed['gap_voxels']==0
    stable=fixture(c,'SMOKE_stable',cube.copy());a=measure(c,stable,config_path)
    assert a['standing']['status']=='PASS',a['standing']
    assert a['overhang']['status']=='PASS' and a['overhang']['metrics']['partition_objective']['print_part_count']==1
    tall=trimesh.creation.box((8,8,100));tall.apply_transform(trimesh.transformations.rotation_matrix(np.deg2rad(20),[0,1,0]))
    tipping=fixture(c,'SMOKE_tipping',tall);b=measure(c,tipping,config_path)
    assert b['standing']['status']=='FAIL',b['standing']
    record=Path(c['root'])/'measurements/SMOKE_stable/reference_measurement.json';before=record.stat().st_mtime_ns
    again=measure(c,stable,config_path);assert record.stat().st_mtime_ns==before
    # Config and source changes cannot reuse an old cache.
    altered={**c,'longest_extent_mm':151}
    assert cache_key(c,stable['raw_sha256'])!=cache_key(altered,stable['raw_sha256'])
    assert cache_key(c,'bad')!=cache_key(c,stable['raw_sha256'])
    from preflight_candidates import preflight
    bad=Path(c['root'])/'raw/smoke_bad.glb';bad.write_bytes(b'not a model')
    r=preflight(c,dict(case_id='SMOKE_bad',raw_mesh=str(bad.relative_to(c['root']))),config_path)
    assert r['status']=='INDETERMINATE'
    assert measure(c,stable,config_path)['standing']['status']=='PASS'
    output=dict(status='PASS',checks=7,stable=dict(status=a['standing']['status'],peak_tilt_deg=a['standing']['metrics']['peak_tilt_deg']),
        tipping=dict(status=b['standing']['status'],peak_tilt_deg=b['standing']['metrics']['peak_tilt_deg']),
        voxels=dict(occupied=len(cells),gap=0,rotations=24),cache_reused=True,bad_file_isolated=True)
    dump(Path(c['root'])/'logs/smoke.json',output);print(output)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--config',type=Path,required=True);a=p.parse_args();run(config(a.config),a.config.resolve())
