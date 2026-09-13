from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from backend.app.adapters.sql.database import create_session_factory
from backend.app.adapters.sql.models import (
    Base,
    IdempotencyModel,
    MemoryModel,
    MessageModel,
    RunModel,
)
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


@dataclass(frozen=True)
class SqlHarness:
    client: AsyncClient
    repository: SqlPlatformRepository
    engine: AsyncEngine


@pytest_asyncio.fixture
async def sql_harness(tmp_path: object) -> AsyncIterator[SqlHarness]:
    path = str(tmp_path).replace("\\", "/") + "/platform.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")

    @event.listens_for(engine.sync_engine, "connect")
    def _enable_sqlite_foreign_keys(dbapi_connection: object, _: object) -> None:
        cursor = dbapi_connection.cursor()  # type: ignore[attr-defined]
        cursor.execute("PRAGMA foreign_keys=ON")
        cursor.close()

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
        yield SqlHarness(client=client, repository=repository, engine=engine)
    await app.state.platform_service.close()


@pytest_asyncio.fixture
async def sql_client(sql_harness: SqlHarness) -> AsyncIterator[AsyncClient]:
    yield sql_harness.client


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


@pytest.mark.asyncio
async def test_delete_session_removes_run_idempotency_and_source_memory(
    sql_harness: SqlHarness,
) -> None:
    headers = {
        "Authorization": "Bearer sql-delete-user:user",
        "Idempotency-Key": "sql-delete-session-key-001",
    }
    created = await sql_harness.client.post(
        "/api/v1/sessions", headers=headers, json={"title": "SQL delete"}
    )
    session_id = created.json()["data"]["id"]
    streamed = await sql_harness.client.post(
        "/api/v1/chat/stream",
        headers={
            "Authorization": "Bearer sql-delete-user:user",
            "Idempotency-Key": "sql-delete-stream-key-001",
        },
        json={"mode": "new", "session_id": session_id, "content": "记住我偏好安静模式"},
    )
    assert streamed.status_code == 200
    messages = await sql_harness.client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": "Bearer sql-delete-user:user"},
    )
    user_message_id = UUID(messages.json()["data"]["items"][0]["id"])
    async with sql_harness.repository.transaction() as transaction:
        memory = await transaction.create_memory(
            "sql-delete-user",
            memory_type="preference",
            content="用户偏好安静模式",
            confidence=0.95,
            source_message_id=user_message_id,
            now=datetime.now(UTC),
        )

    deleted = await sql_harness.client.delete(
        f"/api/v1/sessions/{session_id}",
        headers={"Authorization": "Bearer sql-delete-user:user"},
    )

    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"session_id": session_id, "deleted": True}
    missing = await sql_harness.client.get(
        f"/api/v1/sessions/{session_id}/messages",
        headers={"Authorization": "Bearer sql-delete-user:user"},
    )
    assert missing.status_code == 404
    async with sql_harness.engine.connect() as connection:
        assert await connection.scalar(
            select(func.count()).select_from(MemoryModel).where(MemoryModel.id == memory.id)
        ) == 0
        assert await connection.scalar(
            select(func.count()).select_from(MessageModel).where(
                MessageModel.session_id == UUID(session_id)
            )
        ) == 0
        assert await connection.scalar(
            select(func.count()).select_from(RunModel).where(
                RunModel.session_id == UUID(session_id)
            )
        ) == 0
        assert await connection.scalar(
            select(func.count()).select_from(IdempotencyModel).where(
                IdempotencyModel.run_id.is_not(None),
                IdempotencyModel.subject_id == "sql-delete-user",
            )
        ) == 0
