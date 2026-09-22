"""Focused adapter tests: real surface geometry; mocked controller/API where marked."""
from dataclasses import asdict,replace
import json
from pathlib import Path
from types import SimpleNamespace
import numpy as np
import pytest
import manifold3d as mf
from adsl.core.assembly import FixedAssembly,TabSlot
from adsl.core import Cube
from adsl.core.assembly_topology import solid_mesh,query_solids,read_print_mesh
from adsl.agents.assembly_physics import area_comparison,orientation_only,checker_spec,run_assembly_checks
from adsl.agents.assembly_overhang import measure_part
from adsl.agents.feedback_schema import sha256_file
from adsl.agents.utils.io import write_json

OVERHANG={'overhang_threshold_from_horizontal_deg':45.,'layer_height_mm':.2,'orientation_editable':True}
STANDING={'duration_seconds':5.,'timestep_seconds':.002,'friction':[.5,.005,.0001],
    'tilt_threshold_deg':25.,'settle_window_seconds':.5,'settle_linear_m_s':.001,
    'settle_angular_rad_s':.01,'coacd':{'threshold_mm':.02,'resolution':2000,'mcts_iterations':30,
        'mcts_nodes':20,'mcts_max_depth':3,'merge':True,'seed':0}}
MATERIAL={'youngs_modulus_pa':3e9,'poisson_ratio':.35,'density_kg_m3':1240.}


def fixture(root,*,pair=True,inverted=False):
    root.mkdir(parents=True,exist_ok=True)
    source=root/'source.py';source.write_text('# geometric fixture, never an agent answer\n')
    p=TabSlot(12,6,6,7,.2);tab,slot=query_solids(p)
    # Receiver on the floor, tab child descends into upward-open slot.
    solids={'base':mf.Manifold.cube((60,30,12),True).translate((0,0,6))-slot,
            'stem':mf.Manifold.cube((20,12,30),True).translate((0,0,-15))+tab}
    transform=np.eye(4);transform[1,1]=transform[2,2]=-1;transform[2,3]=12
    if inverted:
        # Receiver stands on two posts; the hanging stem has 10 mm of fall
        # before the floor, enough to leave the 6-mm socket. No body is welded.
        for x in (-25,25):
            solids['base']+=mf.Manifold.cube((10,30,40),True).translate((x,0,-20))
        transform=np.eye(4);transform[2,3]=40
    c=dict(id='joint',tab_part='stem',slot_part='base',tab_port='tab',slot_port='slot',parameter_name='joint',
           parameters=asdict(p),tab_frame=np.eye(4).tolist(),slot_frame=np.eye(4).tolist())
    if not pair:solids={'base':mf.Manifold.cube((60,30,12),True)}
    parts=[]
    for name,s in solids.items():
        mesh=solid_mesh(s);print_tf=np.eye(4);print_tf[2,3]=-mesh.bounds[0,2]
        mesh.apply_transform(print_tf);mesh.export(root/f'{name}.stl',file_type='stl_ascii')
        parts.append(dict(id=name,stl=f'{name}.stl',components=[name],print_transform_mm=print_tf.tolist(),assembly_transform=transform.tolist()))
    report=dict(source_sha256=sha256_file(source),root_id='base',mm_per_unit=1.,parts=parts,
        part_declarations=parts,connections=[c] if pair else [],
        files_sha256={p.name:sha256_file(p) for p in root.glob('*.stl')})
    write_json(root/'assembly_manifest.json',report)
    out=root/'results';out.mkdir(exist_ok=True)
    return SimpleNamespace(source=source,manifest=root/'assembly_manifest.json',output=out,
                           source_index=None,topology_cache=None),report


def test_print_rotation_inverse_and_use_pose_unchanged(tmp_path):
    a=FixedAssembly(root_id='part',mm_per_unit=2)
    a.add_part('part',Cube((1,2,3)),components=('body',))
    before=a.transforms['part'].copy()
    mesh=solid_mesh(mf.Manifold.cube((10,20,30),True))
    a.set_print_orientation('part',rotation_deg=(90,0,0))
    rotation=a.print_rotation('part');rotation[2,3]=-(mesh.vertices@rotation[:3,:3].T)[:,2].min()
    printed=mesh.copy();printed.apply_transform(rotation);printed.export(tmp_path/'part.stl',file_type='stl_ascii')
    read=read_print_mesh(tmp_path/'part.stl',rotation)
    assert np.array_equal(before,a.transforms['part'])
    assert np.allclose(read.triangles,mesh.triangles,rtol=0,atol=1e-12)
    assert printed.bounds[0,2]==pytest.approx(0)
    with pytest.raises(ValueError):a.set_print_orientation('missing',rotation_deg=(0,0,0))
    with pytest.raises(ValueError):a.set_print_orientation('part',rotation_deg=(float('nan'),0,0))


def test_l_part_real_overhang_reduces_without_rescaling():
    mesh=solid_mesh(mf.Manifold.cube((6,10,30),True)+mf.Manifold.cube((30,10,6),True).translate((12,0,12)))
    tf=np.eye(4);tf[2,3]=-mesh.bounds[0,2]
    first,_=measure_part(mesh,tf,OVERHANG)
    tf=np.array([[1,0,0,0],[0,0,-1,0],[0,1,0,5],[0,0,0,1.]])
    second,_=measure_part(mesh,tf,OVERHANG)
    assert first['area_mm2']>100 and second['area_mm2']==0
    assert first['area_mm2']>first['uncertainty']['bound_mm2']+second['uncertainty']['bound_mm2']


def test_comparison_missing_data_never_zero_and_ast_scope():
    assert area_comparison({'status':'PASS'},{'status':'PASS'})['conclusion']=='NOT_EVALUATED'
    before='assembly.add_part("p", body, components=("body",))\n'
    assert orientation_only(before,before+'assembly.set_print_orientation("p", rotation_deg=(90,0,0))\n')
    assert not orientation_only(before,before.replace('body,','changed,')+'assembly.set_print_orientation("p", rotation_deg=(90,0,0))\n')


def test_real_overhang_adapter_and_missing_part(tmp_path):
    from adsl.agents.assembly_overhang import analyze
    args,report=fixture(tmp_path,pair=False)
    measured=analyze(args,{'overhang':OVERHANG})
    assert measured.status=='PASS' and measured.metrics['total_overhang_mm2']==0
    (tmp_path/'base.stl').unlink()
    measured=analyze(args,{'overhang':OVERHANG})
    assert measured.status=='INDETERMINATE' and measured.metrics['total_overhang_mm2'] is None


def test_timeout_isolated_and_all_tools_run(tmp_path,monkeypatch):
    from adsl.agents import assembly_physics as module
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerResult
    from adsl.agents.utils.execution import ExecutionResult
    source=tmp_path/'source.py';source.write_text('unchanged')
    calls=[]
    def run(spec,**kw):
        calls.append(spec.name);out=tmp_path/spec.name;out.mkdir()
        result=CheckerResult(checker=spec.name,status='ERROR' if len(calls)==1 else 'PASS',summary='mock',
            violations=[{'code':'CHECKER_TIMEOUT'}] if len(calls)==1 else [])
        if len(calls)==1:(out/'stage.json').write_text('{ interrupted write')
        return CheckerRun(spec,result,out,())
    monkeypatch.setattr(module,'run_checker',run)
    specs=[checker_spec(n) for n in ('assembly_standing','assembly_overhang','assembly_fea')]
    runs=run_assembly_checks(specs,execution=ExecutionResult(tmp_path,tmp_path/'scene.glb',None,(),'',''),
        source=source,root=tmp_path,physics={})
    assert calls==[s.name for s in specs]
    assert [r.result.status for r in runs]==['INDETERMINATE','PASS','PASS']
    assert runs[0].result.metrics['last_stage']['report_unavailable']=='JSONDecodeError'


def test_fea_region_normal_is_outward_not_unsigned():
    from experiments.load_bearing_structural_performance.assembly import boundary_faces,select_region
    nodes={1:np.array([0.,0.,0.]),2:np.array([1.,0.,0.]),3:np.array([0.,1.,0.]),4:np.array([0.,0.,1.])}
    for nid,(a,b) in enumerate(((1,2),(2,3),(3,1),(1,4),(2,4),(3,4)),5):nodes[nid]=(nodes[a]+nodes[b])/2
    mapping={'p':dict(faces=boundary_faces(nodes,{1:list(range(1,11))}),components=[],assembly_transform_m=np.eye(4).tolist())}
    spec=dict(part_id='p',frame='part_local',bounds_mm=[[-1,-1,-1],[1001,1001,1]],normal=[0,0,-1])
    assert len(select_region(spec,nodes,mapping))==1
    with pytest.raises(ValueError,match='REGION_UNAVAILABLE'):
        select_region(dict(spec,normal=[0,0,1]),nodes,mapping)


def test_solver_fields_do_not_confuse_reaction_with_displacement(tmp_path):
    from experiments.load_bearing_structural_performance.assembly import result_fields
    path=tmp_path/'fields.dat'
    path.write_text('displacements (vx,vy,vz) for set NALL and time 0.1000000E+01\n'
        '1 2.1E-05 0.0 0.0\nforces (fx,fy,fz) for set NALL and time 0.1000000E+01\n'
        '1 10.0 0.0 0.0\nstresses (elem, integ.pnt.,sxx,syy,szz,sxy,sxz,syz)\n'
        '1 1 100.0 0.0 0.0 0.0 0.0 0.0\n')
    u,rf,stress=result_fields(path)
    assert np.linalg.norm(u[1])==pytest.approx(2.1e-5)
    assert rf[1][0]==10 and stress[1]==100


def test_ccx_numeric_fields_and_lossless_surface_transport(tmp_path):
    from experiments.load_bearing_structural_performance.assembly import write_assembly_deck
    from adsl.agents.assembly_topology import _save_solid_mesh,_load_solid_mesh
    # Old .17g output would exceed CalculiX's 20-character free-field limit.
    nodes={1:np.array([-1.2345678901234567e-7,2.3456789012345678e-9,3.4567890123456789e-11])}
    path=tmp_path/'format.inp'
    write_assembly_deck(path,nodes,{}, {},MATERIAL,[],[],[],False)
    values=path.read_text().split('*NODE\n')[1].splitlines()[0].split(',')[1:]
    assert all(len(v.strip())<=20 for v in values)
    assert np.allclose(np.array(values,dtype=float),nodes[1],rtol=5e-12,atol=0)
    mesh=solid_mesh(mf.Manifold.cube((60,30,12),True))
    vertices=np.array(mesh.vertices,copy=True);vertices[0,0]+=1e-11;mesh.vertices=vertices
    _save_solid_mesh(mesh,tmp_path/'solid.npz')
    copy=_load_solid_mesh(tmp_path/'solid.npz')
    assert copy.vertices.dtype==np.float64
    assert np.array_equal(mesh.vertices,copy.vertices) and np.array_equal(mesh.faces,copy.faces)


def test_tie_postcheck_preserves_si_results_and_gap(tmp_path):
    from experiments.load_bearing_structural_performance.assembly import transfer_check
    # One straight quadratic face and one node across a 0.2-mm gap. Nonlinear
    # nodal displacement requires quadratic, not subtriangle-linear, mapping.
    xyz=np.array([[0,0,0],[.001,0,0],[0,.001,0],[.0005,0,0],[.0005,.0005,0],[0,.0005,0]])
    nodes={i+1:p for i,p in enumerate(xyz)};nodes[7]=np.array([.0002,.0003,.0002])
    u={i:np.array([p[0]**2,0,0]) for i,p in nodes.items()};rf={i:np.zeros(3) for i in nodes}
    regions={'nodal_forces':{},'support_node_dofs':[], 'ties':[dict(connection_id='joint',slave_nodes=[7],
        master_faces=[{'nodes':list(range(1,7))}],position_tolerance_m=.000200001)]}
    result=transfer_check(nodes,{}, {},regions,MATERIAL,False,u,rf)
    assert result['interfaces_compatible'] and result['reaction_balanced']
    assert result['interface_compatibility'][0]['max_projection_distance_m']==pytest.approx(.0002)


def test_multitool_feedback_one_repair_despite_image_rejection_and_timeout(tmp_path,monkeypatch):
    from test_fixed_assembly import mock_flow,run_flow
    from adsl.agents import assembly_physics,assembly_topology
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerResult,EngineeringCriticDecision,RepairProposal,RepairTarget
    f=mock_flow(tmp_path,monkeypatch,[('PASS',False),('PASS',True)])
    specs=tuple(checker_spec(n) for n in ('assembly_standing','assembly_overhang','assembly_fea'))
    f=(f[0],replace(f[1],checker_specs=specs,max_rounds=2),*f[2:]);calls=[]
    def checks(specs,*,execution,source,root,**kw):
        first='round_01' in str(root);runs=[]
        for spec in specs:
            calls.append(spec.name);out=root/'checkers'/spec.name;out.mkdir(parents=True)
            status=('FAIL' if first else 'PASS') if spec.name=='assembly_standing' else ('INDETERMINATE' if spec.name=='assembly_fea' else 'PASS')
            findings=[assembly_physics.finding(spec.name,'SELF_WEIGHT_TIPPING','measured tipping',part_ids=['base'])] if status=='FAIL' else []
            r=CheckerResult(checker=spec.name,status=status,summary='timeout' if status=='INDETERMINATE' else status,findings=findings,
                assumptions={'source_sha256':sha256_file(source)})
            write_json(out/'result.json',r.model_dump());runs.append(CheckerRun(spec,r,out,()))
        return runs
    monkeypatch.setattr(assembly_physics,'run_assembly_checks',checks)
    async def model(**kw):
        data=json.loads(kw['input'][0]['content'][0]['text']) if isinstance(kw['input'],list) else json.loads(kw['input'])
        assert len(data['checker_summary'])==3
        assert data['pending_reviews']['code_critic']['required_changes']==['repair']
        kw['context'].record('read_file',kw['context'].source_path)
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,observations=[],repair_proposals=[
            RepairProposal(proposal_id='joint_advice',finding_ids=['assembly_standing:SELF_WEIGHT_TIPPING:base'],
                hypothesis='fix measured stability and visible issue',target=RepairTarget(),action='reshape')]))
    f[2].run=model
    result,book=run_flow(f)
    assert calls==[s.name for s in specs]*2 and len(f[-1])==1
    assert len((tmp_path/'repair_history.jsonl').read_text().splitlines())==1
    assert not result.approved  # FEA timeout is never hidden by appearance PASS.
    assert len(f[-1][0]['payload']['feedback']['checker_summary'])==3
    assert book['versions']['attempt_0001']['reviews']['assembly_standing']['status']=='PASS'


@pytest.mark.parametrize('final_area,retained',[(8.,'attempt_0001'),(12.,'original')])
def test_measurement_pass_still_optimizes_and_rejected_orientation_keeps_results(tmp_path,monkeypatch,final_area,retained):
    from test_fixed_assembly import mock_flow,run_flow
    from adsl.agents import assembly_physics,assembly_topology
    from adsl.agents.checkers import CheckerRun
    from adsl.agents.models import CheckerResult,RepairProposal,RepairTarget
    f=mock_flow(tmp_path,monkeypatch,[('PASS',True),('PASS',True)])
    f[3].write_text('assembly.add_part("base", body)\n')
    spec=checker_spec('assembly_overhang')
    f=(f[0],replace(f[1],checker_specs=(spec,),max_rounds=2,
        repair_policy=f[1].repair_policy.model_copy(update={'print_orientation_editable':True})),*f[2:])
    def checks(specs,*,source,root,**kw):
        out=root/'checkers'/spec.name;out.mkdir(parents=True)
        area=10. if 'round_01' in str(root) else final_area
        r=CheckerResult(checker=spec.name,status='PASS',summary='measurement only',
            metrics={'total_overhang_mm2':area,'area_uncertainty_mm2':.01,'measurement_config_sha256':'fixed'},
            findings=[assembly_physics.finding(spec.name,'ORIENTATION','can rotate',part_ids=['base'],
                category='optimization_opportunity',repairability='design_variable',required=False)],
            assumptions={'source_sha256':sha256_file(source)})
        write_json(out/'result.json',r.model_dump());return [CheckerRun(spec,r,out,())]
    async def advice(*args):
        return RepairProposal(proposal_id='rotate',finding_ids=['assembly_overhang:ORIENTATION:base'],
            hypothesis='rotate only',action='change_print_orientation',target=RepairTarget(parameters=['base']))
    async def patch(**kw):
        f[-1].append(kw);p=kw['source_path'];p.write_text(p.read_text()+'assembly.set_print_orientation("base", rotation_deg=(90,0,0))\n')
        return {'status':'CHANGED'}
    monkeypatch.setattr(assembly_physics,'run_assembly_checks',checks)
    monkeypatch.setattr(assembly_topology,'engineer',advice);monkeypatch.setattr(f[0],'_repair',patch)
    final,book=run_flow(f)
    assert final.approved and len(f[-1])==1 and book['retained']==retained
    output=json.loads((tmp_path/'checker_results.json').read_text())
    assert output['results'][0]['metrics']['total_overhang_mm2']==(final_area if retained!='original' else 10.)
    assert output['source_sha256']==sha256_file(tmp_path/'source.py')


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_ASSEMBLY_PHYSICS_REAL')!='1',reason='explicit native smoke')
def test_real_standing_seated_and_exit(tmp_path):
    from adsl.agents.assembly_standing import analyze
    results=[]
    for name,inverted in [('seated',False),('exit',True)]:
        args,_=fixture(tmp_path/name,inverted=inverted)
        result=analyze(args,{'standing':STANDING,'material':MATERIAL})
        write_json(args.output/'test_result.json',result.model_dump());results.append(result)
    assert results[0].status=='PASS',results[0].model_dump()
    assert results[1].status=='FAIL' and results[1].metrics['exits'],results[1].model_dump()


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_ASSEMBLY_PHYSICS_REAL')!='1',reason='explicit native smoke')
def test_real_mujoco_with_exact_fixture_proxies_only(tmp_path):
    """Isolate contact semantics while CoACD is blocked. NOT a production fallback."""
    from adsl.agents.assembly_physics import load_parts
    from adsl.agents.assembly_standing import verify_proxies,xml_model,simulate,save_frames
    from adsl.core.assembly_topology import interface_measurement
    def box(size,centre):return solid_mesh(mf.Manifold.cube(size,True).translate(centre))
    def bounded_box(lo,hi):
        # Share the exact boundary coordinates, not independently rounded
        # centre +/- half-size expressions that create ~1e-15 mm seams.
        mesh=solid_mesh(mf.Manifold.cube((2,2,2),True))
        mesh.vertices=np.where(mesh.vertices>0,np.asarray(hi),np.asarray(lo))
        return mesh
    results=[]
    for label,inverted in [('seated',False),('exit',True)]:
        args,report=fixture(tmp_path/label,inverted=inverted)
        _,_,meshes=load_parts(args)
        proxies={'base':[bounded_box((-30,-15,7),(30,15,12)),
            bounded_box((-30,-15,0),(-6.2,15,7)),bounded_box((6.2,-15,0),(30,15,7)),
            bounded_box((-6.2,-15,0),(6.2,-3.2,7)),bounded_box((-6.2,3.2,0),(6.2,15,7))],
            'stem':[box((20,12,30),(0,0,-15)),box((12,6,6.5),(0,0,2.75))]}
        if inverted:proxies['base'] += [box((10,30,40),(x,0,-20)) for x in (-25,25)]
        solids={};validation={}
        for n in meshes:
            validation[n],solids[n]=verify_proxies(meshes[n],proxies[n],.2)
            assert validation[n]['accepted'],validation[n]
        parts={p['id']:p for p in report['parts']}
        assert interface_measurement(report['connections'][0],parts,solids,1.)['status']=='PASS'
        xml=xml_model(report,meshes,proxies,STANDING,MATERIAL['density_kg_m3'])
        (args.output/'model.xml').write_text(xml)
        result,trajectory=simulate(xml,report,STANDING,args.output)
        result['frames']=save_frames(report,meshes,trajectory,args.output)
        result['proxy_scope']='Exact test-fixture boxes; CoACD NOT validated; not a production alternative'
        write_json(args.output/'test_result.json',result);results.append(result)
    # This isolated test validates time/event semantics, NOT end-to-end stable
    # PASS. The full CoACD+settling acceptance gate is the separate test above.
    assert not results[0]['tipped'] and not results[0]['exits'],results[0]
    assert results[1]['exits'],results[1]
    for result in results:
        assert result['assessment_time_seconds']==5. and result['simulated_duration_seconds']==5.
    assert results[1]['exits']['joint']<5. and results[1]['final_exited_interfaces']==['joint']
    saved=json.loads((tmp_path/'exit/results/test_result.json').read_text())
    assert saved['final_interfaces'][0]['exited'] is True

    args,report=fixture(tmp_path/'tipping',pair=False)
    mesh=box((10,10,80),(0,0,0))
    from scipy.spatial.transform import Rotation
    tf=np.eye(4);tf[:3,:3]=Rotation.from_euler('y',20,degrees=True).as_matrix()
    report['parts'][0]['assembly_transform']=tf.tolist()
    xml=xml_model(report,{'base':mesh},{'base':[mesh]},STANDING,MATERIAL['density_kg_m3'])
    (args.output/'model.xml').write_text(xml)
    result,trajectory=simulate(xml,report,STANDING,args.output)
    result['frames']=save_frames(report,{'base':mesh},trajectory,args.output)
    write_json(args.output/'test_result.json',result)
    assert result['tipped'] and result['peak_tilt_deg']>25


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_FIXED_REAL')!='1',reason='explicit Blender export smoke')
def test_existing_example_print_rotation_export_and_measurement(tmp_path):
    from adsl.agents.utils.execution import execute_asset_source
    from adsl.agents.assembly_overhang import analyze
    from adsl.agents.assembly_physics import load_parts
    repo=Path(__file__).resolve().parents[1]
    text=(repo/'examples/fixed_assembly/t_bracket.py').read_text()
    records=[];meshes=[];reports=[]
    for name,extra in [('original',''),('rotated','\nassembly.set_print_orientation("crossbar", rotation_deg=(90,0,0))\n')]:
        root=tmp_path/name;root.mkdir();source=root/'source.py';source.write_text(text+extra)
        execution=execute_asset_source(source,root/'asset',render=False,export_urdf=False,
            fixed_assembly={'mm_per_unit':1.,'fit_offset_mm':.2,'final_size_mm':[60.,20.,62.],
                            'validation_mode':'visual_only'})
        out=root/'measure';out.mkdir()
        args=SimpleNamespace(source=source,manifest=execution.output_root/'assembly/assembly_manifest.json',
            output=out,topology_cache=None,source_index=None)
        reports.append(json.loads(args.manifest.read_text()))
        records.append(analyze(args,{'overhang':OVERHANG}).model_dump())
        write_json(out/'result.json',records[-1]);meshes.append(load_parts(args)[2])
    assert all(r['status']=='PASS' for r in records)
    for first,second in zip(reports[0]['parts'],reports[1]['parts']):
        assert first['assembly_transform']==second['assembly_transform']
        # Rotation changes printing only; round-trip restored local material.
        assert meshes[0][first['id']].volume==pytest.approx(meshes[1][first['id']].volume,rel=1e-10)
        assert np.allclose(meshes[0][first['id']].bounds,meshes[1][first['id']].bounds,atol=1e-10)
    assert reports[0]['parts'][0]['print_transform_mm']!=reports[1]['parts'][0]['print_transform_mm']
    write_json(tmp_path/'comparison.json',area_comparison(records[0],records[1]))


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_ASSEMBLY_PHYSICS_REAL')!='1',reason='explicit native smoke')
def test_real_fea_mesh_tie(tmp_path):
    from adsl.agents.assembly_fea import analyze
    args,_=fixture(tmp_path)
    config=dict(mesh_size_mm=2.,supports=[dict(part_id='base',frame='part_local',bounds_mm=[[-31,-16,11.9],[31,16,12.1]],dofs=[1,2,3])],
        loads=[dict(part_id='stem',frame='part_local',bounds_mm=[[-11,-7,-30.1],[11,7,-29.9]],force_n=[10,0,0])],
        allowable_stress_pa=25e6,allowable_displacement_m=.005,include_gravity=True)
    result=analyze(args,{'fea':config,'material':MATERIAL})
    write_json(args.output/'test_result.json',result.model_dump())
    assert result.status=='PASS',result.model_dump()
    assert result.metrics['max_displacement_m']>0
    assert result.metrics['transfer_check']['reaction_balanced']
    assert result.metrics['transfer_check']['interfaces_compatible']
    assert (args.output/'displacement_mm.png').is_file()


@pytest.mark.skipif(__import__('os').environ.get('ADSL_TEST_ASSEMBLY_PHYSICS_REAL')!='1',reason='explicit native smoke')
def test_real_fea_finer_mesh_untied_and_analytic_control(tmp_path):
    import os,time
    from adsl.agents.assembly_fea import analyze
    from experiments.load_bearing_structural_performance.assembly import (
        mesh_part,remap_parts,write_assembly_deck,run_ccx,result_fields)
    configs=dict(supports=[dict(part_id='base',frame='part_local',bounds_mm=[[-31,-16,11.9],[31,16,12.1]],dofs=[1,2,3])],
        loads=[dict(part_id='stem',frame='part_local',bounds_mm=[[-11,-7,-30.1],[11,7,-29.9]],force_n=[10,0,0])],
        allowable_stress_pa=25e6,allowable_displacement_m=.005,include_gravity=True)
    records=[]
    for label,size in [('coarse',2.),('fine',1.5)]:
        args,_=fixture(tmp_path/label);start=time.monotonic()
        result=analyze(args,{'fea':dict(configs,mesh_size_mm=size),'material':MATERIAL})
        write_json(args.output/'test_result.json',result.model_dump())
        assert result.status=='PASS',result.model_dump()
        records.append(dict(label=label,mesh_size_mm=size,seconds=time.monotonic()-start,metrics=result.metrics))
    ccx=Path(os.environ['ADSL_CCX_BIN'])
    # Remove only the declared binding, not geometry/load/support, in a control
    # deck. The unbound stem must not reproduce the constrained solution.
    lines=(tmp_path/'coarse/results/assembly.inp').read_text().splitlines();kept=[];skip=False
    for line in lines:
        if skip:skip=False;continue
        if line.startswith('*TIE'):skip=True;continue
        kept.append(line)
    deck=tmp_path/'untied.inp';deck.write_text('\n'.join(kept)+'\n')
    unbound=run_ccx(ccx,deck,60)
    u,_,_=result_fields(deck.with_suffix('.dat')) if deck.with_suffix('.dat').is_file() else ({},{},{})
    unbound_u=max((float(np.linalg.norm(v)) for v in u.values()),default=None)
    assert unbound['status']!='SOLVED' or unbound_u is None or unbound_u>100*records[0]['metrics']['max_displacement_m']
    # Independent axial bar checks force/length/stiffness units against FL/EA.
    mesh=solid_mesh(mf.Manifold.cube((10,10,40),True).translate((0,0,20)))
    local=mesh_part(mesh,tmp_path/'bar.inp',2.)
    report={'mm_per_unit':1.,'parts':[{'id':'bar','assembly_transform':np.eye(4).tolist()}]}
    nodes,elements,mapping=remap_parts({'bar':local},report)
    supports=[dict(part_id='bar',frame='part_local',bounds_mm=[[-6,-6,-.01],[6,6,.01]],dofs=[1,2,3])]
    loads=[dict(part_id='bar',frame='part_local',bounds_mm=[[-6,-6,39.99],[6,6,40.01]],force_n=[0,0,10])]
    deck=tmp_path/'axial.inp';write_assembly_deck(deck,nodes,elements,mapping,MATERIAL,[],supports,loads,False)
    solved=run_ccx(ccx,deck,60);assert solved['status']=='SOLVED'
    u,_,_=result_fields(deck.with_suffix('.dat'));actual=max(v[2] for v in u.values());theory=10*.04/(3e9*.01*.01)
    assert actual==pytest.approx(theory,rel=.1)  # fixed-end Poisson constraint, not a checker threshold
    write_json(tmp_path/'validation.json',{'meshes':records,'untied':unbound,'untied_displacement_m':unbound_u,
        'axial_displacement_m':actual,'FL_over_EA_m':theory,
        'displacement_relative_change':records[1]['metrics']['max_displacement_m']/records[0]['metrics']['max_displacement_m']-1,
        'stress_relative_change':records[1]['metrics']['max_von_mises_pa']/records[0]['metrics']['max_von_mises_pa']-1,
        'scope':'small validation control; no universal convergence or physical safety claim'})
