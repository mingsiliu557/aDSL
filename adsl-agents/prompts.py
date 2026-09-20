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
    "engineering_critic": "critic_engineering.md",
}


def object_prompt(role: str, *, articulation: bool, fixed_assembly: bool = False) -> str:
    try:
        filename = ROLE_PROMPTS[role]
    except KeyError as exc:
        raise ValueError(f"Unknown object prompt role: {role}") from exc
    prompt = render_prompt_resource(
        PROMPT_ROOT / filename,
        articulation=articulation,
    )
    if fixed_assembly and role == 'code_critic':
        # API reference, not Planner/Coder assignments or manufacturing approval.
        prompt += '\n' + files('adsl.core').joinpath('docs/fixed_assembly.md').read_text(encoding='utf-8')
    elif fixed_assembly and role != 'image_critic':
        prompt += '\n' + (PROMPT_ROOT / 'fixed_assembly.md').read_text(encoding='utf-8')
    return prompt


__all__ = ["object_prompt"]
