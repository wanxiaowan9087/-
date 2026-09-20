from __future__ import annotations

import pytest
from pydantic import ValidationError

from backend.app.core.config import Settings


@pytest.mark.parametrize(
    ("raw_origins", "expected_origins"),
    [
        (
            "http://localhost:5173,https://console.example.com",
            ["http://localhost:5173", "https://console.example.com"],
        ),
        (
            '["http://localhost:5173", "https://console.example.com"]',
            ["http://localhost:5173", "https://console.example.com"],
        ),
    ],
)
def test_settings_accepts_cors_origin_environment_formats(
    monkeypatch,
    raw_origins: str,
    expected_origins: list[str],
) -> None:
    monkeypatch.setenv("APP_CORS_ORIGINS", raw_origins)

    settings = Settings(environment="test", redis_url=None)

    assert settings.cors_origins == expected_origins


def test_test_executor_is_rejected_outside_test_environment() -> None:
    with pytest.raises(ValidationError, match="test executor can only"):
        Settings(environment="development", test_executor_enabled=True)


def test_dashscope_rerank_settings_read_standard_key_without_exposing_it(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "rerank-secret")

    settings = Settings(environment="test", redis_url=None)

    assert settings.dashscope_api_key is not None
    assert settings.dashscope_api_key.get_secret_value() == "rerank-secret"
    assert "rerank-secret" not in repr(settings)
    assert settings.agent_rerank_result_limit == 8
    assert settings.agent_query_rewrite_confidence_threshold == 0.5


def test_dashscope_rerank_key_is_separate_from_primary_key(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "primary-secret")
    monkeypatch.setenv("DASHSCOPE_RERANK_API_KEY", "agent-robot-rerank-secret")

    settings = Settings(environment="test", redis_url=None)

    assert settings.dashscope_api_key is not None
    assert settings.dashscope_rerank_api_key is not None
    assert settings.dashscope_api_key.get_secret_value() == "primary-secret"
    assert settings.dashscope_rerank_api_key.get_secret_value() == "agent-robot-rerank-secret"
    assert "agent-robot-rerank-secret" not in repr(settings)


def test_dashscope_rerank_key_is_optional_for_legacy_deployments(monkeypatch) -> None:
    monkeypatch.setenv("DASHSCOPE_API_KEY", "primary-secret")
    monkeypatch.delenv("DASHSCOPE_RERANK_API_KEY", raising=False)

    settings = Settings(environment="test", redis_url=None)

    assert settings.dashscope_rerank_api_key is None
