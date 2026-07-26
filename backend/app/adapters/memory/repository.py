from __future__ import annotations

import asyncio
import copy
import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from backend.app.core.errors import AppError, conflict, not_found
from backend.app.domain.records import (
    FeedbackRecord,
    IdempotencyRecord,
    MemoryRecord,
    MessageRecord,
    ReviewAuditRecord,
    ReviewRecord,
    RunRecord,
    SessionRecord,
    StreamEventRecord,
)


class MemoryPlatformRepository:
    """Serializable fake adapter; snapshot rollback mirrors a database transaction."""

    def __init__(self) -> None:
        self.sessions: dict[UUID, SessionRecord] = {}
        self.messages: dict[UUID, MessageRecord] = {}
        self.runs: dict[UUID, RunRecord] = {}
        self.events: dict[UUID, list[StreamEventRecord]] = {}
        self.memories: dict[UUID, MemoryRecord] = {}
        self.reviews: dict[UUID, ReviewRecord] = {}
        self.feedback: dict[UUID, FeedbackRecord] = {}
        self.idempotency: dict[tuple[str, str, str, str], IdempotencyRecord] = {}
        self.review_audits: list[ReviewAuditRecord] = []
        self._lock = asyncio.Lock()

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[MemoryPlatformRepository]:
        async with self._lock:
            snapshot = copy.deepcopy(
                (
                    self.sessions,
                    self.messages,
                    self.runs,
                    self.events,
                    self.memories,
                    self.reviews,
                    self.feedback,
                    self.idempotency,
                    self.review_audits,
                )
            )
            try:
                yield self
            except Exception:
                (
                    self.sessions,
                    self.messages,
                    self.runs,
                    self.events,
                    self.memories,
                    self.reviews,
                    self.feedback,
                    self.idempotency,
                    self.review_audits,
                ) = snapshot
                raise

    async def ping(self) -> bool:
        return True

    async def close(self) -> None:
        return None

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
    ) -> tuple[str, IdempotencyRecord | None]:
        lookup = (subject_id, operation_id, normalized_path, key_digest)
        existing = self.idempotency.get(lookup)
        if existing is not None and existing.expires_at <= now:
            del self.idempotency[lookup]
            existing = None
        if existing is not None:
            if existing.request_digest != request_digest:
                return "reused", existing
            if existing.state == "in_progress":
                return "in_progress", existing
            return "replay", existing
        record = IdempotencyRecord(
            subject_id=subject_id,
            operation_id=operation_id,
            normalized_path=normalized_path,
            key_digest=key_digest,
            request_digest=request_digest,
            state="in_progress",
            status_code=None,
            response_body=None,
            run_id=None,
            created_at=now,
            expires_at=expires_at,
        )
        self.idempotency[lookup] = record
        return "new", record

    async def finish_idempotency(
        self,
        record: IdempotencyRecord,
        *,
        status_code: int,
        response_body: dict[str, Any] | None,
        run_id: UUID | None = None,
    ) -> None:
        record.state = "completed"
        record.status_code = status_code
        record.response_body = copy.deepcopy(response_body)
        record.run_id = run_id

    async def create_session(self, owner_id: str, title: str, now: datetime) -> SessionRecord:
        record = SessionRecord(uuid4(), owner_id, title, now, now)
        self.sessions[record.id] = record
        return record

    async def list_sessions(
        self, owner_id: str, *, limit: int, after: tuple[datetime | None, UUID] | None
    ) -> list[SessionRecord]:
        records = [item for item in self.sessions.values() if item.owner_id == owner_id]
        records.sort(
            key=lambda item: (
                item.last_message_at or datetime.min.replace(tzinfo=item.created_at.tzinfo),
                item.id,
            ),
            reverse=True,
        )
        if after:
            after_time, after_id = after
            marker = after_time or datetime.min.replace(
                tzinfo=records[0].created_at.tzinfo if records else None
            )
            records = [
                item
                for item in records
                if (
                    item.last_message_at
                    or datetime.min.replace(tzinfo=item.created_at.tzinfo)
                    or item.created_at
                )
                < marker
                or (
                    (item.last_message_at or datetime.min.replace(tzinfo=item.created_at.tzinfo))
                    == marker
                    and item.id.int < after_id.int
                )
            ]
        return records[:limit]

    async def get_session(self, owner_id: str, session_id: UUID) -> SessionRecord | None:
        item = self.sessions.get(session_id)
        return item if item and item.owner_id == owner_id else None

    async def list_messages(
        self, owner_id: str, session_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[MessageRecord]:
        if await self.get_session(owner_id, session_id) is None:
            raise not_found()
        records = [
            item
            for item in self.messages.values()
            if item.owner_id == owner_id and item.session_id == session_id
        ]
        records.sort(key=lambda item: (item.created_at, item.id))
        if after:
            records = [
                item
                for item in records
                if item.created_at is not None
                and (item.created_at, item.id.int) > (after[0], after[1].int)
            ]
        return records[:limit]

    async def get_message(self, owner_id: str, message_id: UUID) -> MessageRecord | None:
        item = self.messages.get(message_id)
        return item if item and item.owner_id == owner_id else None

    async def prepare_chat(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        content: str | None,
        original_user_message_id: UUID | None,
        now: datetime,
    ) -> tuple[MessageRecord, MessageRecord, RunRecord]:
        session = await self.get_session(owner_id, session_id)
        if session is None:
            raise not_found()
        if original_user_message_id is None:
            if content is None:
                raise AppError("BAD_REQUEST", "new chat requires content", 400)
            user_message = MessageRecord(
                uuid4(),
                session_id,
                owner_id,
                "user",
                "completed",
                content,
                None,
                None,
                [],
                now,
                now,
            )
            self.messages[user_message.id] = user_message
            attempt = 1
        else:
            original = await self.get_message(owner_id, original_user_message_id)
            if original is None or original.session_id != session_id or original.role != "user":
                raise not_found()
            active = [
                run
                for run in self.runs.values()
                if run.owner_id == owner_id
                and run.retry_of_user_message_id == original.id
                and run.status in {"queued", "running", "needs_review"}
            ]
            if active:
                raise conflict("an active retry already exists")
            user_message = original
            attempt = (
                max(
                    [
                        run.attempt
                        for run in self.runs.values()
                        if run.retry_of_user_message_id == original.id
                    ],
                    default=1,
                )
                + 1
            )
        assistant_now = now + timedelta(microseconds=1)
        assistant = MessageRecord(
            uuid4(),
            session_id,
            owner_id,
            "assistant",
            "accepted",
            "",
            user_message.id,
            None,
            [],
            assistant_now,
            assistant_now,
        )
        run = RunRecord(
            uuid4(),
            session_id,
            owner_id,
            user_message.id,
            assistant.id,
            "queued",
            attempt,
            original_user_message_id,
            started_at=now,
        )
        assistant.run_id = run.id
        self.messages[assistant.id] = assistant
        self.runs[run.id] = run
        session.last_message_at = now
        session.updated_at = now
        return user_message, assistant, run

    async def add_event(self, event: StreamEventRecord) -> None:
        events = self.events.setdefault(event.run_id, [])
        if events and events[-1].event["event_type"] in {"done", "error"}:
            raise conflict("cannot append after terminal stream event")
        if events and event.sequence != events[-1].sequence + 1:
            raise conflict("stream sequence must increase by one")
        if not events and event.sequence != 1:
            raise conflict("first stream sequence must be one")
        events.append(event)

    async def list_events(self, run_id: UUID, after_sequence: int) -> list[StreamEventRecord]:
        return [item for item in self.events.get(run_id, []) if item.sequence > after_sequence]

    async def get_run(self, owner_id: str, run_id: UUID) -> RunRecord | None:
        item = self.runs.get(run_id)
        return item if item and item.owner_id == owner_id else None

    async def update_run(
        self,
        run_id: UUID,
        *,
        expected_statuses: set[str],
        status: str,
        now: datetime,
        assistant_content: str | None = None,
    ) -> RunRecord | None:
        run = self.runs.get(run_id)
        if run is None or run.status not in expected_statuses:
            return None
        run.status = status
        if status in {"completed", "cancelled", "failed", "rejected", "needs_review"}:
            run.ended_at = now
        assistant = self.messages[run.assistant_message_id]
        assistant.status = status if status != "running" else "generating"
        assistant.updated_at = now
        if assistant_content is not None and status == "completed":
            assistant.content = assistant_content
        return run

    async def create_feedback(
        self,
        owner_id: str,
        message_id: UUID,
        rating: str,
        reason: str | None,
        comment: str | None,
        now: datetime,
    ) -> FeedbackRecord:
        message = await self.get_message(owner_id, message_id)
        if (
            message is None
            or message.role != "assistant"
            or message.status != "completed"
            or not message.content
        ):
            raise not_found()
        if any(
            item.owner_id == owner_id and item.message_id == message_id
            for item in self.feedback.values()
        ):
            raise conflict("feedback already exists")
        record = FeedbackRecord(uuid4(), message_id, owner_id, rating, reason, comment, now)
        self.feedback[record.id] = record
        return record

    async def list_memories(
        self,
        owner_id: str,
        *,
        status: str | None,
        memory_type: str | None,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[MemoryRecord]:
        items = [
            item
            for item in self.memories.values()
            if item.owner_id == owner_id
            and item.status != "deleted"
            and (status is None or item.status == status)
            and (memory_type is None or item.memory_type == memory_type)
        ]
        items.sort(key=lambda item: (item.updated_at, item.id), reverse=True)
        if after:
            items = [
                item for item in items if (item.updated_at, item.id.int) < (after[0], after[1].int)
            ]
        return items[:limit]

    async def get_memory(self, owner_id: str, memory_id: UUID) -> MemoryRecord | None:
        item = self.memories.get(memory_id)
        return item if item and item.owner_id == owner_id and item.status != "deleted" else None

    async def create_memory(
        self,
        owner_id: str,
        *,
        memory_type: str,
        content: str,
        confidence: float,
        source_message_id: UUID,
        now: datetime,
    ) -> MemoryRecord:
        item = MemoryRecord(
            uuid4(),
            owner_id,
            memory_type,
            content,
            "active",
            confidence,
            source_message_id,
            None,
            1,
            now,
            now,
        )
        self.memories[item.id] = item
        return item

    async def update_memory(
        self,
        owner_id: str,
        memory_id: UUID,
        *,
        expected_version: int,
        content: str | None,
        deactivate: bool,
        now: datetime,
    ) -> MemoryRecord | None:
        item = await self.get_memory(owner_id, memory_id)
        if item is None:
            return None
        if item.version != expected_version:
            raise conflict("memory version conflict")
        if deactivate and item.status != "active":
            raise conflict("only active memory can be deactivated")
        old_version = item.version
        item.version += 1
        item.updated_at = now
        if content is not None:
            item.content = content
            item.corrected_from_version = old_version
        if deactivate:
            item.status = "inactive"
        return item

    async def delete_memory(
        self, owner_id: str, memory_id: UUID, *, expected_version: int, now: datetime
    ) -> MemoryRecord | None:
        item = await self.get_memory(owner_id, memory_id)
        if item is None:
            return None
        if item.version != expected_version:
            raise conflict("memory version conflict")
        item.status = "deleted"
        item.version += 1
        item.updated_at = now
        item.deleted_at = now
        return item

    async def list_reviews(
        self,
        *,
        status: str | None,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[ReviewRecord]:
        items = [item for item in self.reviews.values() if status is None or item.status == status]
        items.sort(key=lambda item: (item.created_at, item.id), reverse=True)
        if after:
            items = [
                item for item in items if (item.created_at, item.id.int) < (after[0], after[1].int)
            ]
        return items[:limit]

    async def get_review(self, review_id: UUID) -> ReviewRecord | None:
        return self.reviews.get(review_id)

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
    ) -> ReviewRecord:
        item = ReviewRecord(
            uuid4(),
            run_id,
            session_id,
            owner_id,
            user_message_id,
            candidate_content,
            reason_codes,
            confidence,
            "pending",
            1,
            now,
        )
        self.reviews[item.id] = item
        return item

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
    ) -> tuple[ReviewRecord | None, UUID | None, bool]:
        item = self.reviews.get(review_id)
        if item is None:
            return None, None, False
        old_status = item.status
        success = item.status == "pending" and item.version == expected_version
        target = {
            "approve": "approved",
            "reject": "rejected",
            "edit_and_publish": "edited_and_published",
        }[decision]
        self.review_audits.append(
            ReviewAuditRecord(
                uuid4(),
                review_id,
                reviewer_id,
                request_id,
                old_status,
                target,
                hashlib.sha256((edited_content or item.candidate_content).encode()).hexdigest(),
                success,
                now,
            )
        )
        if not success:
            return item, None, False
        item.status = target
        item.version += 1
        item.decided_at = now
        run = self.runs[item.run_id]
        message = self.messages[run.assistant_message_id]
        published: UUID | None = None
        if decision in {"approve", "edit_and_publish"}:
            message.content = (
                edited_content if edited_content is not None else item.candidate_content
            )
            message.status = "completed"
            message.updated_at = now
            run.status = "completed"
            published = message.id
        else:
            message.status = "rejected"
            message.updated_at = now
            run.status = "rejected"
        run.ended_at = now
        return item, published, True
