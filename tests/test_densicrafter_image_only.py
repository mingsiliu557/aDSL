import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from adsl.agents.models import FixedAssemblyPlan, ObjectRequest
from experiments.densicrafter_image_only.workflow import ImageOnlyWorkflow, TASK, COPY_SIZE, normalize_size


def request(tmp_path):
    image=tmp_path/'image.png';image.write_bytes(b'image-fixture')
    return ObjectRequest(requirement=TASK,workspace=tmp_path,task_id='fixture',image_paths=(image,),
        fixed_assembly=dict(mm_per_unit=1.,fit_offset_mm=.2,validation_mode='visual_only',require_multiple_parts=False))


def plan(size=(2.,4.,6.)):
    return FixedAssemblyPlan(object_name='fixture',components=[dict(name='body',description='body')],relations=[],
        critic_checklist=[],print_parts=[dict(id='body',components=['body'])],connections=[],root_part='body',
        mm_per_unit=1.,final_size_mm=list(size))


def test_one_planner_call_freezes_image_derived_size(tmp_path):
    req=request(tmp_path);seen={}
    def agent(**kwargs):seen.update(kwargs);return object()
    runtime=SimpleNamespace(agent=agent,run=AsyncMock(return_value=SimpleNamespace(final_output=plan())))
    w=ImageOnlyWorkflow();result=asyncio.run(w._plan_generation(runtime,req))
    assert runtime.run.await_count==1
    call=runtime.run.call_args.kwargs
    assert call['role']=='planner' and call['stage']=='plan'
    assert '"final_size_mm"' not in str(call['input'])
    assert COPY_SIZE not in seen['instructions']
    assert result.final_size_mm==[50.,100.,150.]
    assert req.fixed_assembly['final_size_mm']==[50.,100.,150.]
    assert (tmp_path/'image_only_size_freeze.json').exists()


@pytest.mark.parametrize('size', [[0,1,2],[-1,1,2],[float('nan'),1,2],[float('inf'),1,2],[1,2],[True,2,3]])
def test_invalid_dimensions_rejected(size):
    with pytest.raises(ValueError):normalize_size(size)


def test_validation_does_not_insert_placeholder(tmp_path):
    req=request(tmp_path);ImageOnlyWorkflow._validate_request(req)
    assert 'final_size_mm' not in req.fixed_assembly


def test_resume_rejects_different_reference(tmp_path):
    req=request(tmp_path);runtime=SimpleNamespace(agent=lambda **kw:object(),run=AsyncMock(return_value=SimpleNamespace(final_output=plan())))
    w=ImageOnlyWorkflow();asyncio.run(w._plan_generation(runtime,req));req.image_paths[0].write_bytes(b'changed')
    with pytest.raises(ValueError,match='binding mismatch'):asyncio.run(w.resume(req))


def test_generate_freezes_before_coder_without_mutating_caller(tmp_path, monkeypatch):
    from adsl.agents.service import ObjectWorkflow
    req=request(tmp_path);runtime=SimpleNamespace(agent=lambda **kw:object(),run=AsyncMock(return_value=SimpleNamespace(final_output=plan())))
    async def native_generate(self, actual):
        self._validate_request(actual)
        planned=await self._plan_generation(runtime,actual)
        assert actual.fixed_assembly['final_size_mm']==planned.final_size_mm
        return actual.fixed_assembly
    monkeypatch.setattr(ObjectWorkflow,'generate',native_generate)
    result=asyncio.run(ImageOnlyWorkflow().generate(req))
    assert result['final_size_mm']==[50.,100.,150.]
    assert 'final_size_mm' not in req.fixed_assembly


def test_resume_reuses_frozen_plan_without_new_planner_call(tmp_path,monkeypatch):
    from adsl.agents.service import ObjectWorkflow
    from adsl.agents.utils.io import write_json
    req=request(tmp_path);runtime=SimpleNamespace(agent=lambda **kw:object(),run=AsyncMock(return_value=SimpleNamespace(final_output=plan((1.12,2.33,4.74)))))
    w=ImageOnlyWorkflow();planned=asyncio.run(w._plan_generation(runtime,req));write_json(tmp_path/'plan.json',planned.model_dump())
    parent=AsyncMock(return_value='resumed');monkeypatch.setattr(ObjectWorkflow,'resume',parent)
    assert asyncio.run(w.resume(req))=='resumed'
    assert parent.call_args.args[0].fixed_assembly['final_size_mm']==planned.final_size_mm
    assert runtime.run.await_count==1
