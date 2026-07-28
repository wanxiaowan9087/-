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
