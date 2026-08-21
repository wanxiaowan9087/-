from __future__ import annotations

from collections.abc import AsyncIterator
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from backend.app.adapters.sql.database import create_session_factory
from backend.app.adapters.sql.models import Base
from backend.app.adapters.sql.repository import SqlPlatformRepository
from backend.app.application.ports import RunExecution
from backend.app.core.config import Settings
from backend.app.main import create_app


class SqlFakeExecutor:
    def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, object]]]:
        async def events() -> AsyncIterator[tuple[str, dict[str, object]]]:
            yield "delta", {"index": 0, "content": "sql"}
            yield (
                "done",
                {
                    "outcome": "completed",
                    "assistant_message_id": str(execution.assistant_message_id),
                    "finish_reason": "stop",
                    "usage": None,
                },
            )

        return events()

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id

    async def health(self) -> str:
        return "available"

    async def close(self) -> None:
        return None


@pytest_asyncio.fixture
async def sql_client(tmp_path: object) -> AsyncIterator[AsyncClient]:
    path = str(tmp_path).replace("\\", "/") + "/platform.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    repository = SqlPlatformRepository(create_session_factory(engine), engine)
    app = create_app(
        Settings(
            environment="test",
            database_url=f"sqlite+aiosqlite:///{path}",
            redis_url=None,
            demo_auth_enabled=True,
        ),
        repository=repository,
        executor=SqlFakeExecutor(),
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client
    await app.state.platform_service.close()


@pytest.mark.asyncio
async def test_sql_repository_transaction_and_stream(sql_client: AsyncClient) -> None:
    headers = {"Authorization": "Bearer sql-user:user", "Idempotency-Key": "sql-session-key-001"}
    created = await sql_client.post("/api/v1/sessions", headers=headers, json={"title": "SQL"})
    assert created.status_code == 201
    session_id = created.json()["data"]["id"]
    streamed = await sql_client.post(
        "/api/v1/chat/stream",
        headers={"Authorization": "Bearer sql-user:user", "Idempotency-Key": "sql-stream-key-001"},
        json={"mode": "new", "session_id": session_id, "content": "hello"},
    )
    assert streamed.status_code == 200
    assert "event: done" in streamed.text
    messages = await sql_client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": "Bearer sql-user:user"},
    )
    assert messages.status_code == 200
    assert [item["role"] for item in messages.json()["data"]["items"]] == ["user", "assistant"]
