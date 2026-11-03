"""Image-only size planning using the existing single Planner call.

This experiment subclass does not change ObjectWorkflow or its physical rules.
"""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import math
from pathlib import Path

from adsl.agents.models import FixedAssemblyPlan
from adsl.agents.service import ObjectWorkflow
from adsl.agents.prompts import object_prompt, support_prompt_options, fixed_assembly_for_agent
from adsl.agents.utils.inputs import user_input
from adsl.agents.utils.io import write_json, read_json

TASK = ('Create the complete object shown in the reference image. '
        'Preserve its main proportions, visible components and intended pose. '
        'Do not add bases or stands absent from the image. '
        'Use right-handed Z-up coordinates and millimetres. '
        'Use a nominal longest dimension of 150 mm, estimating the other dimensions from the image.')
COPY_SIZE = ('Copy mm_per_unit and final_size_mm from the frozen request, not from a guessed\n'
             'bounding box.')
PLAN_SIZE = ('Copy mm_per_unit from the request. For this image-only task no three-axis size is supplied. '
             'Estimate the object dimensions in its intended pose from the image, preserve its proportions, '
             'and return positive final_size_mm with the longest dimension 150 mm. '
             'These are image-derived nominal dimensions, not measured or ground-truth dimensions. '
             'The controller will normalize the longest dimension to 150 mm and freeze the result '
             'before Coder and all subsequent checks and repairs.')


def normalize_size(values):
    if len(values) != 3 or any(isinstance(x, bool) or not math.isfinite(x) or x <= 0 for x in values):
        raise ValueError('Planner dimensions must be three finite positive values')
    result = [150. * (x / max(values)) for x in values]
    if any(x <= 0 or not math.isfinite(x) for x in result):
        raise ValueError('Normalized dimensions are invalid')
    return result


def input_binding(request):
    return dict(requirement=request.requirement,
                image_sha256=hashlib.sha256(Path(request.image_paths[0]).read_bytes()).hexdigest(),
                fixed_assembly={k:v for k,v in request.fixed_assembly.items() if k != 'final_size_mm'},
                max_rounds=request.max_rounds)


class ImageOnlyWorkflow(ObjectWorkflow):
    @staticmethod
    def _validate_request(request):
        if request.requirement != TASK or len(request.image_paths) != 1 or not request.fixed_assembly:
            raise ValueError('This adapter requires the uniform image-only task and one reference image')
        # Validate all existing manufacturing constraints. The temporary size is
        # used only by this schema check, never persisted or sent to an Agent.
        config = deepcopy(request.fixed_assembly)
        config.setdefault('final_size_mm', [1., 1., 1.])
        ObjectWorkflow._validate_request(replace(request, fixed_assembly=config))

    async def generate(self, request):
        if 'final_size_mm' in request.fixed_assembly:
            raise ValueError('Image-only generation must not import per-case dimensions')
        return await super().generate(replace(request, fixed_assembly=deepcopy(request.fixed_assembly)))

    async def _plan_generation(self, runtime, request):
        instructions = object_prompt('planner', articulation=request.articulation, fixed_assembly=True,
                                     **support_prompt_options(request.fixed_assembly))
        if instructions.count(COPY_SIZE) != 1:
            raise ValueError('Planner size instruction changed; review image-only adapter')
        instructions = instructions.replace(COPY_SIZE, PLAN_SIZE)
        planner = runtime.agent(name='object-planner', instructions=instructions, output_type=FixedAssemblyPlan)
        payload = dict(requirement=request.requirement,
                       fixed_assembly=fixed_assembly_for_agent(request.fixed_assembly))
        assert 'final_size_mm' not in payload['fixed_assembly']
        from adsl.agents.fixed_assembly import print_mode, check_single_plan
        single = print_mode(request.fixed_assembly) == 'single'
        content = json.dumps(payload)
        for attempt in range(2 if single else 1):
            result = await runtime.run(agent=planner, input=user_input(content, request.image_paths),
                                       role='planner', stage='plan')
            plan = self._typed_output(result.final_output, FixedAssemblyPlan)
            if plan.mm_per_unit != request.fixed_assembly['mm_per_unit']:
                raise ValueError('Planner changed frozen units')
            raw_size = list(plan.final_size_mm)
            plan = plan.model_copy(update={'final_size_mm': normalize_size(raw_size)})
            if single:
                try:
                    check_single_plan(plan)
                except ValueError as error:
                    if attempt:
                        raise
                    content += '\n' + str(error)
                    continue
            break
        request.fixed_assembly['final_size_mm'] = list(plan.final_size_mm)
        write_json(request.workspace/'image_only_size_freeze.json', dict(
            protocol='image_only_planner_size_v1', input_binding=input_binding(request),
            raw_planner_size_mm=raw_size, final_size_mm=plan.final_size_mm,
            fixed_assembly=request.fixed_assembly, source='same initial image-only Planner call',
            longest_dimension_mm=150., dataset_caption_used=False, gt_dimensions_used=False))
        return plan

    async def resume(self, request):
        request = replace(request, fixed_assembly=deepcopy(request.fixed_assembly))
        frozen = request.workspace/'image_only_size_freeze.json'
        if frozen.exists():
            record = read_json(frozen)
            if record['input_binding'] != input_binding(request):
                raise ValueError('Image-only resume input binding mismatch')
            size = record['final_size_mm']
            if len(size) != 3 or any(not math.isfinite(x) or x <= 0 for x in size) or max(size) != 150.:
                raise ValueError('Invalid frozen dimensions')
            plan_path = request.workspace/'plan.json'
            if not plan_path.exists() or read_json(plan_path)['final_size_mm'] != size:
                raise ValueError('Frozen size and saved plan mismatch')
            request.fixed_assembly['final_size_mm'] = size
        elif (request.workspace/'plan.json').exists():
            raise ValueError('Saved plan lacks image-only dimension binding')
        return await super().resume(request)
