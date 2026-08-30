from __future__ import annotations

import asyncio
import json
from pathlib import Path

from agents import Agent, Runner, function_tool
from pydantic import BaseModel

from adsl.agents.providers.codex_cli import CodexCliModel, ProcessResult


class FinalDecision(BaseModel):
    value: str


def test_agents_sdk_tool_loop_and_typed_output(tmp_path) -> None:
    observed: list[str] = []

    @function_tool
    def remember(value: str) -> str:
        """Remember one value."""

        observed.append(value)
        return f"remembered {value}"

    turns = [
        {
            "content": "",
            "thought_summary": "call the harness tool",
            "tool_calls": [{"name": "remember", "arguments": {"value": "alpha"}}],
            "final_output": None,
        },
        {
            "content": "",
            "thought_summary": "return typed result",
            "tool_calls": [],
            "final_output": {"value": "complete"},
        },
    ]

    async def fake_runner(argv, *, stdin, cwd, timeout):
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text(json.dumps(turns.pop(0)), encoding="utf-8")
        return ProcessResult(
            0,
            json.dumps(
                {
                    "type": "turn.completed",
                    "usage": {"input_tokens": 4, "output_tokens": 2},
                }
            )
            + "\n",
            "",
        )

    model = CodexCliModel(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        timeout=10,
        max_prompt_chars=100_000,
        repository_root=tmp_path,
        diagnostics_root=tmp_path,
        binary="/bin/true",
        process_runner=fake_runner,
        preflight=False,
    )
    agent = Agent(
        name="contract-test",
        instructions="Use remember, then finish.",
        model=model,
        tools=[remember],
        output_type=FinalDecision,
    )
    result = asyncio.run(Runner.run(agent, "start", max_turns=3))
    assert observed == ["alpha"]
    assert result.final_output == FinalDecision(value="complete")
    assert result.context_wrapper.usage.requests == 2
