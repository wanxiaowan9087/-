from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from uuid import UUID

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.application.ports import RunExecution
from backend.app.application.usage_summary import UsageSummaryWorker
from backend.app.core.config import Settings
from backend.app.main import create_app


@dataclass(frozen=True)
class ScenarioHarness:
    """HTTP client plus the isolated fixture store used to arrange API scenarios."""

    client: AsyncClient
    repository: MemoryPlatformRepository
    usage_summary_worker: UsageSummaryWorker


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
        sms_provider="fake",
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
async def scenario_harness() -> AsyncIterator[ScenarioHarness]:
    """Provide an isolated FastAPI application for the offline scenario suite.

    The scenarios arrange memory and review candidates through the repository because
    the frozen public contract intentionally exposes no create endpoints for those
    internal records. Every behaviour under test still crosses the HTTP route.
    """

    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./scenario-test.db",
        redis_url=None,
        demo_auth_enabled=True,
    )
    repository = MemoryPlatformRepository()
    app = create_app(settings, repository=repository, executor=FakeExecutor())
    async with AsyncClient(
        transport=ASGITransport(app=app), base_url="http://test"
    ) as async_client:
        yield ScenarioHarness(
            client=async_client,
            repository=repository,
            usage_summary_worker=app.state.usage_summary_worker,
        )
    await app.state.platform_service.close()
