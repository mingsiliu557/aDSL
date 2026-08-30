from __future__ import annotations

from adsl.agents.prompts import object_prompt


def test_coder_prompt_requires_public_core_import() -> None:
    prompt = object_prompt("coder", articulation=False)

    assert "Start every generated program with `from adsl.core import *`" in prompt
    assert "from adsl import *" not in prompt
