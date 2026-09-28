from __future__ import annotations

from adsl.agents.prompts import object_prompt


def test_coder_prompt_requires_public_core_import() -> None:
    prompt = object_prompt("coder", articulation=False)

    assert "Start every generated program with `from adsl.core import *`" in prompt
    assert "from adsl import *" not in prompt


def test_static_review_prompts_grade_current_evidence_and_only_append_connector_reference():
    image = object_prompt('image_critic_review', articulation=False)
    code = object_prompt('code_critic_review', articulation=False)
    assert 'HIGH' in image and 'MED/LOW' in image
    assert 'current renders' in image and 'Reassess' in image
    assert 'every dismissal or downgrade' in code and 'visible view evidence' in code
    assert 'MUST TRUST THE CODE LOGIC' not in code
    assert object_prompt('image_critic_review', articulation=False, fixed_assembly=True) == image
    assembly_code = object_prompt('code_critic_review', articulation=False, fixed_assembly=True)
    assert assembly_code.startswith(code)
    assert 'M_child = M_receiver @ F_slot @ inverse(F_tab)' in assembly_code
    assert 'Planner: return FixedAssemblyPlan' not in assembly_code
    assert 'Generate one complete program' not in assembly_code
