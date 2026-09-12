from __future__ import annotations

import json

from agents import RunContextWrapper, function_tool

from .context import AgentToolContext


READ_FILE_MAX_CHARS = 12_000


@function_tool(failure_error_function=None)
def read_file(
    context: RunContextWrapper[AgentToolContext], path: str,
    offset: int = 0, max_chars: int = READ_FILE_MAX_CHARS,
    json_pointer: str | None = None,
) -> str:
    """Read a bounded page of a workspace UTF-8 text file.

    Large files return a partial excerpt, not a complete file or JSON document.
    The metadata gives next_offset (Unicode characters, not bytes). Request
    another page only when relevant; do not automatically read whole reports.
    Small files read from offset zero retain their original text output.

    Args:
        path: Workspace-relative file path.
        offset: Zero-based character offset; must be non-negative.
        max_chars: Requested page size, positive and capped at 12000 characters.
        json_pointer: Optional JSON field path, e.g. /findings/0/metric. Use ~1 for
            a slash and ~0 for a tilde in object keys. Offset then refers to the
            selected field's serialized text, not the whole file.
    """
    if offset < 0 or max_chars <= 0:
        raise ValueError("offset must be non-negative and max_chars must be positive")
    limit = min(max_chars, READ_FILE_MAX_CHARS)
    target = context.context.resolve(path)
    with target.open(encoding="utf-8") as handle:
        if json_pointer is not None:
            if json_pointer and not json_pointer.startswith("/"):
                raise ValueError("json_pointer must be empty or start with /")
            value = json.load(handle)
            for raw in json_pointer.split("/")[1:]:
                key = raw.replace("~1", "/").replace("~0", "~")
                if isinstance(value, dict):
                    value = value[key]
                elif isinstance(value, list) and key.isascii() and key.isdigit():
                    value = value[int(key)]
                else:
                    raise ValueError("json_pointer does not select an existing field")
            # Parsing stays local; only the requested field/page reaches the model.
            content = json.dumps(value, ensure_ascii=False, indent=2)[offset:offset + limit + 1]
        else:
            # Bounded reads avoid materializing a huge text file just to truncate it.
            remaining = offset
            while remaining:
                skipped = handle.read(min(remaining, READ_FILE_MAX_CHARS))
                if not skipped:
                    break
                remaining -= len(skipped)
            content = handle.read(limit + 1)
    truncated = len(content) > limit
    content = content[:limit]
    context.context.record("read_file", target)
    if offset or truncated:
        end = offset + len(content)
        next_offset = str(end) if truncated else "none (EOF)"
        return (
            f"[read_file excerpt: offset={offset}, returned_chars={len(content)}, "
            f"truncated={str(truncated).lower()}, next_offset={next_offset}]\n"
            + content
        )
    return content


@function_tool(failure_error_function=None)
def write_file(context: RunContextWrapper[AgentToolContext], path: str, content: str) -> str:
    """Write the complete initial program to the assigned empty source file.

    Args:
        path: Workspace-relative path of the assigned source file.
        content: Complete UTF-8 source text.
    """
    target = context.context.resolve(path)
    if target != context.context.source_path:
        raise ValueError("write_file may write only the assigned source file")
    if not target.is_file():
        raise FileNotFoundError(target)
    if not content.strip():
        raise ValueError("content must not be empty")
    existing = target.read_text(encoding="utf-8")
    if existing:
        if existing == content:
            return "no change: assigned source already has identical content"
        return "write rejected: assigned source is no longer empty; use apply_patch"
    target.write_text(content, encoding="utf-8")
    context.context.record("write_file", target)
    return f"wrote {target.relative_to(context.context.workspace).as_posix()}"


@function_tool(failure_error_function=None)
def apply_patch(
    context: RunContextWrapper[AgentToolContext],
    path: str,
    old_text: str,
    new_text: str,
) -> str:
    """Replace one exact, unique text block in an existing workspace file.

    Args:
        path: Workspace-relative file path.
        old_text: Exact current text. It must occur exactly once.
        new_text: Replacement text.
    """
    target = context.context.resolve(path)
    if target != context.context.source_path:
        raise ValueError("apply_patch may edit only the assigned source file")
    content = target.read_text(encoding="utf-8")
    if not old_text:
        raise ValueError("old_text must not be empty")
    occurrences = content.count(old_text)
    if occurrences != 1:
        raise ValueError(f"old_text must occur exactly once; found {occurrences}")
    if old_text == new_text:
        return "no change: new_text is identical to old_text"
    target.write_text(content.replace(old_text, new_text, 1), encoding="utf-8")
    context.context.record("apply_patch", target)
    return f"patched {target.relative_to(context.context.workspace).as_posix()}"


READ_TOOLS = [read_file]
WRITE_TOOLS = [write_file]
BUILD_TOOLS = [read_file, write_file, apply_patch]
PATCH_TOOLS = [read_file, apply_patch]

__all__ = [
    "BUILD_TOOLS",
    "PATCH_TOOLS",
    "READ_TOOLS",
    "WRITE_TOOLS",
    "apply_patch",
    "read_file",
    "write_file",
]
