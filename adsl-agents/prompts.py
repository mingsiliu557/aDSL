from __future__ import annotations

from pathlib import Path
from importlib.resources import files

from .utils.prompts import render_prompt_resource


PROMPT_ROOT = Path(__file__).resolve().parent / "prompt"
ROLE_PROMPTS = {
    "planner": "planner.md",
    "edit_planner": "edit_planner.md",
    "coder": "coder.md",
    "debugger": "debugger.md",
    "image_critic": "critic_image.md",
    "code_critic": "critic_code_image.md",
    "image_critic_review": "critic_image_review.md",
    "code_critic_review": "critic_code_review.md",
    "engineering_critic": "critic_engineering.md",
}


_SOURCE_CANDIDATE_READING = (
    'Source candidates are location hints, not confirmed causes. Their inline entries may omit '
    'detailed locations and evidence. Read preview_info.source_candidates.full_ref with read_file '
    'and a JSON pointer only when those details are needed. Use bounded pages; do not load every '
    'candidate report. Existing repair permissions and checker requirements remain unchanged.'
)
_STANDING_EVALUATION = (
    'Topology and standing are separate evaluations. An initial contact-depth warning '
    'alone does not invalidate the standing observation and is not a mandatory geometry '
    'repair. A standing pass does not override topology failures or prove physical '
    'manufacturability. Unavailable standing evidence is not observed tipping. '
    'Read checker_summary and its result_ref/result_pointer for details; historical '
    'checker summaries retain their original source binding and are not current measurements.'
)


def object_prompt(role: str, *, articulation: bool, fixed_assembly: bool = False,
                  support_metric: str = 'area', shape_editable: bool = False, print_mode: str = 'split') -> str:
    try:
        filename = ROLE_PROMPTS[role]
    except KeyError as exc:
        raise ValueError(f"Unknown object prompt role: {role}") from exc
    prompt = render_prompt_resource(
        PROMPT_ROOT / filename,
        articulation=articulation,
    )
    if fixed_assembly and role in {'code_critic', 'code_critic_review', 'engineering_critic'}:
        # API reference, not Planner/Coder assignments or manufacturing approval.
        prompt += '\n' + files('adsl.core').joinpath('docs/fixed_assembly.md').read_text(encoding='utf-8')
    elif fixed_assembly and role not in {'image_critic', 'image_critic_review'}:
        prompt += '\n' + (PROMPT_ROOT / 'fixed_assembly.md').read_text(encoding='utf-8')
    if fixed_assembly and support_metric == 'nsv':
        prompt = _support_prompt(prompt, shape_editable=shape_editable, print_mode=print_mode)
    if fixed_assembly and role in {'coder', 'engineering_critic', 'code_critic', 'code_critic_review'}:
        prompt += '\n' + _SOURCE_CANDIDATE_READING
        prompt += '\n' + _STANDING_EVALUATION
    if fixed_assembly and print_mode == 'single':
        prompt = _single_prompt(prompt)
    if fixed_assembly and print_mode != 'single' and support_metric == 'nsv' and role == 'planner':
        prompt += '\n' + (PROMPT_ROOT / 'support_grouping.md').read_text(encoding='utf-8')
    return prompt


__all__ = ["object_prompt"]


def support_prompt_options(fixed_assembly):
    from .support_volume import support_options
    metric, shape = support_options((fixed_assembly or {}).get('physics',{}).get('overhang',{}))
    # Keep default callers' argument sets and serialized configuration unchanged.
    from .fixed_assembly import print_mode
    options = {'support_metric':metric, 'shape_editable':shape} if metric == 'nsv' else {}
    if print_mode(fixed_assembly) == 'single':
        options['print_mode'] = 'single'
    return options


def _support_prompt(prompt, *, shape_editable, print_mode='split'):
    # Replace only known policy paragraphs, retaining all modelling API material.
    spans = [
        ('When partition_objective is enabled, the checker searches 24 print rotations and',
         'Appearance API limits for generated source and repair:'),
        ('Partition scoring uses the Dapper-form', 'When partition_intent is present,'),
        ('## Optional partition objective', '### Appearance controls'),
        ('For an initial partition_optimization proposal, use regroup_print_parts and a valid',
         'scores alone are not a reason to decline. Never invent a measured improvement.'),
    ]
    for start, end in spans:
        if start in prompt:
            begin = prompt.index(start)
            if end not in prompt[begin:]:
                raise ValueError('NSV prompt policy boundary unavailable: ' + start)
            finish = prompt.index(end, begin)
            if start.startswith('For an initial partition_optimization'):
                finish += len(end)
            prompt = prompt[:begin] + prompt[finish:]
    prompt = prompt.replace(
        'Legacy overhang optimization permits only\n  this direction change.',
        'Print rotation permission remains separate from shape-edit permission.')
    prompt = prompt.replace('Never delete required body material or change measurement settings.',
                            'Keep required semantic features and frozen measurement settings.')
    prompt = prompt.replace('This example does not promise a higher partition score.',
                            'This example does not promise lower support requirements.')
    policy = (PROMPT_ROOT/'support_optimization.md').read_text(encoding='utf-8')
    permission = ('Authorized optional shape actions: reshape, resize, add_local_structure without grouping_change; '
                  'do not alter grouping unless a separate regroup is authorized.' if shape_editable else
                  'Optional shape changes for support reduction are disabled. Preserve pre-connector body shape '
                  'for optional regroup/rotation. Necessary repairs keep their original evidence-based permissions.')
    if print_mode == 'single':
        policy = policy[:policy.index('With a partition objective,')]
        policy = policy.replace('A split or same-count edit requires', 'A same-count edit requires').replace('; a merge requires\ndelta_S <= tau AND delta_S_hat <= tau_hat.', '.')
        permission = 'Authorized actions: resize, reshape, relayout, add_local_structure; preserve the single print-part identity.'
    return prompt + '\n' + policy + '\n' + permission


def fixed_assembly_for_agent(config):
    """Remove the alternate score objective from NSV model inputs only."""
    if support_prompt_options(config).get('support_metric') != 'nsv':
        return config
    from copy import deepcopy
    result = deepcopy(config)
    overhang = result.get('physics',{}).get('overhang',{})
    if 'partition_objective' in overhang:
        overhang['partition_objective_enabled'] = overhang.pop('partition_objective') is not None
    return result


_SINGLE_PRINT_INSTRUCTION = (
    'print_mode=single: put ALL semantic components in exactly one print part whose ID is plan.root_part; '
    'declare no connections. Keep the program-declared print rotation identity. '
    'Never propose regroup_print_parts or change_print_orientation. If orientation editing is enabled, '
    'the checker automatically chooses the minimum-support pose among 24 rotations; otherwise printing stays upright. '
    'The Agent may only resize, reshape, relayout or add_local_structure within the same print part. '
    'Preserve semantic features, fixed scale, root frame and all checker requirements.')


def _single_prompt(prompt):
    # Remove only policy sections that authorize regrouping or source rotations.
    # General API documentation remains reference material, not permission.
    spans = [
        ('Planner:\n', 'Copy mm_per_unit'),
        ('- `assembly.set_print_orientation', '\n\nRead the connection list'),
        ('During repair, the initial print_parts', 'MATE_FRAME_MISMATCH'),
        ('When partition_objective is enabled,', 'Appearance API limits for generated source and repair:'),
        ('Partition scoring uses the Dapper-form', 'When partition_intent is present,'),
        ('## Optional partition objective', '### Appearance controls'),
    ]
    for start, end in spans:
        if start in prompt:
            begin = prompt.index(start)
            if end not in prompt[begin:]:
                raise ValueError('single prompt policy boundary unavailable: ' + start)
            prompt = prompt[:begin] + prompt[prompt.index(end, begin):]
    for start in ('### Implementing a regroup proposal', 'When partition_intent is present,'):
        if start in prompt:
            # These terminal API-reference sections describe unavailable split operations.
            begin = prompt.index(start)
            ends = [prompt.find(marker, begin) for marker in ('Support optimization uses', _SOURCE_CANDIDATE_READING, _STANDING_EVALUATION)]
            end = min((i for i in ends if i >= 0), default=-1)
            prompt = prompt[:begin] + (prompt[end:] if end >= 0 else '')
    return prompt + '\n' + _SINGLE_PRINT_INSTRUCTION
