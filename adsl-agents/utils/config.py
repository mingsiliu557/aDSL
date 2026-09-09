from __future__ import annotations

from dataclasses import dataclass, field
import os
from pathlib import Path
import re
import subprocess
from typing import Any, Literal

import httpx
import yaml
from agents import (
    Model,
    ModelSettings,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    set_tracing_disabled,
)
from openai import AsyncOpenAI


ProviderKind = Literal["openai", "codex-cli"]
ApiKind = Literal["chat_completions", "responses", "exec"]
_CODEX_REASONING_EFFORTS = {"low", "medium", "high", "xhigh", "max", "ultra"}
_STEPCODE_KEY_RE = re.compile(r"(?<![A-Za-z0-9])(?:ak|sk)-[A-Za-z0-9._-]+")


def packaged_openrouter_profile() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "llm"
        / "openrouter-gemini-3.1-pro.yaml"
    )


def packaged_codex_profile() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "llm"
        / "codex-cli-gpt-5.6-sol.yaml"
    )


def packaged_profile() -> Path:
    # The project default is the authenticated local Codex CLI transport.
    return packaged_codex_profile()


def packaged_stepcode_profile() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "llm"
        / "stepcode-gpt-5.6-sol.yaml"
    )


def _positive_float(value: Any, *, field: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _positive_int(value: Any, *, field: str) -> int:
    parsed = int(value)
    if parsed <= 0:
        raise ValueError(f"{field} must be positive")
    return parsed


def _stepcode_api_key() -> str:
    binary = os.environ.get("ADSL_STEPCODE_BIN", "stepcode").strip()
    if not binary:
        raise ValueError("ADSL_STEPCODE_BIN must not be empty")
    timeout = _positive_float(
        os.environ.get("ADSL_STEPCODE_TIMEOUT_SECONDS", 15),
        field="Stepcode credential timeout",
    )
    try:
        completed = subprocess.run(
            [binary, "config", "get", "apiKey"],
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except FileNotFoundError as error:
        raise ValueError(f"Stepcode binary was not found: {binary}") from error
    except subprocess.TimeoutExpired as error:
        raise ValueError(f"Stepcode credential lookup timed out after {timeout:g} seconds") from error
    if completed.returncode != 0:
        raise ValueError(
            f"Stepcode credential lookup exited with status {completed.returncode}; "
            "run `stepcode system status` without printing the key"
        )
    matches = _STEPCODE_KEY_RE.findall(completed.stdout)
    if len(matches) != 1:
        raise ValueError(
            "Stepcode credential lookup did not return exactly one supported API key"
        )
    return matches[0]


@dataclass(frozen=True)
class ModelProfile:
    path: Path
    provider: ProviderKind
    api: ApiKind
    model: str
    timeout: float
    base_url: str | None = None
    api_key: str | None = field(default=None, repr=False)
    credential_source: str | None = None
    trust_env: bool = True
    max_retries: int = 0
    max_tokens: int | None = None
    temperature: float | None = None
    parallel_tool_calls: bool | None = None
    include_usage: bool = True
    reasoning_effort: str | None = None
    max_prompt_chars: int | None = None
    cli_binary: str | None = None

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        expected_api: ApiKind | None = None,
    ) -> "ModelProfile":
        config_path = Path(path).expanduser().resolve()
        payload = yaml.safe_load(config_path.read_text(encoding="utf-8"))
        if not isinstance(payload, dict):
            raise ValueError(f"Model profile must contain a mapping: {config_path}")
        allowed_top_level = {"provider", "api", "credential", "params"}
        unknown = set(payload) - allowed_top_level
        if unknown:
            raise ValueError(f"Unknown model profile fields: {sorted(unknown)}")
        provider = payload.get("provider")
        if provider not in {"openai", "codex-cli"}:
            raise ValueError("provider must be 'openai' or 'codex-cli'")
        api = payload.get("api")
        if expected_api is not None and api != expected_api:
            raise ValueError(f"api must be '{expected_api}'")
        params = payload.get("params")
        if not isinstance(params, dict):
            raise ValueError("params must be a mapping")
        if provider == "openai":
            return cls._load_openai(config_path, payload, params, api)
        return cls._load_codex(config_path, payload, params, api)

    @classmethod
    def _load_openai(
        cls,
        config_path: Path,
        payload: dict[str, Any],
        params: dict[str, Any],
        api: Any,
    ) -> "ModelProfile":
        if api not in {"chat_completions", "responses"}:
            raise ValueError("OpenAI api must be 'chat_completions' or 'responses'")
        credential = payload.get("credential")
        if not isinstance(credential, dict):
            raise ValueError("credential must be a mapping")
        if set(credential) not in ({"file"}, {"env"}, {"stepcode"}):
            raise ValueError(
                "credential must define exactly one of 'file', 'env', or 'stepcode'"
            )
        if "file" in credential:
            key_path = Path(str(credential["file"])).expanduser()
            if not key_path.is_absolute():
                key_path = (config_path.parent / key_path).resolve()
            api_key = key_path.read_text(encoding="utf-8").strip()
            if not api_key:
                raise ValueError(f"API key file is empty: {key_path}")
            credential_source = "file"
        elif "env" in credential:
            variable = str(credential["env"]).strip()
            api_key = os.environ.get(variable, "").strip()
            if not api_key:
                raise ValueError(f"Environment variable is empty: {variable}")
            credential_source = "env"
        else:
            if credential["stepcode"] is not True:
                raise ValueError("credential.stepcode must be true")
            api_key = _stepcode_api_key()
            credential_source = "stepcode"
        allowed_params = {
            "base_url",
            "model",
            "timeout",
            "max_retries",
            "max_tokens",
            "temperature",
            "parallel_tool_calls",
            "include_usage",
            "trust_env",
        }
        unknown_params = set(params) - allowed_params
        if unknown_params:
            raise ValueError(f"Unknown OpenAI model params: {sorted(unknown_params)}")
        if "base_url" not in params or "model" not in params:
            raise ValueError("params.base_url and params.model are required")
        model = str(params["model"]).strip()
        base_url = str(params["base_url"]).strip()
        if not model or not base_url:
            raise ValueError("params.base_url and params.model must not be empty")
        trust_env = params.get("trust_env", True)
        if not isinstance(trust_env, bool):
            raise ValueError("params.trust_env must be a boolean")
        max_retries = int(params.get("max_retries", 0))
        if max_retries < 0:
            raise ValueError("params.max_retries must not be negative")
        return cls(
            path=config_path,
            provider="openai",
            api=api,
            model=model,
            base_url=base_url,
            api_key=api_key,
            credential_source=credential_source,
            trust_env=trust_env,
            timeout=_positive_float(params.get("timeout", 300), field="params.timeout"),
            max_retries=max_retries,
            max_tokens=None if params.get("max_tokens") is None else int(params["max_tokens"]),
            temperature=None if params.get("temperature") is None else float(params["temperature"]),
            parallel_tool_calls=(
                None
                if params.get("parallel_tool_calls") is None
                else bool(params["parallel_tool_calls"])
            ),
            include_usage=bool(params.get("include_usage", True)),
        )

    @classmethod
    def _load_codex(
        cls,
        config_path: Path,
        payload: dict[str, Any],
        params: dict[str, Any],
        api: Any,
    ) -> "ModelProfile":
        if "credential" in payload:
            raise ValueError("codex-cli profiles must not contain credential; use `codex login`")
        if api != "exec":
            raise ValueError("codex-cli api must be 'exec'")
        allowed_params = {"model", "reasoning_effort", "timeout", "max_prompt_chars"}
        unknown_params = set(params) - allowed_params
        if unknown_params:
            raise ValueError(f"Unknown Codex CLI model params: {sorted(unknown_params)}")
        model = str(params.get("model", "")).strip()
        if not model:
            raise ValueError("params.model is required for codex-cli")
        reasoning_effort = str(params.get("reasoning_effort", "high")).strip()
        if reasoning_effort not in _CODEX_REASONING_EFFORTS:
            raise ValueError(
                "params.reasoning_effort must be one of "
                + ", ".join(sorted(_CODEX_REASONING_EFFORTS))
            )
        timeout_value: Any = os.environ.get(
            "ADSL_CODEX_CLI_TIMEOUT_SECONDS", params.get("timeout", 900)
        )
        max_prompt_value: Any = os.environ.get(
            "ADSL_CODEX_CLI_MAX_PROMPT_CHARS", params.get("max_prompt_chars", 200_000)
        )
        binary = os.environ.get("ADSL_CODEX_CLI_BIN", "codex").strip()
        if not binary:
            raise ValueError("ADSL_CODEX_CLI_BIN must not be empty")
        return cls(
            path=config_path,
            provider="codex-cli",
            api="exec",
            model=model,
            timeout=_positive_float(timeout_value, field="Codex CLI timeout"),
            reasoning_effort=reasoning_effort,
            max_prompt_chars=_positive_int(
                max_prompt_value, field="Codex CLI max prompt characters"
            ),
            cli_binary=binary,
            parallel_tool_calls=False,
            include_usage=True,
        )

    def client(self) -> AsyncOpenAI:
        if self.provider != "openai" or self.api_key is None or self.base_url is None:
            raise ValueError("client() requires an OpenAI-compatible profile")
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=httpx.AsyncClient(trust_env=self.trust_env),
        )

    def agent_model(self, *, workspace: str | Path | None = None) -> Model:
        set_tracing_disabled(True)
        if self.provider == "codex-cli":
            if workspace is None:
                raise ValueError("workspace is required for a codex-cli model")
            from adsl.agents.providers.codex_cli import CodexCliModel

            return CodexCliModel(
                model=self.model,
                reasoning_effort=self.reasoning_effort or "high",
                timeout=self.timeout,
                max_prompt_chars=self.max_prompt_chars or 200_000,
                repository_root=Path(__file__).resolve().parents[2],
                diagnostics_root=workspace,
                binary=self.cli_binary or "codex",
            )
        if self.api == "responses":
            return OpenAIResponsesModel(model=self.model, openai_client=self.client())
        if self.api != "chat_completions":
            raise ValueError("OpenAI profile requires chat_completions or responses api")
        return OpenAIChatCompletionsModel(
            model=self.model,
            openai_client=self.client(),
            strict_feature_validation=False,
        )

    def model_settings(self) -> ModelSettings:
        if self.provider == "codex-cli":
            return ModelSettings(parallel_tool_calls=False, include_usage=True)
        return ModelSettings(
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            parallel_tool_calls=self.parallel_tool_calls,
            include_usage=self.include_usage,
        )

    def runtime_metadata(self) -> dict[str, Any]:
        common: dict[str, Any] = {
            "profile_name": self.path.name,
            "provider": self.provider,
            "api": self.api,
            "model": self.model,
            "timeout": self.timeout,
        }
        if self.provider == "codex-cli":
            return {
                **common,
                "reasoning_effort": self.reasoning_effort,
                "max_prompt_chars": self.max_prompt_chars,
                "cli_binary": self.cli_binary,
                "sandbox": "read-only",
                "ephemeral": True,
                "ignore_user_config": True,
            }
        return {
            **common,
            "base_url": self.base_url,
            "credential_source": self.credential_source,
            "trust_env": self.trust_env,
            "max_retries": self.max_retries,
            "max_tokens": self.max_tokens,
            "temperature": self.temperature,
            "parallel_tool_calls": self.parallel_tool_calls,
            "include_usage": self.include_usage,
        }


__all__ = [
    "ApiKind",
    "ModelProfile",
    "ProviderKind",
    "packaged_codex_profile",
    "packaged_openrouter_profile",
    "packaged_profile",
    "packaged_stepcode_profile",
]
