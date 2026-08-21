from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.application.ports import RunExecution
from backend.app.application.security_rate_limit import (
    InMemorySecurityRateLimiter,
    RateLimit,
    SecurityRateLimiter,
)
from backend.app.application.streaming import RunCoordinator
from backend.app.core.config import Settings
from backend.app.core.errors import AppError
from backend.app.main import create_app


class NoopExecutor:
    async def close(self) -> None:
        return None


class TinyExecutor:
    async def stream(
        self, execution: RunExecution
    ) -> AsyncIterator[tuple[str, dict[str, object]]]:
        yield "status", {"phase": "accepted", "detail": None}
        yield "delta", {"index": 0, "content": "安全测试回复"}
        yield "done", {
            "outcome": "completed",
            "assistant_message_id": str(execution.assistant_message_id),
            "finish_reason": "stop",
            "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
        }

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_rate_limiter_returns_retry_after_without_storing_raw_identifier() -> None:
    now = [datetime(2026, 8, 21, tzinfo=UTC)]
    limiter = SecurityRateLimiter(
        InMemorySecurityRateLimiter(clock=lambda: now[0])
    )
    policy = RateLimit(requests=1, window_seconds=60)

    await limiter.enforce(scope="test", identifier="203.0.113.10", limit=policy)
    with pytest.raises(AppError) as error:
        await limiter.enforce(scope="test", identifier="203.0.113.10", limit=policy)

    assert error.value.code == "RATE_LIMITED"
    assert error.value.status_code == 429
    assert error.value.headers == {"Retry-After": "60"}
    assert "203.0.113.10" not in "".join(limiter._adapter._events)  # type: ignore[attr-defined]

    now[0] += timedelta(seconds=61)
    await limiter.enforce(scope="test", identifier="203.0.113.10", limit=policy)


@pytest.mark.asyncio
async def test_run_coordinator_rejects_excess_global_and_per_user_capacity() -> None:
    coordinator = RunCoordinator(
        MemoryPlatformRepository(),
        executor=NoopExecutor(),  # type: ignore[arg-type]
        max_concurrent_runs=2,
        max_concurrent_runs_per_user=1,
    )
    first = await coordinator.reserve("user-a")
    with pytest.raises(AppError) as same_user:
        await coordinator.reserve("user-a")
    assert same_user.value.code == "RATE_LIMITED"

    second = await coordinator.reserve("user-b")
    with pytest.raises(AppError) as global_limit:
        await coordinator.reserve("user-c")
    assert global_limit.value.code == "RATE_LIMITED"

    await coordinator.release_reservation(first)
    await coordinator.release_reservation(second)
    await coordinator.close()


@pytest.mark.asyncio
async def test_login_rate_limit_is_enforced_at_http_boundary() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./security-login-test.db",
        redis_url=None,
        demo_auth_enabled=False,
        auth_login_ip_limit=1,
        sms_provider="fake",
    )
    app = create_app(settings, repository=MemoryPlatformRepository(), executor=TinyExecutor())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        payload = {"phone": "13800138000", "password": "Wrong@123"}
        first = await client.post("/api/v1/auth/login", json=payload)
        second = await client.post("/api/v1/auth/login", json=payload)
    await app.state.platform_service.close()

    assert first.status_code == 401
    assert second.status_code == 429
    assert second.headers["Retry-After"].isdigit()
    assert second.json()["code"] == "RATE_LIMITED"


@pytest.mark.asyncio
async def test_chat_rate_limit_is_enforced_before_run_creation() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./security-chat-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        chat_ip_per_minute_limit=1,
    )
    app = create_app(settings, repository=MemoryPlatformRepository(), executor=TinyExecutor())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as client:
        session = await client.post(
            "/api/v1/sessions",
            headers={
                "Authorization": "Bearer alice:user",
                "Idempotency-Key": "security-session-001",
            },
            json={},
        )
        session_id = session.json()["data"]["id"]
        base_headers = {"Authorization": "Bearer alice:user"}
        first = await client.post(
            "/api/v1/chat/stream",
            headers={**base_headers, "Idempotency-Key": "security-stream-001"},
            json={"mode": "new", "session_id": session_id, "content": "你好"},
        )
        second = await client.post(
            "/api/v1/chat/stream",
            headers={**base_headers, "Idempotency-Key": "security-stream-002"},
            json={"mode": "new", "session_id": session_id, "content": "再次询问"},
        )
    await app.state.platform_service.close()

    assert first.status_code == 200
    assert second.status_code == 429
    assert second.json()["code"] == "RATE_LIMITED"
