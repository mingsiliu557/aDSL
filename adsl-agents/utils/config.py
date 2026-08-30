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
    ModelSettings,
    OpenAIChatCompletionsModel,
    OpenAIResponsesModel,
    set_tracing_disabled,
)
from openai import AsyncOpenAI


ApiKind = Literal["chat_completions", "responses"]
_STEPCODE_KEY_RE = re.compile(r"(?<![A-Za-z0-9])(?:ak|sk)-[A-Za-z0-9._-]+")


def packaged_openrouter_profile() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "llm"
        / "openrouter-gemini-3.1-pro.yaml"
    )


def packaged_stepcode_profile() -> Path:
    return (
        Path(__file__).resolve().parents[1]
        / "configs"
        / "llm"
        / "stepcode-gpt-5.6-sol.yaml"
    )


def packaged_profile() -> Path:
    # The local CLI defaults to the native Stepcode HTTP API.
    return packaged_stepcode_profile()


def _positive_float(value: Any, *, field_name: str) -> float:
    parsed = float(value)
    if parsed <= 0:
        raise ValueError(f"{field_name} must be positive")
    return parsed


def _stepcode_api_key() -> str:
    binary = os.environ.get("ADSL_STEPCODE_BIN", "stepcode").strip()
    if not binary:
        raise ValueError("ADSL_STEPCODE_BIN must not be empty")
    timeout = _positive_float(
        os.environ.get("ADSL_STEPCODE_TIMEOUT_SECONDS", 15),
        field_name="Stepcode credential timeout",
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
        raise ValueError(
            f"Stepcode credential lookup timed out after {timeout:g} seconds"
        ) from error
    if completed.returncode != 0:
        raise ValueError(
            f"Stepcode credential lookup exited with status {completed.returncode}; "
            "run stepcode system status without printing the key"
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
    api: ApiKind
    model: str
    base_url: str
    api_key: str = field(repr=False)
    credential_source: str
    trust_env: bool
    timeout: float
    max_retries: int
    max_tokens: int | None
    temperature: float | None
    parallel_tool_calls: bool | None
    include_usage: bool

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
        if payload.get("provider") != "openai":
            raise ValueError("provider must be 'openai'")
        api = payload.get("api")
        if api not in {"chat_completions", "responses"}:
            raise ValueError("api must be 'chat_completions' or 'responses'")
        if expected_api is not None and api != expected_api:
            raise ValueError(f"api must be '{expected_api}'")

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

        params = payload.get("params")
        if not isinstance(params, dict):
            raise ValueError("params must be a mapping")
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
            raise ValueError(f"Unknown model params: {sorted(unknown_params)}")
        if "base_url" not in params or "model" not in params:
            raise ValueError("params.base_url and params.model are required")
        trust_env = params.get("trust_env", True)
        if not isinstance(trust_env, bool):
            raise ValueError("params.trust_env must be a boolean")
        timeout = _positive_float(
            params.get("timeout", 300),
            field_name="params.timeout",
        )
        max_retries = int(params.get("max_retries", 0))
        if max_retries < 0:
            raise ValueError("params.max_retries must not be negative")
        return cls(
            path=config_path,
            api=api,
            model=str(params["model"]),
            base_url=str(params["base_url"]),
            api_key=api_key,
            credential_source=credential_source,
            trust_env=trust_env,
            timeout=timeout,
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

    def client(self) -> AsyncOpenAI:
        return AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.base_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            http_client=httpx.AsyncClient(trust_env=self.trust_env),
        )

    def agent_model(self) -> OpenAIChatCompletionsModel | OpenAIResponsesModel:
        set_tracing_disabled(True)
        if self.api == "responses":
            return OpenAIResponsesModel(
                model=self.model,
                openai_client=self.client(),
            )
        if self.api != "chat_completions":
            raise ValueError("agent_model requires a chat_completions or responses profile")
        return OpenAIChatCompletionsModel(
            model=self.model,
            openai_client=self.client(),
            strict_feature_validation=False,
        )

    def model_settings(self) -> ModelSettings:
        if self.api not in {"chat_completions", "responses"}:
            raise ValueError("model_settings requires a chat_completions or responses profile")
        return ModelSettings(
            max_tokens=self.max_tokens,
            temperature=self.temperature,
            parallel_tool_calls=self.parallel_tool_calls,
            include_usage=self.include_usage,
        )


__all__ = [
    "ApiKind",
    "ModelProfile",
    "packaged_openrouter_profile",
    "packaged_profile",
    "packaged_stepcode_profile",
]
