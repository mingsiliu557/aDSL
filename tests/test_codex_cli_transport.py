from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
from agents import ModelSettings
from agents.models.interface import ModelTracing

from adsl.agents.providers.codex_cli import CodexCliError, CodexCliModel, ProcessResult


def _invoke(model: CodexCliModel, text: str = "hello"):
    return asyncio.run(
        model.get_response(
            "Be concise.",
            text,
            ModelSettings(),
            [],
            None,
            [],
            ModelTracing.DISABLED,
            previous_response_id=None,
            conversation_id=None,
            prompt=None,
        )
    )


def test_command_output_usage_and_sanitized_diagnostics(tmp_path) -> None:
    seen: dict[str, object] = {}

    async def fake_runner(argv, *, stdin, cwd, timeout):
        seen.update(argv=argv, stdin=stdin, cwd=cwd, timeout=timeout)
        output_path = Path(argv[argv.index("--output-last-message") + 1])
        output_path.write_text(
            json.dumps(
                {
                    "content": "done",
                    "thought_summary": "finished",
                    "tool_calls": [],
                    "final_output": None,
                }
            ),
            encoding="utf-8",
        )
        stdout = json.dumps(
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 12,
                    "cached_input_tokens": 2,
                    "output_tokens": 5,
                    "reasoning_output_tokens": 1,
                },
            }
        )
        return ProcessResult(0, stdout + "\n", "")

    model = CodexCliModel(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        timeout=9,
        max_prompt_chars=50_000,
        repository_root=tmp_path,
        diagnostics_root=tmp_path,
        binary="/bin/true",
        process_runner=fake_runner,
        preflight=False,
    )
    response = _invoke(model)
    argv = seen["argv"]
    assert argv[:3] == ["/usr/bin/true", "exec", "--ephemeral"]
    for flag in [
        "--ignore-user-config",
        "--ignore-rules",
        "--json",
        "--output-schema",
        "--output-last-message",
    ]:
        assert flag in argv
    assert argv[argv.index("--sandbox") + 1] == "read-only"
    assert argv[argv.index("--model") + 1] == "gpt-5.6-sol"
    assert response.usage.total_tokens == 17
    diagnostic = next((tmp_path / "codex_cli").iterdir())
    metadata = json.loads((diagnostic / "metadata.json").read_text())
    assert metadata["prompt_chars"] > 0
    assert "prompt" not in metadata
    assert not any("adsl-codex-" in value for value in metadata["command"])


@pytest.mark.parametrize(
    ("result", "message"),
    [
        (ProcessResult(-1, "", "", timed_out=True), "timed out"),
        (ProcessResult(2, "", "authentication failed"), "status 2"),
    ],
)
def test_process_failures_are_clear(tmp_path, result: ProcessResult, message: str) -> None:
    async def fake_runner(argv, *, stdin, cwd, timeout):
        return result

    model = CodexCliModel(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        timeout=1,
        max_prompt_chars=50_000,
        repository_root=tmp_path,
        diagnostics_root=tmp_path,
        binary="/bin/true",
        process_runner=fake_runner,
        preflight=False,
    )
    with pytest.raises(CodexCliError, match=message):
        _invoke(model)


def test_prompt_limit_fails_without_invoking_process(tmp_path) -> None:
    called = False

    async def fake_runner(argv, *, stdin, cwd, timeout):
        nonlocal called
        called = True
        return ProcessResult(0, "", "")

    model = CodexCliModel(
        model="gpt-5.6-sol",
        reasoning_effort="high",
        timeout=1,
        max_prompt_chars=10,
        repository_root=tmp_path,
        diagnostics_root=tmp_path,
        binary="/bin/true",
        process_runner=fake_runner,
        preflight=False,
    )
    with pytest.raises(CodexCliError, match="refusing to truncate"):
        _invoke(model, "long input")
    assert not called


def test_missing_binary_fails_at_construction(tmp_path) -> None:
    with pytest.raises(CodexCliError, match="executable not found"):
        CodexCliModel(
            model="gpt-5.6-sol",
            reasoning_effort="high",
            timeout=1,
            max_prompt_chars=10,
            repository_root=tmp_path,
            diagnostics_root=tmp_path,
            binary="definitely-no-such-codex-binary",
        )
