from __future__ import annotations

import base64
from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from agents.agent_output import AgentOutputSchemaBase
from agents.exceptions import ModelBehaviorError
from agents.items import ModelResponse, TResponseInputItem
from agents.tool import FunctionTool, Tool
from agents.usage import Usage
from openai.types.responses.response_function_tool_call import ResponseFunctionToolCall
from openai.types.responses.response_output_message import ResponseOutputMessage
from openai.types.responses.response_output_text import ResponseOutputText
from openai.types.responses.response_usage import InputTokensDetails, OutputTokensDetails
from pydantic import BaseModel


_DATA_IMAGE_RE = re.compile(
    r"^data:(image/(?:jpeg|png|webp|gif));base64,([A-Za-z0-9+/=\r\n]+)$"
)
_IMAGE_SUFFIXES = {
    "image/jpeg": ".jpg",
    "image/png": ".png",
    "image/webp": ".webp",
    "image/gif": ".gif",
}
_SUPPORTED_ITEM_TYPES = {
    "message",
    "function_call",
    "function_call_output",
}
_SUPPORTED_CONTENT_TYPES = {
    "input_text",
    "input_image",
    "output_text",
}


@dataclass(frozen=True)
class EncodedRequest:
    prompt: str
    output_schema: dict[str, Any]
    image_paths: tuple[Path, ...]
    tools: dict[str, FunctionTool]


def _jsonable(value: Any) -> Any:
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="json", exclude_none=True))
    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _jsonable(model_dump(mode="json", exclude_none=True))
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise TypeError(f"Unsupported SDK input value: {type(value).__name__}")


def _validate_input_items(value: str | list[TResponseInputItem]) -> Any:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        raise TypeError("Codex CLI input must be text or a Responses input-item list")
    payload = _jsonable(value)
    for index, item in enumerate(payload):
        if not isinstance(item, dict):
            raise TypeError(f"Input item {index} must be an object")
        item_type = item.get("type")
        if item_type is not None and item_type not in _SUPPORTED_ITEM_TYPES:
            raise ValueError(f"Unsupported SDK input item type: {item_type!r}")
        content = item.get("content")
        if isinstance(content, list):
            for part in content:
                if not isinstance(part, dict):
                    raise TypeError("Message content parts must be objects")
                part_type = part.get("type")
                if part_type not in _SUPPORTED_CONTENT_TYPES:
                    raise ValueError(f"Unsupported SDK content type: {part_type!r}")
    return payload


class _AttachmentWriter:
    def __init__(self, directory: Path) -> None:
        self.directory = directory
        self.by_digest: dict[str, tuple[str, Path]] = {}

    def replace(self, value: Any) -> Any:
        if isinstance(value, str):
            match = _DATA_IMAGE_RE.fullmatch(value)
            if match is None:
                return value
            mime_type, encoded = match.groups()
            try:
                content = base64.b64decode(encoded, validate=True)
            except ValueError as error:
                raise ValueError("Invalid base64 image attachment") from error
            if not content:
                raise ValueError("Image attachment must not be empty")
            digest = hashlib.sha256(content).hexdigest()
            existing = self.by_digest.get(digest)
            if existing is None:
                label = f"attachment_{len(self.by_digest) + 1}"
                path = self.directory / f"{label}{_IMAGE_SUFFIXES[mime_type]}"
                path.write_bytes(content)
                existing = (label, path)
                self.by_digest[digest] = existing
            label, _path = existing
            return f"attachment://{label}?sha256={digest}"
        if isinstance(value, list):
            return [self.replace(item) for item in value]
        if isinstance(value, dict):
            if value.get("type") == "input_image":
                image_url = value.get("image_url")
                if not isinstance(image_url, str) or not image_url.startswith("data:image/"):
                    raise ValueError("Codex CLI accepts only embedded data-URL input images")
            return {key: self.replace(item) for key, item in value.items()}
        return value

    @property
    def paths(self) -> tuple[Path, ...]:
        return tuple(path for _label, path in self.by_digest.values())


def _json_pointer(root: dict[str, Any], reference: str) -> Any:
    if not reference.startswith("#/"):
        raise ValueError(f"Only local JSON Schema references are supported: {reference}")
    current: Any = root
    for encoded_part in reference[2:].split("/"):
        part = encoded_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, dict) or part not in current:
            raise ValueError(f"Broken JSON Schema reference: {reference}")
        current = current[part]
    return current


def inline_local_refs(schema: dict[str, Any]) -> dict[str, Any]:
    """Inline local refs so a nested tool/output schema cannot point at the outer root."""

    root = deepcopy(schema)

    def visit(value: Any, stack: tuple[str, ...] = ()) -> Any:
        if isinstance(value, list):
            return [visit(item, stack) for item in value]
        if not isinstance(value, dict):
            return value
        if "$ref" in value:
            reference = value["$ref"]
            if not isinstance(reference, str):
                raise ValueError("JSON Schema $ref must be a string")
            if reference in stack:
                raise ValueError("Recursive JSON Schema references are not supported")
            resolved = deepcopy(_json_pointer(root, reference))
            siblings = {key: item for key, item in value.items() if key != "$ref"}
            if siblings:
                resolved = {"allOf": [resolved], **siblings}
            return visit(resolved, (*stack, reference))
        return {
            key: visit(item, stack)
            for key, item in value.items()
            if key not in {"$defs", "definitions"}
        }

    result = visit(root)
    if not isinstance(result, dict):
        raise TypeError("JSON Schema root must be an object")
    return result


def _tool_branch(tool: FunctionTool) -> dict[str, Any]:
    arguments_schema = inline_local_refs(tool.params_json_schema)
    return {
        "type": "object",
        "properties": {
            "name": {"type": "string", "const": tool.name},
            "arguments": arguments_schema,
        },
        "required": ["name", "arguments"],
        "additionalProperties": False,
    }


def response_schema(
    tools: list[FunctionTool],
    output_schema: AgentOutputSchemaBase | None,
) -> dict[str, Any]:
    branches = [_tool_branch(tool) for tool in tools]
    if branches:
        tool_items: dict[str, Any] = {"anyOf": branches}
    else:
        tool_items = {
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        }
    if output_schema is not None and not output_schema.is_plain_text():
        final_schema: dict[str, Any] = {
            "anyOf": [inline_local_refs(output_schema.json_schema()), {"type": "null"}]
        }
    else:
        final_schema = {"type": "null"}
    return {
        "type": "object",
        "properties": {
            "content": {"type": "string"},
            "thought_summary": {"type": "string"},
            "tool_calls": {
                "type": "array",
                "items": tool_items,
                "maxItems": 1,
            },
            "final_output": final_schema,
        },
        "required": ["content", "thought_summary", "tool_calls", "final_output"],
        "additionalProperties": False,
    }


def encode_request(
    *,
    system_instructions: str | None,
    input: str | list[TResponseInputItem],
    tools: list[Tool],
    output_schema: AgentOutputSchemaBase | None,
    temporary_directory: Path,
) -> EncodedRequest:
    function_tools: list[FunctionTool] = []
    for tool in tools:
        if not isinstance(tool, FunctionTool):
            raise ValueError(f"Unsupported hosted or non-function tool: {type(tool).__name__}")
        if not tool.strict_json_schema:
            raise ValueError(f"Codex CLI requires a strict schema for tool {tool.name!r}")
        function_tools.append(tool)
    names = [tool.name for tool in function_tools]
    if len(set(names)) != len(names):
        raise ValueError("Function tool names must be unique")

    conversation = _validate_input_items(input)
    attachment_writer = _AttachmentWriter(temporary_directory)
    conversation = attachment_writer.replace(conversation)
    schema = response_schema(function_tools, output_schema)
    tool_descriptions = [
        {
            "name": tool.name,
            "description": tool.description,
            "arguments_schema": inline_local_refs(tool.params_json_schema),
        }
        for tool in function_tools
    ]
    typed = output_schema is not None and not output_schema.is_plain_text()
    prompt = "\n\n".join(
        [
            """You are the model transport inside the aDSL agent harness. Produce exactly one assistant turn matching the supplied output JSON schema. Do not run shell commands, edit files, or use native Codex tools. Any file operation must be requested through exactly one listed aDSL function tool, and the harness will execute it after this turn.

Choose one mode only:
- Tool mode: return one tool_calls entry and set final_output to null.
- Typed final mode: when a typed result is requested, return no tool calls and put the object directly in final_output.
- Text final mode: when no typed result is requested, return no tool calls, set final_output to null, and put the answer in content.

Tool arguments must be JSON objects, never JSON-encoded strings. thought_summary is diagnostic only and must be concise.""",
            "SYSTEM INSTRUCTIONS\n" + (system_instructions or ""),
            "AVAILABLE ADSL TOOLS\n"
            + json.dumps(tool_descriptions, ensure_ascii=False, separators=(",", ":")),
            "TYPED FINAL OUTPUT REQUIRED\n" + ("yes" if typed else "no"),
            "CONVERSATION (Responses input format; attachment:// values correspond to --image files)\n"
            + json.dumps(conversation, ensure_ascii=False, separators=(",", ":")),
        ]
    )
    return EncodedRequest(
        prompt=prompt,
        output_schema=schema,
        image_paths=attachment_writer.paths,
        tools={tool.name: tool for tool in function_tools},
    )


def _schema_type_matches(value: Any, expected: str) -> bool:
    return {
        "null": value is None,
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
    }.get(expected, True)


def _validate_against_schema(value: Any, schema: dict[str, Any], path: str = "arguments") -> None:
    if "anyOf" in schema:
        failures: list[Exception] = []
        for branch in schema["anyOf"]:
            try:
                _validate_against_schema(value, branch, path)
                return
            except (TypeError, ValueError) as error:
                failures.append(error)
        raise ValueError(f"{path} does not match any allowed schema") from failures[-1]
    if "const" in schema and value != schema["const"]:
        raise ValueError(f"{path} must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        raise ValueError(f"{path} is not an allowed value")
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_schema_type_matches(value, item) for item in expected):
            raise TypeError(f"{path} has the wrong type")
    elif isinstance(expected, str) and not _schema_type_matches(value, expected):
        raise TypeError(f"{path} must be {expected}")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        missing = [key for key in required if key not in value]
        if missing:
            raise ValueError(f"{path} is missing required fields: {missing}")
        if schema.get("additionalProperties") is False:
            unknown = set(value) - set(properties)
            if unknown:
                raise ValueError(f"{path} has unknown fields: {sorted(unknown)}")
        for key, item in value.items():
            child_schema = properties.get(key)
            if isinstance(child_schema, dict):
                _validate_against_schema(item, child_schema, f"{path}.{key}")
    if isinstance(value, list):
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        if min_items is not None and len(value) < min_items:
            raise ValueError(f"{path} has too few items")
        if max_items is not None and len(value) > max_items:
            raise ValueError(f"{path} has too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                _validate_against_schema(item, item_schema, f"{path}[{index}]")


def _drop_nullable_nulls(value: Any, schema: dict[str, Any]) -> Any:
    if not isinstance(value, dict):
        return value
    properties = schema.get("properties", {})
    result: dict[str, Any] = {}
    for key, item in value.items():
        child_schema = properties.get(key, {})
        nullable = any(
            isinstance(branch, dict) and branch.get("type") == "null"
            for branch in child_schema.get("anyOf", [])
        )
        if item is None and nullable:
            continue
        result[key] = _drop_nullable_nulls(item, child_schema)
    return result


def usage_from_events(events: list[dict[str, Any]]) -> tuple[Usage, dict[str, int] | None]:
    raw_usage: dict[str, int] | None = None
    for event in events:
        if event.get("type") != "turn.completed" or not isinstance(event.get("usage"), dict):
            continue
        candidate = event["usage"]
        fields = {
            "input_tokens": int(candidate.get("input_tokens", 0) or 0),
            "cached_input_tokens": int(candidate.get("cached_input_tokens", 0) or 0),
            "output_tokens": int(candidate.get("output_tokens", 0) or 0),
            "reasoning_output_tokens": int(
                candidate.get("reasoning_output_tokens", 0) or 0
            ),
        }
        if any(value < 0 for value in fields.values()):
            raise ValueError("Codex token usage cannot contain negative values")
        raw_usage = fields
    if raw_usage is None:
        return Usage(requests=1), None
    input_tokens = raw_usage["input_tokens"]
    output_tokens = raw_usage["output_tokens"]
    usage = Usage(
        requests=1,
        input_tokens=input_tokens,
        input_tokens_details=InputTokensDetails(
            cached_tokens=raw_usage["cached_input_tokens"],
            cache_write_tokens=0,
        ),
        output_tokens=output_tokens,
        output_tokens_details=OutputTokensDetails(
            reasoning_tokens=raw_usage["reasoning_output_tokens"]
        ),
        total_tokens=input_tokens + output_tokens,
    )
    return usage, raw_usage


def decode_response(
    *,
    response_text: str,
    tools: dict[str, FunctionTool],
    output_schema: AgentOutputSchemaBase | None,
    usage: Usage,
    raw_usage: dict[str, Any] | None,
    response_id: str,
) -> ModelResponse:
    try:
        payload = json.loads(response_text)
    except json.JSONDecodeError as error:
        raise ModelBehaviorError("Codex CLI returned malformed response JSON") from error
    if not isinstance(payload, dict):
        raise ModelBehaviorError("Codex CLI response must be a JSON object")
    expected_fields = {"content", "thought_summary", "tool_calls", "final_output"}
    if set(payload) != expected_fields:
        raise ModelBehaviorError(
            f"Codex CLI response fields must be exactly {sorted(expected_fields)}"
        )
    content = payload["content"]
    thought_summary = payload["thought_summary"]
    tool_calls = payload["tool_calls"]
    final_output = payload["final_output"]
    if not isinstance(content, str) or not isinstance(thought_summary, str):
        raise ModelBehaviorError("content and thought_summary must be strings")
    if not isinstance(tool_calls, list) or len(tool_calls) > 1:
        raise ModelBehaviorError("tool_calls must contain at most one call")

    output: list[Any] = []
    if tool_calls:
        if final_output is not None:
            raise ModelBehaviorError("A tool turn cannot also contain final_output")
        call = tool_calls[0]
        if not isinstance(call, dict) or set(call) != {"name", "arguments"}:
            raise ModelBehaviorError("A tool call must contain only name and arguments")
        name = call["name"]
        arguments = call["arguments"]
        if not isinstance(name, str) or name not in tools:
            raise ModelBehaviorError(f"Codex CLI requested unknown tool: {name!r}")
        if not isinstance(arguments, dict):
            raise ModelBehaviorError("Tool arguments must be a JSON object, not encoded JSON")
        schema = inline_local_refs(tools[name].params_json_schema)
        try:
            _validate_against_schema(arguments, schema)
        except (TypeError, ValueError) as error:
            raise ModelBehaviorError(f"Invalid arguments for tool {name!r}: {error}") from error
        arguments = _drop_nullable_nulls(arguments, schema)
        call_id = f"call_{hashlib.sha256((response_id + name).encode()).hexdigest()[:24]}"
        output.append(
            ResponseFunctionToolCall(
                arguments=json.dumps(arguments, ensure_ascii=False, separators=(",", ":")),
                call_id=call_id,
                name=name,
                type="function_call",
                status="completed",
            )
        )
    else:
        typed = output_schema is not None and not output_schema.is_plain_text()
        if typed:
            if final_output is None:
                raise ModelBehaviorError("Typed agent turn is missing final_output")
            serialized = json.dumps(final_output, ensure_ascii=False, separators=(",", ":"))
            try:
                output_schema.validate_json(serialized)
            except Exception as error:
                raise ModelBehaviorError("Codex CLI returned invalid typed final_output") from error
            message_text = serialized
        else:
            if final_output is not None:
                raise ModelBehaviorError("Plain-text agent turn must set final_output to null")
            message_text = content
        output.append(
            ResponseOutputMessage(
                id=f"msg_{response_id}",
                content=[
                    ResponseOutputText(
                        annotations=[],
                        text=message_text,
                        type="output_text",
                    )
                ],
                role="assistant",
                status="completed",
                type="message",
                phase="final_answer",
            )
        )
    return ModelResponse(
        output=output,
        usage=usage,
        response_id=None,
        request_id=response_id,
        raw_usage=raw_usage,
    )


__all__ = [
    "EncodedRequest",
    "decode_response",
    "encode_request",
    "inline_local_refs",
    "response_schema",
    "usage_from_events",
]
