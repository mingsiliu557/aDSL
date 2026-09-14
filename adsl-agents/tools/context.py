from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class ToolEvent:
    tool: str
    path: str
    success: bool = True
    code: str | None = None
    changed: bool | None = None


def patch_failure(events: list[ToolEvent]) -> str | None:
    pending = None
    for event in events:
        if event.tool != "apply_patch":
            continue
        if event.code == "PATCH_RETRY_EXHAUSTED":
            return event.code
        if not event.success:
            pending = event.code or "PATCH_FAILED"
        elif event.changed is not False:
            pending = None
    return pending


@dataclass
class AgentToolContext:
    workspace: Path
    source_path: Path
    executor_timeout: float = 300.0
    events: list[ToolEvent] = field(default_factory=list)
    record_noop_patch: bool = False
    patch_scope_id: str | None = None

    def __post_init__(self) -> None:
        self.workspace = self.workspace.expanduser().resolve()
        self.source_path = self.source_path.expanduser().resolve()
        if self.source_path != self.workspace and self.workspace not in self.source_path.parents:
            raise ValueError("source_path must be inside workspace")

    def resolve(self, path: str) -> Path:
        candidate = Path(path).expanduser()
        if not candidate.is_absolute():
            candidate = self.workspace / candidate
        resolved = candidate.resolve()
        if resolved != self.workspace and self.workspace not in resolved.parents:
            raise ValueError(f"path escapes workspace: {path}")
        return resolved

    def record(self, tool: str, path: Path, *, success: bool = True,
               code: str | None = None, changed: bool | None = None) -> None:
        self.events.append(ToolEvent(tool=tool, path=path.relative_to(self.workspace).as_posix(),
                                     success=success, code=code, changed=changed))


__all__ = ["AgentToolContext", "ToolEvent"]
