from __future__ import annotations

import pytest

from adsl.agents.service import (
    _asset_executor_timeout_seconds,
    _render_execution_config,
)


def test_asset_executor_timeout_defaults_to_300(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS", raising=False)

    assert _asset_executor_timeout_seconds() == 300.0


def test_asset_executor_timeout_accepts_positive_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS", "900")

    assert _asset_executor_timeout_seconds() == 900.0


@pytest.mark.parametrize("value", ["0", "-1", "not-a-number"])
def test_asset_executor_timeout_rejects_invalid_override(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    monkeypatch.setenv("ADSL_ASSET_EXECUTOR_TIMEOUT_SECONDS", value)

    with pytest.raises(ValueError, match="must be a positive number"):
        _asset_executor_timeout_seconds()


def test_render_execution_config_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "ADSL_RENDER_ENGINE",
        "ADSL_RENDER_WIDTH",
        "ADSL_RENDER_HEIGHT",
        "ADSL_RENDER_SAMPLES",
    ):
        monkeypatch.delenv(name, raising=False)

    assert _render_execution_config() == {
        "render_engine": "BLENDER_EEVEE",
        "render_width": 1024,
        "render_height": 1024,
        "render_samples": 256,
    }


def test_render_execution_config_accepts_cpu_preview(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ADSL_RENDER_ENGINE", "cycles")
    monkeypatch.setenv("ADSL_RENDER_WIDTH", "512")
    monkeypatch.setenv("ADSL_RENDER_HEIGHT", "512")
    monkeypatch.setenv("ADSL_RENDER_SAMPLES", "16")

    assert _render_execution_config() == {
        "render_engine": "CYCLES",
        "render_width": 512,
        "render_height": 512,
        "render_samples": 16,
    }


@pytest.mark.parametrize(
    ("name", "value", "message"),
    [
        ("ADSL_RENDER_ENGINE", "invalid", "must be CYCLES or BLENDER_EEVEE"),
        ("ADSL_RENDER_WIDTH", "0", "must be a positive integer"),
        ("ADSL_RENDER_SAMPLES", "invalid", "must be a positive integer"),
    ],
)
def test_render_execution_config_rejects_invalid_overrides(
    monkeypatch: pytest.MonkeyPatch,
    name: str,
    value: str,
    message: str,
) -> None:
    monkeypatch.setenv(name, value)

    with pytest.raises(ValueError, match=message):
        _render_execution_config()
