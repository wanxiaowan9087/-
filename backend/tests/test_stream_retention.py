from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.application.ports import RunExecution
from backend.app.application.service import PlatformService
from backend.app.core.context import bind_context
from backend.app.core.errors import AppError
from backend.app.core.security import Principal
from backend.app.schemas.resources import CreateSessionRequest, NewChatRequest


class TerminalExecutor:
    async def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, object]]]:
        yield "status", {"phase": "accepted", "detail": None}
        yield (
            "done",
            {
                "outcome": "completed",
                "assistant_message_id": str(execution.assistant_message_id),
                "finish_reason": "stop",
                "usage": None,
            },
        )

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id

    async def health(self) -> str:
        return "available"

    async def close(self) -> None:
        return None


@pytest.mark.asyncio
async def test_expired_stream_replay_is_rejected_before_streaming() -> None:
    repository = MemoryPlatformRepository()
    service = PlatformService(
        repository,
        TerminalExecutor(),
        cursor_secret="stream-test-secret",
        idempotency_ttl_seconds=200_000,
        stream_retention_seconds=86_400,
    )
    principal = Principal(subject_id="alice", role="user")
    tokens = bind_context("req_stream_retention")
    try:
        _, session, _ = await service.create_session(
            principal, "session-stream-retention", CreateSessionRequest()
        )
        request = NewChatRequest(
            mode="new",
            session_id=session.data.id,
            content="test replay retention",
        )
        _, run_id, _, stream = await service.prepare_stream(
            principal,
            "stream-retention-key-0001",
            request,
            0,
        )
        _ = [frame async for frame in stream]
        repository.runs[run_id].ended_at = datetime.now(UTC) - timedelta(days=2)

        with pytest.raises(AppError) as captured:
            await service.prepare_stream(
                principal,
                "stream-retention-key-0001",
                request,
                1,
            )
        assert captured.value.status_code == 410
        assert captured.value.code == "STREAM_REPLAY_EXPIRED"
    finally:
        tokens.reset()
        await service.close()


@pytest.mark.asyncio
async def test_last_event_id_cannot_be_ahead_of_stream() -> None:
    repository = MemoryPlatformRepository()
    service = PlatformService(
        repository,
        TerminalExecutor(),
        cursor_secret="stream-test-secret",
        idempotency_ttl_seconds=200_000,
        stream_retention_seconds=86_400,
    )
    principal = Principal(subject_id="alice", role="user")
    tokens = bind_context("req_stream_sequence")
    try:
        _, session, _ = await service.create_session(
            principal, "session-stream-sequence", CreateSessionRequest()
        )
        request = NewChatRequest(
            mode="new",
            session_id=session.data.id,
            content="test invalid sequence",
        )
        _, _, _, stream = await service.prepare_stream(
            principal,
            "stream-sequence-key-0001",
            request,
            0,
        )
        _ = [frame async for frame in stream]

        with pytest.raises(AppError) as captured:
            await service.prepare_stream(
                principal,
                "stream-sequence-key-0001",
                request,
                999,
            )
        assert captured.value.status_code == 400
        assert captured.value.code == "BAD_REQUEST"
    finally:
        tokens.reset()
        await service.close()
