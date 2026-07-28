from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.application.ports import RunExecution
from backend.app.core.config import Settings
from backend.app.main import create_app


class FakeExecutor:
    async def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, object]]]:
        yield "status", {"phase": "accepted", "detail": None}
        yield "delta", {"index": 0, "content": "fake answer"}
        yield (
            "done",
            {
                "outcome": "completed",
                "assistant_message_id": str(execution.assistant_message_id),
                "finish_reason": "stop",
                "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
            },
        )

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id

    async def health(self) -> str:
        return "available"

    async def close(self) -> None:
        return None


@pytest_asyncio.fixture
async def client() -> AsyncIterator[AsyncClient]:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./test.db",
        redis_url=None,
        demo_auth_enabled=True,
    )
    app = create_app(
        settings,
        repository=MemoryPlatformRepository(),
        executor=FakeExecutor(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client
    await app.state.platform_service.close()


@pytest_asyncio.fixture
async def identity_client() -> AsyncIterator[AsyncClient]:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./identity-test.db",
        redis_url=None,
        demo_auth_enabled=False,
    )
    app = create_app(
        settings,
        repository=MemoryPlatformRepository(),
        executor=FakeExecutor(),
    )
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield async_client
    await app.state.platform_service.close()
