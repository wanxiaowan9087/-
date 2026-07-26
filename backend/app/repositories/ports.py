from __future__ import annotations

from contextlib import AbstractAsyncContextManager
from datetime import datetime
from typing import Any, Protocol
from uuid import UUID

from backend.app.domain.records import (
    FeedbackRecord,
    IdempotencyRecord,
    MemoryRecord,
    MessageRecord,
    ReviewRecord,
    RunRecord,
    SessionRecord,
    StreamEventRecord,
)


class PlatformTransaction(Protocol):
    async def claim_idempotency(
        self,
        *,
        subject_id: str,
        operation_id: str,
        normalized_path: str,
        key_digest: str,
        request_digest: str,
        now: datetime,
        expires_at: datetime,
    ) -> tuple[str, IdempotencyRecord | None]: ...

    async def finish_idempotency(
        self,
        record: IdempotencyRecord,
        *,
        status_code: int,
        response_body: dict[str, Any] | None,
        run_id: UUID | None = None,
    ) -> None: ...

    async def create_session(self, owner_id: str, title: str, now: datetime) -> SessionRecord: ...

    async def list_sessions(
        self, owner_id: str, *, limit: int, after: tuple[datetime | None, UUID] | None
    ) -> list[SessionRecord]: ...

    async def get_session(self, owner_id: str, session_id: UUID) -> SessionRecord | None: ...

    async def list_messages(
        self, owner_id: str, session_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[MessageRecord]: ...

    async def get_message(self, owner_id: str, message_id: UUID) -> MessageRecord | None: ...

    async def prepare_chat(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        content: str | None,
        original_user_message_id: UUID | None,
        now: datetime,
    ) -> tuple[MessageRecord, MessageRecord, RunRecord]: ...

    async def add_event(self, event: StreamEventRecord) -> None: ...

    async def list_events(self, run_id: UUID, after_sequence: int) -> list[StreamEventRecord]: ...

    async def get_run(self, owner_id: str, run_id: UUID) -> RunRecord | None: ...

    async def update_run(
        self,
        run_id: UUID,
        *,
        expected_statuses: set[str],
        status: str,
        now: datetime,
        assistant_content: str | None = None,
    ) -> RunRecord | None: ...

    async def create_feedback(
        self,
        owner_id: str,
        message_id: UUID,
        rating: str,
        reason: str | None,
        comment: str | None,
        now: datetime,
    ) -> FeedbackRecord: ...

    async def list_memories(
        self,
        owner_id: str,
        *,
        status: str | None,
        memory_type: str | None,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[MemoryRecord]: ...

    async def create_memory(
        self,
        owner_id: str,
        *,
        memory_type: str,
        content: str,
        confidence: float,
        source_message_id: UUID,
        now: datetime,
    ) -> MemoryRecord: ...

    async def get_memory(self, owner_id: str, memory_id: UUID) -> MemoryRecord | None: ...

    async def update_memory(
        self,
        owner_id: str,
        memory_id: UUID,
        *,
        expected_version: int,
        content: str | None,
        deactivate: bool,
        now: datetime,
    ) -> MemoryRecord | None: ...

    async def delete_memory(
        self, owner_id: str, memory_id: UUID, *, expected_version: int, now: datetime
    ) -> MemoryRecord | None: ...

    async def list_reviews(
        self,
        *,
        status: str | None,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[ReviewRecord]: ...

    async def create_review(
        self,
        *,
        run_id: UUID,
        session_id: UUID,
        owner_id: str,
        user_message_id: UUID,
        candidate_content: str,
        reason_codes: list[str],
        confidence: float,
        now: datetime,
    ) -> ReviewRecord: ...

    async def get_review(self, review_id: UUID) -> ReviewRecord | None: ...

    async def decide_review(
        self,
        review_id: UUID,
        *,
        reviewer_id: str,
        request_id: str,
        expected_version: int,
        decision: str,
        edited_content: str | None,
        now: datetime,
    ) -> tuple[ReviewRecord | None, UUID | None, bool]: ...


class PlatformRepository(Protocol):
    def transaction(self) -> AbstractAsyncContextManager[PlatformTransaction]: ...

    async def ping(self) -> bool: ...

    async def close(self) -> None: ...
