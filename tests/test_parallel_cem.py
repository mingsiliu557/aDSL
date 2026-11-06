"""Ordered process-pool CEM; stub physics only, apart from tiny XML initialization."""
import copy
from pathlib import Path
import time
import xml.etree.ElementTree as ET

import numpy as np
import pytest
import yaml
from snap3d.cem import optimize,effective_workers,FAMILIES

CFG=yaml.safe_load(Path('configs/default.yaml').read_text())


def fake_simulate(job):
    # Complete out of order without changing the candidate's deterministic score.
    time.sleep(.002*(4-job['candidate_index']%4))
    i=job['candidate_index'];stable=i in job['stable_at']
    global_score=3. if i in job['regress_at'] else .5
    return dict(status='PASS',local_score=float(20-i),global_score=global_score,
                stable=stable,theta=job['theta'])


def run_cem(workers,*,stable_at=(),regress_at=(),reject=False,population=8):
    plan=[];consumed=[]
    def prepare(theta,**meta):
        plan.append(dict(meta,theta=theta.tolist()))
        if reject and meta['candidate_index'] in (2,5):return dict(status='FAIL',reason='GEOMETRY')
        return dict(status='PASS',job=dict(meta,theta=theta.tolist(),stable_at=stable_at,regress_at=regress_at))
    result=optimize([0,0,.02,.03,1,3],D=150.,config=dict(CFG['cem'],workers=workers,population=population),
        prepare=prepare,simulate=fake_simulate,on_result=lambda t,r:consumed.append(r),baseline_global=1.,seed=42)
    return result,plan,consumed


@pytest.mark.parametrize('stable_at,reject',[((),False),((),True),((3,6),False),((3,7),True)])
def test_parallel_matches_serial_sampling_results_and_adoption(stable_at,reject):
    serial,sp,sr=run_cem(1,stable_at=stable_at,reject=reject)
    parallel,pp,pr=run_cem(2,stable_at=stable_at,reject=reject)
    assert sp==pp and sr==pr and serial['best']==parallel['best']
    assert serial['early_stop']==parallel['early_stop']
    for a,b in zip(serial['history'],parallel['history']):
        for key in ('sampling_plan','draws','valid','eligible','failures','family_counts','consumed_rollouts'):
            assert a[key]==b[key]


def test_stable_early_stop_adopts_first_ordered_eligible_candidate():
    result,_,consumed=run_cem(2,stable_at=(3,6))
    assert result['early_stop']==dict(generation=1,candidate_index=3,consumed_rollouts_in_generation=3)
    assert result['best']['result']==consumed[-1] and len(consumed)==3
    assert len(result['history'])==1
    # Stable but globally worse is not eligible; keep the non-worsening rule.
    result,_,consumed=run_cem(2,stable_at=(3,6),regress_at=(3,))
    assert result['early_stop']['candidate_index']==6 and len(consumed)==6


def test_no_stable_candidate_uses_existing_local_score_ranking():
    result,_,consumed=run_cem(1)
    eligible=[r for r in consumed if r['status']=='PASS' and r['global_score']<=1]
    assert result['early_stop'] is None
    assert result['best']['result']['local_score']==min(r['local_score'] for r in eligible)
    assert len(result['history'])==2


@pytest.mark.parametrize('population,counts',[(32,[11,10,10]),(8,[3,2,2])])
def test_first_generation_family_allocation(population,counts):
    result,_,_=run_cem(1,population=population)
    assert list(result['history'][0]['family_counts'].values())==counts
    assert [row['family'] for row in result['history'][0]['sampling_plan'][1:]]==[
        FAMILIES[i%3] for i in range(population-1)]
    for row in result['history'][0]['sampling_plan'][1:]:
        alpha,beta=row['theta'][2:4]
        cap=10 if row['family']=='normal_10' else 45
        # Normal-centered families retain the original spherical-cap sampler.
        if row['family']!='as_built_45':assert np.rad2deg(np.arccos(np.cos(alpha)*np.cos(beta)))<=cap+1e-9


def test_budget_workers_and_unchanged_sampling_parameters(monkeypatch):
    assert (CFG['cem']['population'],CFG['cem']['elite'],CFG['cem']['generations'],CFG['cem']['workers'])==(8,2,2,8)
    assert CFG['cem']['max_draws']==320
    assert CFG['cem']['sigma']==[.01,.01,np.deg2rad(15),np.deg2rad(15),.002,1/256]
    assert CFG['cem']['floor']==pytest.approx([.0005,.0005,np.deg2rad(1),np.deg2rad(1),.00025,1/1024])
    monkeypatch.setattr('os.sched_getaffinity',lambda _:set(range(3)))
    assert effective_workers({})==3 and effective_workers({'workers':2})==2
    with pytest.raises(ValueError):effective_workers({'workers':0})


def test_display_removal_preserves_compiled_mass_inertia_flex_and_contacts(monkeypatch):
    import mujoco
    import trimesh
    from snap3d import rollout as module
    from snap3d.vendor.rigid_flex import xml_model
    parts={'base':trimesh.creation.box([10,10,2]),'top':trimesh.creation.box([10,10,2])}
    parts['base'].apply_translation([0,0,1]);parts['top'].apply_translation([0,0,3])
    ids=sorted(parts);bodies={}
    for k,name in enumerate(ids):
        com=parts[name].center_mass;local=parts[name].copy();local.apply_translation(-com)
        T=np.eye(4);T[:3,3]=com;bodies[f'part_{k}']=dict(mesh=local,assembly_transform_mm=T.tolist())
    config=CFG['rollout'];report=dict(connections=[dict(parameters=dict(fit_offset_mm=.1))])
    root=ET.fromstring(xml_model(report,parts,{},dict(config,timestep_seconds=config['dt']),config['density'],body_layout=dict(bodies=bodies)))
    ET.SubElement(root,'size',memory='1G')
    root.find('./worldbody/body').remove(root.find('./worldbody/body/freejoint'))
    before=ET.tostring(root,encoding='unicode')
    after,_=module._rigid_flex_xml(parts,ids,['base'],config,.1)
    models=[mujoco.MjModel.from_xml_string(xml) for xml in (before,after)]
    for field in ('body_mass','body_inertia','body_ipos','body_iquat','flex_elemnum','flex_vertbodyid','flex_vert'):
        np.testing.assert_array_equal(getattr(models[0],field),getattr(models[1],field))
    contacts=[]
    for m in models:
        d=mujoco.MjData(m);mujoco.mj_forward(m,d)
        fb={i:int(m.flex_vertbodyid[m.flex_vertadr[i]]) for i in range(m.nflex)}
        contacts.append(module._initial_contacts(m,d,fb))
    assert contacts[0]==contacts[1] and contacts[0]['contact_count']>0
    assert ET.fromstring(after).findall('./asset/mesh')==[]
    assert ET.fromstring(after).findall('./worldbody/body/geom')==[]
