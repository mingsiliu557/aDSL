from __future__ import annotations

from pathlib import Path
import subprocess
from unittest.mock import Mock

import pytest

from adsl.agents.utils import config
from adsl.agents.utils.config import ModelProfile


CODEX_PROFILE = """provider: codex-cli
api: exec
params:
  model: gpt-5.6-sol
  reasoning_effort: high
  timeout: 900
  max_prompt_chars: 200000
"""


def _profile(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "model.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_packaged_profile_defaults_to_codex_cli() -> None:
    assert config.packaged_profile() == config.packaged_codex_profile()
    assert config.packaged_profile().name == "codex-cli-gpt-5.6-sol.yaml"
    assert config.packaged_openrouter_profile().name == "openrouter-gemini-3.1-pro.yaml"


def test_codex_profile_needs_no_api_credential(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADSL_CODEX_CLI_BIN", "/bin/true")
    profile = ModelProfile.load(_profile(tmp_path, CODEX_PROFILE))
    assert profile.provider == "codex-cli"
    assert profile.api == "exec"
    assert profile.api_key is None
    assert profile.model == "gpt-5.6-sol"


def test_codex_environment_overrides(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("ADSL_CODEX_CLI_BIN", "/bin/true")
    monkeypatch.setenv("ADSL_CODEX_CLI_TIMEOUT_SECONDS", "12.5")
    monkeypatch.setenv("ADSL_CODEX_CLI_MAX_PROMPT_CHARS", "1234")
    profile = ModelProfile.load(_profile(tmp_path, CODEX_PROFILE))
    assert profile.timeout == 12.5
    assert profile.max_prompt_chars == 1234


@pytest.mark.parametrize(
    "content",
    [
        CODEX_PROFILE.replace("api: exec", "api: responses"),
        CODEX_PROFILE.replace("params:", "credential:\n  env: OPENAI_API_KEY\nparams:"),
        CODEX_PROFILE.replace("  timeout: 900", "  unknown: true"),
        CODEX_PROFILE.replace("  model: gpt-5.6-sol\n", ""),
    ],
)
def test_bad_codex_profiles_fail_closed(tmp_path, content) -> None:
    with pytest.raises(ValueError):
        ModelProfile.load(_profile(tmp_path, content))


def test_openai_profile_still_loads(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "not-a-real-key")
    content = """provider: openai
api: chat_completions
credential:
  env: TEST_OPENAI_KEY
params:
  base_url: https://example.invalid/v1
  model: test-model
"""
    profile = ModelProfile.load(_profile(tmp_path, content))
    assert profile.provider == "openai"
    assert profile.api_key == "not-a-real-key"
    assert profile.credential_source == "env"
    assert profile.trust_env is True
    assert profile.model_settings().include_usage is True
    assert "not-a-real-key" not in repr(profile)


def test_stepcode_profile_loads_key_without_storing_it_in_repr(tmp_path, monkeypatch) -> None:
    secret = "ak-testcredential1234567890"
    run = Mock(
        return_value=subprocess.CompletedProcess(
            ["stepcode", "config", "get", "apiKey"],
            0,
            stdout=f"info {secret}\n",
            stderr="",
        )
    )
    monkeypatch.setattr(config.subprocess, "run", run)
    content = """provider: openai
api: responses
credential:
  stepcode: true
params:
  base_url: http://127.0.0.1:44949/v1
  model: gpt-5.6-sol
  trust_env: false
"""

    profile = ModelProfile.load(_profile(tmp_path, content))

    assert profile.api_key == secret
    assert profile.credential_source == "stepcode"
    assert profile.trust_env is False
    assert secret not in repr(profile)
    assert run.call_args.args[0] == ["stepcode", "config", "get", "apiKey"]
    assert "shell" not in run.call_args.kwargs


def test_stepcode_failure_does_not_leak_command_output(tmp_path, monkeypatch) -> None:
    secret = "ak-should-not-appear-in-error"
    monkeypatch.setattr(
        config.subprocess,
        "run",
        Mock(
            return_value=subprocess.CompletedProcess(
                ["stepcode", "config", "get", "apiKey"],
                2,
                stdout=secret,
                stderr=secret,
            )
        ),
    )
    content = """provider: openai
api: responses
credential:
  stepcode: true
params:
  base_url: http://127.0.0.1:44949/v1
  model: gpt-5.6-sol
"""

    with pytest.raises(ValueError) as caught:
        ModelProfile.load(_profile(tmp_path, content))

    assert secret not in str(caught.value)


@pytest.mark.parametrize("value", ["false", 1, None])
def test_stepcode_credential_requires_literal_true(tmp_path, value) -> None:
    content = f"""provider: openai
api: responses
credential:
  stepcode: {value!r}
params:
  base_url: http://127.0.0.1:44949/v1
  model: gpt-5.6-sol
"""

    with pytest.raises(ValueError, match="credential.stepcode must be true"):
        ModelProfile.load(_profile(tmp_path, content))


@pytest.mark.parametrize("value", ["1", "null", "'false'"])
def test_trust_env_requires_boolean(tmp_path, monkeypatch, value) -> None:
    monkeypatch.setenv("TEST_OPENAI_KEY", "test")
    content = f"""provider: openai
api: responses
credential:
  env: TEST_OPENAI_KEY
params:
  base_url: http://example.invalid/v1
  model: test
  trust_env: {value}
"""

    with pytest.raises(ValueError, match="params.trust_env must be a boolean"):
        ModelProfile.load(_profile(tmp_path, content))
