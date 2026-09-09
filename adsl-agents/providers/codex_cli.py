from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
from tempfile import TemporaryDirectory
from typing import Any
from uuid import uuid4

from agents.agent_output import AgentOutputSchemaBase
from agents.handoffs import Handoff
from agents.items import ModelResponse, TResponseInputItem, TResponseStreamEvent
from agents.model_settings import ModelSettings
from agents.models.interface import Model, ModelTracing
from agents.tool import Tool
from openai.types.responses.response_prompt_param import ResponsePromptParam

from .codex_codec import decode_response, encode_request, usage_from_events


_SECRET_RE = re.compile(r"(?i)(?:bearer\s+|api[_-]?key[=:]\s*|sk-)[A-Za-z0-9._-]+")
_DATA_URL_RE = re.compile(r"data:image/[A-Za-z0-9.+-]+;base64,[A-Za-z0-9+/=\r\n]+")


class CodexCliError(RuntimeError):
    """A clear, sanitized failure from the Codex CLI transport."""


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    timed_out: bool = False


ProcessRunner = Callable[..., Awaitable[ProcessResult]]


def _redact(text: str) -> str:
    text = _DATA_URL_RE.sub("<redacted-data-image>", text)
    return _SECRET_RE.sub("<redacted-credential>", text)


async def run_process(
    argv: list[str],
    *,
    stdin: str,
    cwd: Path,
    timeout: float,
) -> ProcessResult:
    process = await asyncio.create_subprocess_exec(
        *argv,
        cwd=str(cwd),
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        start_new_session=True,
    )
    task = asyncio.create_task(process.communicate(stdin.encode("utf-8")))
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(task), timeout=timeout)
        return ProcessResult(
            returncode=int(process.returncode or 0),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
        )
    except asyncio.TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
        try:
            stdout, stderr = await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
        except asyncio.TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            try:
                stdout, stderr = await asyncio.wait_for(asyncio.shield(task), timeout=5.0)
            except asyncio.TimeoutError:
                # A launcher can exit while a detached descendant keeps its stdio pipes open.
                # Close our transports and cancel communicate so timeout cleanup is still bounded.
                for stream in (process.stdout, process.stderr):
                    transport = getattr(stream, "_transport", None)
                    if transport is not None:
                        transport.close()
                if process.stdin is not None:
                    process.stdin.close()
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                try:
                    await asyncio.wait_for(process.wait(), timeout=1.0)
                except asyncio.TimeoutError:
                    pass
                stdout, stderr = b"", b""
        return ProcessResult(
            returncode=int(process.returncode if process.returncode is not None else -1),
            stdout=stdout.decode("utf-8", errors="replace"),
            stderr=stderr.decode("utf-8", errors="replace"),
            timed_out=True,
        )


class CodexCliModel(Model):
    def __init__(
        self,
        *,
        model: str,
        reasoning_effort: str,
        timeout: float,
        max_prompt_chars: int,
        repository_root: str | Path,
        diagnostics_root: str | Path,
        binary: str = "codex",
        process_runner: ProcessRunner = run_process,
        preflight: bool = True,
    ) -> None:
        self.model = model
        self.reasoning_effort = reasoning_effort
        self.timeout = timeout
        self.max_prompt_chars = max_prompt_chars
        self.repository_root = Path(repository_root).expanduser().resolve()
        self.diagnostics_root = Path(diagnostics_root).expanduser().resolve() / "codex_cli"
        self.process_runner = process_runner
        self.preflight_enabled = preflight
        self.binary = self._resolve_binary(binary)
        self._preflight_lock = asyncio.Lock()
        self._preflight_done = False
        self._version: str | None = None
        self._call_number = 0
        self._call_lock = asyncio.Lock()

    @staticmethod
    def _resolve_binary(binary: str) -> str:
        candidate = binary.strip()
        if not candidate:
            raise CodexCliError("ADSL_CODEX_CLI_BIN must not be empty")
        resolved = shutil.which(candidate)
        if resolved is None:
            raise CodexCliError(
                f"Codex CLI executable not found: {candidate!r}. Install Codex or set "
                "ADSL_CODEX_CLI_BIN to its executable path."
            )
        return str(Path(resolved).resolve())

    async def _ensure_preflight(self) -> None:
        if not self.preflight_enabled or self._preflight_done:
            return
        async with self._preflight_lock:
            if self._preflight_done:
                return
            version = await self.process_runner(
                [self.binary, "--version"],
                stdin="",
                cwd=self.repository_root,
                timeout=min(self.timeout, 300.0),
            )
            if version.timed_out or version.returncode != 0:
                raise CodexCliError(
                    "Codex CLI version preflight failed: "
                    + _redact(version.stderr or version.stdout or "no diagnostic output")
                )
            login = await self.process_runner(
                [self.binary, "login", "status"],
                stdin="",
                cwd=self.repository_root,
                timeout=min(self.timeout, 300.0),
            )
            if login.timed_out or login.returncode != 0:
                raise CodexCliError(
                    "Codex CLI is not logged in. Run `codex login`, then verify with "
                    "`codex login status`. Diagnostic: "
                    + _redact(login.stderr or login.stdout or "no diagnostic output")
                )
            self._version = _redact((version.stdout or version.stderr).strip())
            self._preflight_done = True

    async def _next_call(self) -> tuple[int, str]:
        async with self._call_lock:
            self._call_number += 1
            return self._call_number, uuid4().hex

    def _command(
        self,
        *,
        schema_path: Path,
        response_path: Path,
        image_paths: tuple[Path, ...],
    ) -> list[str]:
        command = [
            self.binary,
            "exec",
            "--ephemeral",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--color",
            "never",
            "--json",
            "--output-schema",
            str(schema_path),
            "--output-last-message",
            str(response_path),
            "-C",
            str(self.repository_root),
            "--model",
            self.model,
            "-c",
            f'model_reasoning_effort="{self.reasoning_effort}"',
        ]
        for path in image_paths:
            command.extend(["--image", str(path)])
        command.append("-")
        return command

    @staticmethod
    def _sanitized_command(command: list[str]) -> list[str]:
        sanitized: list[str] = []
        hidden_next: str | None = None
        image_number = 0
        for value in command:
            if hidden_next is not None:
                if hidden_next == "image":
                    image_number += 1
                    sanitized.append(f"<temporary-image-{image_number}>")
                else:
                    sanitized.append(f"<temporary-{hidden_next}>")
                hidden_next = None
                continue
            sanitized.append(value)
            if value == "--output-schema":
                hidden_next = "schema"
            elif value == "--output-last-message":
                hidden_next = "response"
            elif value == "--image":
                hidden_next = "image"
        return sanitized

    def _persist_diagnostics(
        self,
        *,
        call_number: int,
        call_key: str,
        command: list[str],
        prompt: str,
        result: ProcessResult,
        response_text: str,
    ) -> Path:
        self.diagnostics_root.mkdir(parents=True, exist_ok=True)
        directory = self.diagnostics_root / f"call_{call_number:06d}_{call_key[:8]}"
        directory.mkdir(parents=False, exist_ok=False)
        metadata = {
            "provider": "codex-cli",
            "model": self.model,
            "reasoning_effort": self.reasoning_effort,
            "timeout_seconds": self.timeout,
            "codex_version": self._version,
            "prompt_chars": len(prompt),
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "command": self._sanitized_command(command),
            "returncode": result.returncode,
            "timed_out": result.timed_out,
        }
        (directory / "metadata.json").write_text(
            json.dumps(metadata, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (directory / "events.jsonl").write_text(_redact(result.stdout), encoding="utf-8")
        (directory / "response.json").write_text(_redact(response_text), encoding="utf-8")
        (directory / "stderr.txt").write_text(_redact(result.stderr), encoding="utf-8")
        return directory

    async def get_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> ModelResponse:
        del model_settings, tracing
        if handoffs:
            raise CodexCliError("Codex CLI provider does not support SDK handoffs")
        if previous_response_id is not None or conversation_id is not None or prompt is not None:
            raise CodexCliError(
                "Codex CLI provider does not support provider-side conversations or stored prompts"
            )
        await self._ensure_preflight()
        call_number, call_key = await self._next_call()
        with TemporaryDirectory(prefix="adsl-codex-") as temporary:
            temporary_directory = Path(temporary)
            encoded = encode_request(
                system_instructions=system_instructions,
                input=input,
                tools=tools,
                output_schema=output_schema,
                temporary_directory=temporary_directory,
            )
            if len(encoded.prompt) > self.max_prompt_chars:
                raise CodexCliError(
                    f"Codex prompt has {len(encoded.prompt)} characters, exceeding the configured "
                    f"limit of {self.max_prompt_chars}; refusing to truncate agent context."
                )
            schema_path = temporary_directory / "response-schema.json"
            response_path = temporary_directory / "response.json"
            schema_path.write_text(
                json.dumps(encoded.output_schema, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            command = self._command(
                schema_path=schema_path,
                response_path=response_path,
                image_paths=encoded.image_paths,
            )
            result = await self.process_runner(
                command,
                stdin=encoded.prompt,
                cwd=self.repository_root,
                timeout=self.timeout,
            )
            response_text = response_path.read_text(encoding="utf-8") if response_path.is_file() else ""
            diagnostic_path = self._persist_diagnostics(
                call_number=call_number,
                call_key=call_key,
                command=command,
                prompt=encoded.prompt,
                result=result,
                response_text=response_text,
            )
            if result.timed_out:
                raise CodexCliError(
                    f"Codex CLI timed out after {self.timeout:g} seconds; diagnostics: {diagnostic_path}"
                )
            if result.returncode != 0:
                diagnostic = _redact(result.stderr or result.stdout or "no diagnostic output")
                raise CodexCliError(
                    f"Codex CLI exited with status {result.returncode}: {diagnostic}; "
                    f"diagnostics: {diagnostic_path}"
                )
            if not response_text.strip():
                raise CodexCliError(
                    f"Codex CLI did not write --output-last-message; diagnostics: {diagnostic_path}"
                )
            events: list[dict[str, Any]] = []
            for line_number, line in enumerate(result.stdout.splitlines(), 1):
                if not line.strip():
                    continue
                try:
                    event = json.loads(line)
                except json.JSONDecodeError as error:
                    raise CodexCliError(
                        f"Codex CLI emitted invalid JSONL at line {line_number}; "
                        f"diagnostics: {diagnostic_path}"
                    ) from error
                if not isinstance(event, dict):
                    raise CodexCliError(
                        f"Codex CLI emitted a non-object JSONL event at line {line_number}; "
                        f"diagnostics: {diagnostic_path}"
                    )
                events.append(event)
            usage, raw_usage = usage_from_events(events)
            return decode_response(
                response_text=response_text,
                tools=encoded.tools,
                output_schema=output_schema,
                usage=usage,
                raw_usage=raw_usage,
                response_id=f"codex_{call_key}",
            )

    async def stream_response(
        self,
        system_instructions: str | None,
        input: str | list[TResponseInputItem],
        model_settings: ModelSettings,
        tools: list[Tool],
        output_schema: AgentOutputSchemaBase | None,
        handoffs: list[Handoff],
        tracing: ModelTracing,
        *,
        previous_response_id: str | None,
        conversation_id: str | None,
        prompt: ResponsePromptParam | None,
    ) -> AsyncIterator[TResponseStreamEvent]:
        del (
            system_instructions,
            input,
            model_settings,
            tools,
            output_schema,
            handoffs,
            tracing,
            previous_response_id,
            conversation_id,
            prompt,
        )
        if False:
            yield  # pragma: no cover
        raise NotImplementedError("Codex CLI provider does not support streaming")


__all__ = ["CodexCliError", "CodexCliModel", "ProcessResult", "run_process"]
