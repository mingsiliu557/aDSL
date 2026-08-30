from __future__ import annotations

import base64
import json

import pytest
from agents import function_tool
from agents.agent_output import AgentOutputSchema
from agents.exceptions import ModelBehaviorError
from agents.usage import Usage
from pydantic import BaseModel

from adsl.agents.providers.codex_codec import (
    decode_response,
    encode_request,
    usage_from_events,
)


@function_tool
def write_source(path: str, content: str, note: str | None = None) -> str:
    """Write test source."""

    return path


class Decision(BaseModel):
    accepted: bool
    reason: str


def _payload(**updates: object) -> str:
    payload: dict[str, object] = {
        "content": "",
        "thought_summary": "test",
        "tool_calls": [],
        "final_output": None,
    }
    payload.update(updates)
    return json.dumps(payload)


def test_tool_arguments_remain_one_json_object(tmp_path) -> None:
    encoded = encode_request(
        system_instructions="Use the tool.",
        input="write a file",
        tools=[write_source],
        output_schema=None,
        temporary_directory=tmp_path,
    )
    source = "value = \"quoted\"\npath = r'C:\\\\scene\\\\asset'\n" * 20
    response = decode_response(
        response_text=_payload(
            tool_calls=[
                {
                    "name": "write_source",
                    "arguments": {"path": "source.py", "content": source, "note": None},
                }
            ]
        ),
        tools=encoded.tools,
        output_schema=None,
        usage=Usage(requests=1),
        raw_usage=None,
        response_id="fixed",
    )
    call = response.output[0]
    assert json.loads(call.arguments) == {"path": "source.py", "content": source}
    assert call.call_id


def test_typed_output_is_validated_and_serialized(tmp_path) -> None:
    output_schema = AgentOutputSchema(Decision)
    encoded = encode_request(
        system_instructions=None,
        input="decide",
        tools=[],
        output_schema=output_schema,
        temporary_directory=tmp_path,
    )
    response = decode_response(
        response_text=_payload(final_output={"accepted": True, "reason": "ok"}),
        tools=encoded.tools,
        output_schema=output_schema,
        usage=Usage(requests=1),
        raw_usage=None,
        response_id="typed",
    )
    assert json.loads(response.output[0].content[0].text) == {
        "accepted": True,
        "reason": "ok",
    }


@pytest.mark.parametrize(
    "response_text",
    [
        _payload(tool_calls=[{"name": "unknown", "arguments": {}}]),
        _payload(
            tool_calls=[{"name": "write_source", "arguments": "{\"path\":\"source.py\"}"}]
        ),
        _payload(
            tool_calls=[{"name": "write_source", "arguments": {"path": "source.py"}}]
        ),
        _payload(
            tool_calls=[
                {
                    "name": "write_source",
                    "arguments": {"path": "source.py", "content": "x"},
                }
            ],
            final_output={"unexpected": True},
        ),
    ],
)
def test_bad_tool_turns_fail_closed(tmp_path, response_text: str) -> None:
    encoded = encode_request(
        system_instructions=None,
        input="test",
        tools=[write_source],
        output_schema=None,
        temporary_directory=tmp_path,
    )
    with pytest.raises(ModelBehaviorError):
        decode_response(
            response_text=response_text,
            tools=encoded.tools,
            output_schema=None,
            usage=Usage(),
            raw_usage=None,
            response_id="bad",
        )


def test_data_url_images_are_deduplicated_and_removed_from_prompt(tmp_path) -> None:
    image = base64.b64encode(b"not-a-real-png-but-valid-attachment-bytes").decode()
    data_url = f"data:image/png;base64,{image}"
    encoded = encode_request(
        system_instructions=None,
        input=[
            {
                "role": "user",
                "content": [
                    {"type": "input_text", "text": "compare"},
                    {"type": "input_image", "image_url": data_url, "detail": "auto"},
                    {"type": "input_image", "image_url": data_url, "detail": "auto"},
                ],
            }
        ],
        tools=[],
        output_schema=None,
        temporary_directory=tmp_path,
    )
    assert len(encoded.image_paths) == 1
    assert image not in encoded.prompt
    assert encoded.prompt.count("attachment://attachment_1") == 2


def test_usage_uses_last_completed_turn() -> None:
    usage, raw = usage_from_events(
        [
            {"type": "turn.completed", "usage": {"input_tokens": 1, "output_tokens": 2}},
            {
                "type": "turn.completed",
                "usage": {
                    "input_tokens": 10,
                    "cached_input_tokens": 3,
                    "output_tokens": 7,
                    "reasoning_output_tokens": 4,
                },
            },
        ]
    )
    assert raw is not None
    assert usage.input_tokens == 10
    assert usage.input_tokens_details.cached_tokens == 3
    assert usage.output_tokens == 7
    assert usage.output_tokens_details.reasoning_tokens == 4
    assert usage.total_tokens == 17
