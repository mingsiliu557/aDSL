"""33 fixed, inline artificial configurations; no archived assets or checker processes.

Run with ADSL_TEST_FIXED_REAL=1. Optional ADSL_MESH_SMOKE_EVIDENCE_DIR stores
one strict-JSON measurement record per configuration. All native work is CPU.
"""
import asyncio
from collections import Counter
import hashlib
import importlib
import json
import os
from pathlib import Path
from types import SimpleNamespace
import time

import manifold3d as mf
import numpy as np
import pytest

from adsl.core.export import mesh_validity as validity
from adsl.core.export import mesh_repair as repair

pytestmark = pytest.mark.skipif(
    os.environ.get("ADSL_TEST_FIXED_REAL") != "1",
    reason="explicit native CPU mesh smoke",
)

CASES = ([f'I{i}' for i in range(1, 9)] + [f'T{i}' for i in range(1, 11)]
         + [f'K{i}' for i in range(1, 4)] + [f'N{i}' for i in range(1, 6)]
         + [f'W{i}' for i in range(1, 8)])
REJECT = {'I4', 'I5', 'I6', 'I7', 'I8', 'T6', 'T7', 'T8', 'T9', 'T10', 'N5'}


def snapshot(obj):
    obj.data.calc_loop_triangles()
    return (np.array([tuple(v.co) for v in obj.data.vertices]),
            tuple(tuple(p.vertices) for p in obj.data.polygons),
            tuple(p.material_index for p in obj.data.polygons), obj.data)


def triangles(obj):
    return repair._mesh_triangles(obj.data, np.eye(4))[:2]


def make_object(vertices, polygons, name):
    import bpy
    data = bpy.data.meshes.new(name + '_mesh')
    data.from_pydata(vertices, [], polygons)
    data.update()
    obj = bpy.data.objects.new(name, data)
    bpy.context.collection.objects.link(obj)
    for i in range(2):
        material = bpy.data.materials.new(f'{name}_material_{i}')
        material.use_nodes = True
        data.materials.append(material)
    for i, polygon in enumerate(data.polygons):
        polygon.material_index = i % 2
    return obj


def pyramid(case):
    # The cap has a redundant collinear point shared with valid side faces.
    vertices = [(0, 3, 0), (0, 4, 1), (0, -1, 0), (0, 1, 0), (-1, 0, .5)]
    polygons = [(0, 1, 2, 3), (1, 0, 4), (2, 1, 4), (3, 2, 4), (0, 3, 4)]
    if case == 'I4':
        polygons.pop()
    if case == 'I6':
        polygons[1] = tuple(reversed(polygons[1]))
    return make_object(vertices, polygons, case)


def cracked_cube(case):
    gap = .001 if case == 'I7' else float(np.spacing(np.float32(1.)))
    vertices = np.array([(0,0,0),(1,0,0),(1,1,0),(0,1,0),
                         (0,0,1),(1,0,1),(1,1,1),(0,1,1),(gap,gap,0)]) + 1.
    polygons = [(0,8,3,2,1),(4,5,6,7),(0,1,5,4),(1,2,6,5),(2,3,7,6),(3,0,4,7)]
    if case == 'I8':
        polygons.pop(1)
    return make_object(vertices, polygons, case), gap


def exact_cube():
    vertices = [(0,0,0),(1,0,0),(1,1,0),(0,1,0),
                (0,0,1),(1,0,1),(1,1,1),(0,1,1),(0,0,0)]
    polygons = [(0,3,2,1,8),(4,5,6,7),(0,8,1,5,4),
                (1,2,6,5),(2,3,7,6),(3,0,4,7)]
    return make_object(vertices, polygons, 'I2')


def explicit_degenerate_tetrahedron():
    return make_object([(0,0,0),(1,0,0),(0,1,0),(0,0,1),(0,0,0)],
        [(0,2,4),(4,2,1),(0,4,3),(4,1,3),(0,3,2),(1,2,3)], 'I5')


def positive_tetrahedron():
    return make_object([(0,0,0),(1,0,0),(0,1e-12,0),(0,0,1)],
        [(0,2,1),(0,1,3),(0,3,2),(1,2,3)], 'K2')


def assert_unchanged(obj, before, counts):
    import bpy
    after = snapshot(obj)
    np.testing.assert_array_equal(before[0], after[0])
    assert before[1:] == after[1:]
    assert counts == (len(bpy.data.objects), len(bpy.data.meshes))


def input_case(case, row):
    import bpy
    unit = 1000. if case == 'N5' else 1.
    if case in {'I1', 'I4', 'I6'}:
        obj = pyramid(case)
    elif case == 'I2':
        obj = exact_cube()
    elif case == 'I5':
        obj = explicit_degenerate_tetrahedron()
    elif case == 'K2':
        obj = positive_tetrahedron()
    else:
        obj, gap = cracked_cube(case)
        row['gap_scene_units'] = gap
        required = np.sqrt(2)*gap*unit
        row['minimum_weld_displacement_mm'] = required
        if case == 'N5':
            assert required > 1e-4
        if case == 'N4':
            obj.matrix_world.translation = (1000., 2000., 3000.)
    before = snapshot(obj)
    material_areas = [sum(t.area for t in obj.data.loop_triangles
                          if obj.data.polygons[t.polygon_index].material_index == material)
                      for material in range(2)]
    counts = len(bpy.data.objects), len(bpy.data.meshes)
    vertices, faces = triangles(obj)
    metrics = validity.mesh_metrics(vertices, faces)
    row.update(entry='normalize_blender_input', mm_per_unit=unit,
               metrics_before=metrics, source_vertices=vertices.tolist(), source_faces=faces.tolist())
    cross = np.cross(vertices[faces[:,1]]-vertices[faces[:,0]],
                     vertices[faces[:,2]]-vertices[faces[:,0]])
    if case in {'I1','I2','I4','I5','I6'}:
        assert np.any(np.all(cross == 0, axis=1)), 'factory must contain exact-zero triangles'
    if case == 'I2':
        assert any(np.array_equal(vertices[p[i]],vertices[p[(i+1)%len(p)]])
                   for p in before[1] for i in range(len(p)))
    if case in {'I3','I7','N4','N5'}:
        assert metrics['boundary_edges'] == 3 and metrics['zero_area_triangles'] == 0
        # An isolated triangular boundary loop, independent of the repair helper.
        boundary = Counter(tuple(sorted(e)) for f in faces for e in zip(f,np.roll(f,-1)))
        edges = [e for e,count in boundary.items() if count == 1]
        degrees = Counter(v for e in edges for v in e)
        assert len(edges) == 3 and set(degrees.values()) == {2}
    if case == 'I8':
        assert metrics['boundary_edges'] == 7
    if case == 'I4':
        assert metrics['boundary_edges'] > 0
    if case == 'I6':
        assert metrics['inconsistent_edges'] > 0
    if case == 'K2':
        areas = np.linalg.norm(cross,axis=1)/2
        assert metrics['valid'] and 0 < areas.min() < 1e-10
    if case in REJECT:
        with pytest.raises(validity.MeshEvaluationError) as failure:
            validity.normalize_blender_input(obj, mm_per_unit=unit,
                                            node_path='artificial/input', part_id='manual_part')
        diagnostic = failure.value.diagnostic
        assert diagnostic['code'] == 'INPUT_GEOMETRY_INVALID'
        assert diagnostic['stage'] == 'input_geometry'
        assert diagnostic['part_id'] == 'manual_part' and diagnostic['node_path'] == 'artificial/input'
        assert_unchanged(obj,before,counts)
        row.update(actual='correct_rejection', diagnostic=diagnostic, source_unchanged=True)
        return
    result = validity.normalize_blender_input(obj,mm_per_unit=unit,node_path='artificial/input')
    result_vertices, result_faces = triangles(obj)
    measured, after = validity.validate_mesh(result_vertices,result_faces)
    assert after['valid'] and after['self_intersection'] == 'NOT_EVALUATED'
    assert result['maximum_displacement_mm'] <= result['displacement_budget_mm'] if result['status']=='APPLIED' else True
    if case in {'I1','I2'}:
        np.testing.assert_array_equal(np.unique(result_vertices,axis=0),np.unique(vertices,axis=0))
        def oracle(v,f):
            t=v[f]
            return (float(np.linalg.norm(np.cross(t[:,1]-t[:,0],t[:,2]-t[:,0]),axis=1).sum()/2),
                    float(np.einsum('ij,ij->i',t[:,0],np.cross(t[:,1],t[:,2])).sum()/6))
        assert oracle(result_vertices,result_faces) == pytest.approx(oracle(vertices,faces),rel=1e-12)
        for material in range(2):
            new_area=sum(t.area for t in obj.data.loop_triangles
                         if obj.data.polygons[t.polygon_index].material_index == material)
            assert new_area == pytest.approx(material_areas[material],rel=1e-12)
        row['materials_preserved']=True
        assert result.get('maximum_displacement_mm',0) == 0
    if case == 'K2':
        assert result['status'] == 'UNCHANGED'
        assert_unchanged(obj,before,counts)
    if case in {'I3','N4'}:
        assert tuple(p.material_index for p in obj.data.polygons)==before[2]
        assert after['shell_components']==metrics['shell_components']==1
        assert result['maximum_displacement_mm']==pytest.approx(row['minimum_weld_displacement_mm'])
        assert result['maximum_displacement_mm']<=1e-4
        row['materials_preserved']=True
    data = obj.data
    repeat = validity.normalize_blender_input(obj,mm_per_unit=unit)
    assert repeat['status']=='UNCHANGED' and obj.data is data
    assert (len(bpy.data.objects),len(bpy.data.meshes)) == counts
    row.update(actual='normal_control' if case=='K2' else 'safe_repair',
               result=result, metrics_after=after, volume=float(measured.volume()), idempotent=True,
               input_commit='atomic_replacement' if result['status']=='APPLIED' else 'unchanged_data')


def uniform(solid, material=0):
    raw=solid.to_mesh64()
    return validity.validate_mesh(raw.vert_properties[:,:3],raw.tri_verts,
        face_ids=np.full(len(raw.tri_verts),material,dtype=np.uint64))[0]


def sliver():
    return uniform(mf.Manifold.cube((1,1,1))) + uniform(
        mf.Manifold.cube((1,1,1)).translate((.5+1e-9,1e-9,0)))


def arrays(solid, centered=True):
    raw=solid.to_mesh64()
    vertices=np.asarray(raw.vert_properties[:,:3]).copy()
    if centered:
        vertices -= (vertices.min(axis=0)+vertices.max(axis=0))/2
    return vertices,np.asarray(raw.tri_verts).copy(),np.asarray(raw.face_id).copy()


def flip_pyramid():
    u=float(np.spacing(np.float32(1.)))
    return (np.array([(0,0,0),(1+.49*u,1+.51*u,0),(2,2+.98*u,0),
                      (0,3,0),(1,1,1)],dtype=np.float64),
            np.array([(0,2,1),(0,3,2),(0,1,4),(1,2,4),(2,3,4),(3,0,4)],dtype=np.uint64),
            np.zeros(6,dtype=np.uint64))


def precision_case(case,row,monkeypatch):
    unit=1.
    budget=2e-6
    solid=sliver()
    v,f,ids=arrays(solid)
    if case in {'T2','T3','T8','T9'}:
        v,f,ids=flip_pyramid()
        if case=='T2':
            v=np.array([(0,0,0),(1,-1e-9,0),(2,0,0),(1,1,0),(1,.5,1.)])
            v-=(v.min(axis=0)+v.max(axis=0))/2
        if case=='T8':ids=np.arange(len(f),dtype=np.uint64)
        if case=='T9':budget=1e-9
    elif case=='T4':
        av,af,ai=arrays(sliver())
        bv,bf,bi=flip_pyramid()
        av-=np.array([4,0,0])
        v=np.concatenate((av,bv));f=np.concatenate((af,bf+len(av)))
        ids=np.concatenate((ai,bi+1))
    elif case=='T5':
        solid=sliver().scale((4,4,4))-mf.Manifold.cube((.5,.5,.5)).translate((.2,.2,.2))
        v,f,ids=arrays(solid)
    elif case=='T6':
        v=np.array([(1,0,0),(1+1e-9,0,0),(0,1,0),(0,0,1.)])
        f=np.array([(0,2,1),(0,1,3),(0,3,2),(1,2,3)],dtype=np.uint64)
        ids=np.zeros(4,dtype=np.uint64)
    elif case=='T7':ids=np.arange(len(f),dtype=np.uint64)
    elif case=='T10':
        solid=uniform(mf.Manifold.cube((1,1,1)))+uniform(
            mf.Manifold.cube((2,1,1)).translate((1+1e-9,0,0)))
        v,f,ids=arrays(solid)
    elif case=='K1':
        solid=uniform(mf.Manifold.cube((1.123456789,1,1)))
        v,f,ids=arrays(solid)
    elif case=='K3':
        solid=uniform(mf.Manifold.cube((1,1,1)))+uniform(
            mf.Manifold.cube((1,1,1)).translate((1.2,0,0)))
        v,f,ids=arrays(solid)
    elif case in {'N2','N3'}:
        unit=.001 if case=='N2' else 1000.
        solid=solid.scale((1/unit,)*3)
        v,f,ids=arrays(solid)
    original=v.copy(),f.copy(),ids.copy()
    source,source_metrics=validity.validate_mesh(v,f,face_ids=ids)
    rounded=v.astype(np.float32).astype(np.float64)
    cast_metrics=validity.mesh_metrics(rounded,f)
    cast_orientation=repair.face_orientation_metrics(v,rounded,f)
    row.update(entry='target_mesh' if case in {'T1','T5','T10','K3','N1','N2','N3'} else
               'repair_float32_mesh',mm_per_unit=unit,displacement_budget_scene_units=budget,
               metrics_source=source_metrics,metrics_cast=cast_metrics,
               orientation_cast=cast_orientation,source_vertices=v.tolist(),source_faces=f.tolist(),
               source_materials=ids.tolist())
    assert source_metrics['valid']
    if case in {'T1','T2','T4','T5','T6','T7','N1','N2','N3'}:
        assert cast_metrics['zero_area_triangles'] > 0
    if case in {'T3','T8','T9'}:
        assert cast_metrics['valid'] and cast_metrics['zero_area_triangles']==0
        assert cast_metrics['signed_volume']>0 and cast_orientation['flipped_faces']==1
    if case=='T4':assert cast_orientation['flipped_faces']>0 and source_metrics['material_components']==2
    if case=='T10':
        shells=sorted(source.decompose(),key=lambda s:s.bounding_box()[0])
        assert len(shells)==2 and shells[1].bounding_box()[0]-shells[0].bounding_box()[3]==pytest.approx(1e-9)
        # Actual centered rounding has coincident opposing seam vertices.
        original_gap=np.sort(np.unique(v[:,0]))
        target_gap=np.sort(np.unique(rounded[:,0]))
        assert len(target_gap)<len(original_gap)
    if case in REJECT:
        with pytest.raises(validity.MeshEvaluationError) as failed:
            if case=='T10':validity.target_mesh(solid,part_id='manual_part',node_path='artificial/gap')
            else:repair.repair_float32_mesh(v,f,ids,displacement_budget=budget,mm_per_unit=unit)
        diagnostic=failed.value.diagnostic
        assert diagnostic['code']==('TARGET_PRECISION_UNREPRESENTABLE' if case=='T10' else 'LOCAL_PRECISION_REPAIR_REJECTED')
        assert diagnostic['stage']=='target_precision'
        if case=='T6':assert diagnostic['repair']['rejected_candidates']['link_condition']>0
        if case in {'T7','T8'}:assert diagnostic['repair']['rejected_candidates']['material_boundary']>0
        if case=='T9':assert diagnostic['repair']['remaining_bad_faces_total']>0
        row.update(actual='correct_rejection',diagnostic=diagnostic,source_unchanged=True)
    elif case in {'T1','T5','K3','N1','N2','N3'}:
        reused=[]
        if case=='T1':
            implementation=repair._repair_float32_mesh_checked
            validations=[]
            actual_validate=validity.validate_mesh
            def tracked_validate(*args,**kwargs):
                value=actual_validate(*args,**kwargs)
                validations.append((np.asarray(args[0]).copy(),np.asarray(args[1]).copy(),value))
                return value
            def tracked_repair(*args,**kwargs):
                value=implementation(*args,**kwargs)
                reused.append(value)
                return value
            monkeypatch.setattr(validity,'validate_mesh',tracked_validate)
            monkeypatch.setattr(repair,'_repair_float32_mesh_checked',tracked_repair)
        transformed=solid.translate((1000,2000,3000)) if case=='N1' else solid
        out,pose,result=validity.target_mesh(transformed,mm_per_unit=unit,
            part_id='manual_part',node_path='artificial/target')
        if case=='T1':
            assert reused and result['metrics'] is reused[-1].metrics
            accepted=reused[-1]
            matching=[x for x in validations if np.array_equal(x[0],accepted.vertices)
                      and np.array_equal(x[1],accepted.faces)]
            assert len(matching)==1 and matching[0][2][0] is accepted.solid
            row['business_target_validation_calls']=len(matching)
        assert result['metrics']['valid']
        assert result['surface_displacement_upper_bound_mm']<=result['displacement_budget_mm']
        if case=='N1':
            base,_,baseline=validity.target_mesh(solid)
            assert result['displacement_budget_mm']==pytest.approx(baseline['displacement_budget_mm'])
            np.testing.assert_allclose(out.extents,base.extents,atol=1e-6)
        if case in {'N2','N3'}:
            baseline=validity.target_mesh(sliver())[2]
            assert result['displacement_budget_mm']==pytest.approx(baseline['displacement_budget_mm'])
            np.testing.assert_allclose(out.extents*unit,[1.5,1.,1.],atol=baseline['displacement_budget_mm'])
        measured,metrics=validity.validate_mesh(out.vertices,out.faces)
        assert abs(measured.volume()-source.volume()) <= result['volume_budget_mm3']/unit**3
        if case=='T5':
            assert metrics['shell_components']==2 and metrics['material_components']==1
            assert any(s.volume()<0 for s in measured.decompose())
        if case=='K3':
            islands=sorted(measured.decompose(),key=lambda s:s.bounding_box()[0])
            assert len(islands)==2
            assert islands[1].bounding_box()[0]-islands[0].bounding_box()[3]==pytest.approx(.2,abs=1e-6)
        # Reprocessing the valid result must preserve the same physical frame.
        again,second_pose,second=validity.target_mesh(measured,mm_per_unit=unit)
        np.testing.assert_allclose(validity.world_vertices(again.vertices,second_pose),out.vertices,
                                   atol=result['displacement_budget_mm']/unit)
        assert second['metrics']['valid']
        row.update(actual='normal_control' if case=='K3' else 'safe_repair',
                   result=result,metrics_after=metrics,idempotent=True)
    else:
        calls=[]
        real_validate=validity.validate_mesh
        def count(*args,**kwargs):
            returned=real_validate(*args,**kwargs)
            calls.append((np.asarray(args[0]).copy(),np.asarray(args[1]).copy(),returned))
            return returned
        monkeypatch.setattr(validity,'validate_mesh',count)
        checked=repair._repair_float32_mesh_checked(v,f,ids,displacement_budget=budget,mm_per_unit=unit)
        output,outfaces,outids,result=checked.vertices,checked.faces,checked.face_ids,checked.report
        target_calls=[c for c in calls if np.array_equal(c[0],output) and np.array_equal(c[1],outfaces)]
        assert len(target_calls)==1 and checked.solid is target_calls[0][2][0]
        assert checked.metrics is target_calls[0][2][1]
        assert checked.orientation['valid'] and result['metrics_after']['valid']
        assert result['surface_displacement_upper_bound']<=budget
        assert set(outids)==set(ids)
        if case=='T2':
            assert [x['operation'] for x in result['operations']]==['diagonal_flip']
            assert result['operations'][0]['surface_displacement_upper_bound']==0 and len(outfaces)==len(f)
            np.testing.assert_array_equal(output,rounded)
        if case=='T3':
            assert result['defect_counts_before']['flipped_faces']==1 and result['bad_face_count_after']==0
            for face in f[2:]:assert any(np.array_equal(face,x) for x in outfaces)
            # Centering changes actual rounding; this business entry may directly pass.
            business_mesh,_,business=validity.target_mesh(source)
            assert business['metrics']['valid'] and business['face_orientation']['valid']
            row['business_result']=business
        if case=='T4':assert checked.metrics['material_components']==2
        if case=='K1':
            assert result['status']=='UNCHANGED' and result['operations']==[]
            assert not np.array_equal(output,v) and checked.solid.volume()!=source.volume()
            wrapped=repair.repair_float32_mesh(v,f,ids,displacement_budget=budget)
            assert len(wrapped)==4
            business=validity.target_mesh(source)[2]
            assert business['metrics']['valid'] and business['local_precision_repair'] is None
        repeated=repair.repair_float32_mesh(output,outfaces,outids,displacement_budget=budget)
        assert repeated[3]['status']=='UNCHANGED' and not repeated[3]['operations']
        np.testing.assert_array_equal(output,repeated[0])
        row.update(actual='normal_control' if case=='K1' else 'safe_repair',result=result,
                   target_validation_calls=len(target_calls),idempotent=True)
    for before,after in zip(original,(v,f,ids)):np.testing.assert_array_equal(before,after)
    row['source_unchanged']=True


def assembly_fixture():
    from adsl.core import Cube, FixedAssembly, InterfaceFrame, TabSlot
    assembly=FixedAssembly(root_id='A',mm_per_unit=1.)
    assembly.add_part('A',Cube((4,4,4),center=(0,0,-2)),components=['lower_cube'])
    assembly.add_part('B',Cube((4,4,4),center=(0,0,2)),components=['upper_cube'])
    frame=InterfaceFrame(insert_axis=(0,0,-1))
    assembly.connect('mate',tab_part='B',slot_part='A',tab_frame=frame,slot_frame=frame,
        tab_port='bottom',slot_port='top',parameter_name='joint',
        parameters=TabSlot(1,1,1,1.5,.1,root_overlap_mm=.5,opening_extension_mm=.5))
    assembly.validate()
    assert len(assembly.parts)==2 and len(assembly.connections)==1
    np.testing.assert_array_equal(assembly.transforms['A'],assembly.transforms['B'])
    assert assembly.connections[0]['id']=='mate'
    return assembly


def workflow_stub(directory,source,manifest,monkeypatch,*,expect_approved,no_change=False):
    """The real FixedAssembly loop and evidence adapter, with all external work stubbed."""
    from adsl.agents import fixed_assembly as flow
    from adsl.agents import assembly_topology as adapter
    from adsl.agents.models import (ObjectRequest,FixedAssemblyPlan,
        GradedImageCriticDecision,GradedCodeCriticDecision,EngineeringCriticDecision)
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.utils.execution import ExecutionResult
    from adsl.agents.utils.usage import UsageRecorder
    import shutil
    directory.mkdir()
    assigned=directory/'source.py';assigned.write_text(source.read_text())
    source_hash=flow.file_hash(assigned)
    saved={**manifest,'source_sha256':source_hash}
    events=[]
    request=ObjectRequest('Two cubes joined at their common stop plane.',directory,'artificial',
        max_rounds=2 if no_change else 1,checker_specs=(),
        fixed_assembly={'mm_per_unit':1.,'fit_offset_mm':.1,'final_size_mm':[4.,4.,8.],
                        'validation_mode':'visual_only' if saved.get('verification_scope') == 'visual_code_only' else 'geometry'})
    plan=FixedAssemblyPlan.model_validate(dict(object_name='Two cubes',
        components=[dict(name=n,description=n) for n in ['lower_cube','upper_cube']],relations=['touch'],
        critic_checklist=['two cubes'],print_parts=[dict(id='A',components=['lower_cube']),
            dict(id='B',components=['upper_cube'])],root_part='A',
        connections=[dict(id='mate',tab_part='B',slot_part='A',tab_port='bottom',slot_port='top',
            interface_type='tab_slot',parameter_name='joint',insertion_direction='-Z',fit_intent='positive gap')],
        mm_per_unit=1.,final_size_mm=[4.,4.,8.]))
    usage=UsageRecorder(directory)
    runtime=SimpleNamespace(usage=usage,agent=lambda **kwargs:kwargs)
    workflow=ObjectWorkflow.__new__(ObjectWorkflow)
    async def image(**kwargs):
        events.append('image_stub')
        decision=GradedImageCriticDecision(approved=True,observations=[],issues=[])
        kwargs['image_history'].append(decision.model_dump())
        return decision
    async def code(**kwargs):
        events.append('code_stub')
        decision=GradedCodeCriticDecision(approved=True,observations=[],required_changes=[],issues=[])
        kwargs['code_history'].append(decision.model_dump())
        return decision
    async def unchanged(**kwargs):
        events.append('coder_NO_CHANGE_stub')
        assert kwargs['allow_no_change']
        return {'status':'NO_CHANGE','reason':'No bounded design edit justified by artificial display fault.'}
    def execute(current,out,**kwargs):
        events.append('executor_stub')
        shutil.copytree(directory.parent/'current',out/'assembly')
        (out/'render').mkdir()
        payload=json.loads(json.dumps(saved))
        (out/'assembly/assembly_manifest.json').write_text(json.dumps(payload))
        # Seed a previous version, then publish only a declared current display.
        display=out/'render/scene.glb';display.write_bytes(b'stale previous-version display')
        filename=payload.get('scene_glb') or payload.get('diagnostic',{}).get('glb')
        if filename:
            shutil.copy2(out/'assembly'/filename,display)
            assert display.read_bytes()!=b'stale previous-version display'
        else:
            display.unlink()
        for filename,digest in payload.get('files_sha256',{}).items():
            assert flow.file_hash(out/'assembly'/filename)==digest
        png=out/'render/view.png';png.write_bytes(b'stub image; no renderer used')
        return ExecutionResult(out,display,None,(png,),'','')
    async def model(**kwargs):
        events.append('engineering_NO_PROPOSAL_stub')
        data=json.loads(kwargs['input'])
        assert data['evaluation_failures'] and data['typed_findings']
        assert any(f['region']['part_names']==['B'] for f in data['typed_findings'])
        kwargs['context'].record('read_file',kwargs['context'].source_path)
        return SimpleNamespace(final_output=EngineeringCriticDecision(approved=False,
            observations=['Preserve canonical source; injected target precision failure.'],repair_proposals=[]))
    runtime.run=model
    monkeypatch.setattr(workflow,'_review_generation_image',image)
    monkeypatch.setattr(workflow,'_review_generation_code',code)
    monkeypatch.setattr(workflow,'_repair',unchanged)
    monkeypatch.setattr(flow,'execute_asset_source',execute)
    def forbidden(*args,**kwargs):raise AssertionError('checker process forbidden in artificial smoke')
    monkeypatch.setattr(adapter,'run_assembly_topology',forbidden)
    result=asyncio.run(flow.iterate_fixed_assembly(workflow,runtime=runtime,request=request,
        workspace=directory,source_path=assigned,plan=plan))
    assert result.approved is expect_approved
    book=json.loads((directory/'assembly_versions.json').read_text())
    if no_change:
        assert 'engineering_NO_PROPOSAL_stub' in events and 'coder_NO_CHANGE_stub' in events
        assert book['stop_reason']=='NO_CHANGE' and book['qualified'] is None
    assert assigned.read_bytes()==source.read_bytes()
    if not expect_approved:assert not book.get('qualified')
    selected=book['versions'][book['retained']]
    flow.assert_version(selected)
    assert selected['reviews']['geometry']['source_sha256']==flow.file_hash(Path(selected['source']))==source_hash
    published=json.loads((directory/'assembly/assembly_manifest.json').read_text())
    final=json.loads((directory/'assembly_result.json').read_text())
    assert final['version_id']==book['retained'] and final['source_sha256']==published['source_sha256']==source_hash
    for filename,digest in published.get('files_sha256',{}).items():
        assert flow.file_hash(directory/'assembly'/filename)==digest
    return dict(approved=result.approved,events=events,stop_reason=book['stop_reason'],
                saved_version_file_hashes_valid=True,published_version_id=final['version_id'],
                source_sha256=source_hash,retained=book['retained'],qualified=book.get('qualified'))


def export_case(case,row,tmp_path,monkeypatch):
    exporter=importlib.import_module('adsl.core.export.export_assembly')
    assembly=assembly_fixture()
    input_snapshot=None
    failure_resource_checks=[]
    display_resource_checks=[]
    if case in {'W3','W4'}:
        import bpy
        def input_state():
            assets={kind:{name:dict(object_id=id(shape),parent_id=id(shape._parent),
                                   construction=shape.to_dict()) for name,shape in group.items()}
                    for kind,group in [('parts',assembly.parts),('bodies',assembly.bodies)]}
            return json.dumps(assets,sort_keys=True,allow_nan=False,
                              default=lambda value:value.tolist())
        input_snapshot=input_state()
    mode='visual_only' if case=='W2' else 'geometry'
    output=tmp_path/'current'
    source=tmp_path/'source.py';source.write_text('# Fixed artificial cubes A/B, no historical case.\n')
    source_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    calls=[]
    real_write=exporter._write_mesh_glb
    def write(*args,**kwargs):
        path=Path(args[2]);calls.append(path.name)
        result=real_write(*args,**kwargs)
        if case in {'W3','W4'}:
            expected_count=len(args[0])
            counts=(len(bpy.data.objects),len(bpy.data.meshes))
            assert counts==(2*expected_count,expected_count)
            assert sum(obj.type=='MESH' for obj in bpy.data.objects)==expected_count
            assert all(mesh.users==1 for mesh in bpy.data.meshes)
            assert {obj.name for obj in bpy.data.objects if obj.type!='MESH'}==set(args[0])
            display_resource_checks.append(dict(file=path.name,shown_parts=expected_count,
                resident_objects=counts[0],resident_meshes=counts[1],orphan_meshes=0))
        if (case=='W5' and path.name=='scene.glb') or (case=='W6' and path.name=='exploded.glb'):
            path.write_bytes(b'actual corrupted GLB after successful serialization')
        return result
    monkeypatch.setattr(exporter,'_write_mesh_glb',write)
    if case in {'W3','W4'}:
        real_evaluated=exporter.evaluated
        bad=assembly.parts['B'] if case=='W3' else assembly.bodies['A']
        def evaluated(shape,*args,**kwargs):
            if shape is bad:
                before=(len(bpy.data.objects),len(bpy.data.meshes))
                try:
                    raise validity.MeshEvaluationError('BOOLEAN_EVALUATION_FAILED','artificial input fault',
                        stage='internal_evaluation',failure_kind='geometry_evaluation',
                        node_path='artificial/union',operation='UNION',input_count=2)
                finally:
                    after=(len(bpy.data.objects),len(bpy.data.meshes))
                    assert after==before
                    assert input_state()==input_snapshot
                    failure_resource_checks.append(dict(objects_before=before[0],objects_after=after[0],
                        meshes_before=before[1],meshes_after=after[1],failed_asset_unchanged=True))
            return real_evaluated(shape,*args,**kwargs)
        monkeypatch.setattr(exporter,'evaluated',evaluated)
    if case=='W7':
        actual_target=validity.target_mesh
        def target(*args,**kwargs):
            if kwargs.get('part_id')=='B':
                raise validity.MeshEvaluationError('TARGET_PRECISION_UNREPRESENTABLE','injected B display fault',
                    stage='target_precision',failure_kind='target_precision',part_id='B',
                    node_path='artificial/B',attempts=[],output_role='display')
            return actual_target(*args,**kwargs)
        monkeypatch.setattr(validity,'target_mesh',target)
    expected=dict(validation_mode=mode,mm_per_unit=1.,fit_offset_mm=.1,final_size_mm=[4.,4.,8.])
    manifest=exporter.export_assembly(assembly,output,source_sha256=source_hash,expected=expected)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==source_hash
    row.update(entry='export_assembly',actual='normal_control' if case in {'W1','W2'} else 'injected_feedback',
               validation_mode=mode,glb_write_calls=calls,manifest=manifest,source_unchanged=True)
    parts={p['id']:p for p in manifest['parts']}
    if case in {'W1','W2'}:
        assert manifest['status']==('PASS' if case=='W1' else 'NOT_EVALUATED')
        assert manifest['manufacturing_status']==manifest['display_status']==manifest['export_status']=='PASS'
        assert Counter(calls)==Counter(['A.glb','B.glb','scene.glb','exploded.glb'])
        assert manifest['diagnostic']['display_available'] and not manifest['diagnostic']['diagnostic_only']
        assert not (output/'diagnostic.glb').exists()
        for p in parts.values():
            assert p['file_validation']['status']=='PASS' and p['file_validation']['file']==p['glb']
            assert p['display_status']=='PASS' and p['display_complete']
            _,_,readback=validity.validate_written_mesh(output/p['stl'])
            assert readback['status']=='PASS'
        row['workflow_stub']=workflow_stub(tmp_path/'stub_flow',source,manifest,monkeypatch,expect_approved=True)
    elif case in {'W3','W4'}:
        assert input_state()==input_snapshot
        assert failure_resource_checks and display_resource_checks
        row.update(assembly_inputs_unchanged=True,failed_asset_unchanged=True,
                   failure_resource_checks=failure_resource_checks,
                   display_resource_checks=display_resource_checks)
        assert manifest['manufacturing_status']=='FAIL' and manifest['export_status']=='FAIL'
        failed='B' if case=='W3' else 'A'
        assert failed not in parts
        assert any(f['part_id']==failed and f['code']=='BOOLEAN_EVALUATION_FAILED'
                   and f['diagnostic']['node_path']=='artificial/union' for f in manifest['failures'])
        assert manifest['diagnostic']['diagnostic_only']
        shown={p['id'] for p in manifest['diagnostic']['parts'] if p['shown']}
        assert 'A' in shown
        if case=='W3':
            assert (output/'A.stl').exists() and not (output/'B.stl').exists()
            assert 'B' in manifest['diagnostic']['missing_parts']
        else:
            assert not (output/'A.stl').exists() and (output/'B.stl').exists()
            monkeypatch.setattr(exporter,'evaluated',real_evaluated)
            rerun=exporter.export_assembly(assembly,tmp_path/'recovered',source_sha256=source_hash,expected=expected)
            assert rerun['status']==rerun['manufacturing_status']=='PASS'
            assert input_state()==input_snapshot
            row['new_directory_recovery_status']=rerun['status']
        row['workflow_stub']=workflow_stub(tmp_path/'stub_flow',source,manifest,monkeypatch,expect_approved=False)
    else:
        assert manifest['manufacturing_status']=='PASS' and manifest['display_status']==manifest['export_status']=='FAIL'
        for p in parts.values():
            assert (output/p['stl']).exists()
            assert validity.validate_written_mesh(output/p['stl'])[2]['status']=='PASS'
        if case=='W5':
            assert manifest['scene_glb'] is None and not manifest['diagnostic']['display_available']
            assert all(p['file_validation']['status']=='PASS' and p['display_complete'] for p in parts.values())
            assert any(r['file']=='scene.glb' and r['status']=='FAIL' for r in manifest['export_consistency'])
        elif case=='W6':
            assert manifest['exploded_glb'] is None and manifest['scene_glb']=='scene.glb'
            assert manifest['diagnostic']['display_available'] and not manifest['diagnostic']['diagnostic_only']
            assert Counter(calls)==Counter(['A.glb','B.glb','scene.glb','exploded.glb'])
            assert all(p['file_validation']['status']=='PASS' and p['display_complete'] for p in parts.values())
        else:
            assert parts['A']['file_validation']['status']=='PASS'
            assert parts['B']['file_validation']['status']!='PASS' and not parts['B']['display_complete']
            assert any(f['part_id']=='B' and f['output_role']=='display'
                       and f['code']=='TARGET_PRECISION_UNREPRESENTABLE' for f in manifest['display_failures'])
            row['workflow_stub']=workflow_stub(tmp_path/'stub_flow',source,manifest,monkeypatch,
                expect_approved=False,no_change=True)
        if case=='W5':
            row['workflow_stub']=workflow_stub(tmp_path/'stub_flow',source,manifest,monkeypatch,expect_approved=False)
    assert validity.mesh_metrics(*arrays(uniform(mf.Manifold.cube((1,1,1))))[:2])['valid']


@pytest.mark.parametrize('case_id',CASES,ids=CASES)
def test_artificial_mesh_repair(case_id,tmp_path,monkeypatch):
    assert os.environ.get('ADSL_TEST_FIXED_REAL')=='1', 'native smoke requires explicit CPU flag'
    import bpy
    bpy.ops.wm.read_factory_settings(use_empty=True)
    start=time.monotonic()
    row={'case_id':case_id,'expected':'reject' if case_id in REJECT else
         'injected_export_feedback' if case_id.startswith('W') and case_id not in {'W1','W2'} else 'valid',
         'historical_assets_read':False,'real_model_calls':0,'checker_processes':0,'render_calls':0}
    try:
        if case_id.startswith('I') or case_id in {'K2','N4','N5'}:
            input_case(case_id,row)
        elif case_id.startswith(('T','K','N')):
            precision_case(case_id,row,monkeypatch)
        else:
            export_case(case_id,row,tmp_path,monkeypatch)
        row['test_status']='PASS'
    except Exception as error:
        row['test_status']='FAIL'
        row['error']={'type':type(error).__name__,'message':str(error),
                      'diagnostic':getattr(error,'diagnostic',None)}
        raise
    finally:
        row['elapsed_seconds']=time.monotonic()-start
        serialized=json.dumps(row,allow_nan=False,indent=2)
        (tmp_path/'smoke_record.json').write_text(serialized)
        if os.environ.get('ADSL_MESH_SMOKE_EVIDENCE_DIR'):
            evidence=Path(os.environ['ADSL_MESH_SMOKE_EVIDENCE_DIR']);evidence.mkdir(parents=True,exist_ok=True)
            (evidence/(case_id+'.json')).write_text(serialized)
        bpy.ops.wm.read_factory_settings(use_empty=True)
