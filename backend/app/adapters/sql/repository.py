from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime, timedelta
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import and_, desc, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from backend.app.core.errors import AppError, conflict, not_found
from backend.app.domain.records import (
    FeedbackRecord,
    ExternalIdentityMappingRecord,
    IdempotencyRecord,
    MemoryRecord,
    MessageRecord,
    ReviewRecord,
    RunRecord,
    SessionRecord,
    StreamEventRecord,
)

from .models import (
    FeedbackModel,
    ExternalIdentityMappingModel,
    IdempotencyModel,
    MemoryModel,
    MessageModel,
    ReviewAuditModel,
    ReviewModel,
    RunModel,
    SessionModel,
    StreamEventModel,
)


def _session(row: SessionModel) -> SessionRecord:
    return SessionRecord(
        row.id,
        row.owner_id,
        row.title,
        row.created_at,
        row.updated_at,
        row.last_message_at,
        row.memory_summary,
        row.summary_through_created_at,
        row.summary_through_message_id,
    )


def _message(row: MessageModel) -> MessageRecord:
    return MessageRecord(
        row.id,
        row.session_id,
        row.owner_id,
        row.role,
        row.status,
        row.content,
        row.reply_to_message_id,
        row.run_id,
        row.citations or [],
        row.created_at,
        row.updated_at,
        row.product_recommendations or [],
    )


def _run(row: RunModel) -> RunRecord:
    return RunRecord(
        row.id,
        row.session_id,
        row.owner_id,
        row.user_message_id,
        row.assistant_message_id,
        row.status,
        row.attempt,
        row.retry_of_user_message_id,
        row.model,
        row.retrieval_strategy,
        row.confidence_threshold,
        row.cancellation_requested_at,
        row.started_at,
        row.ended_at,
        row.steps or [],
        row.citations or [],
    )


def _memory(row: MemoryModel) -> MemoryRecord:
    return MemoryRecord(
        row.id,
        row.owner_id,
        row.memory_type,
        row.content,
        row.status,
        row.confidence,
        row.source_message_id,
        row.corrected_from_version,
        row.version,
        row.created_at,
        row.updated_at,
        row.deleted_at,
    )


def _review(row: ReviewModel) -> ReviewRecord:
    return ReviewRecord(
        row.id,
        row.run_id,
        row.session_id,
        row.owner_id,
        row.user_message_id,
        row.candidate_content,
        row.reason_codes,
        row.confidence,
        row.status,
        row.version,
        row.created_at,
        row.decided_at,
    )


def _idem(row: IdempotencyModel) -> IdempotencyRecord:
    return IdempotencyRecord(
        row.subject_id,
        row.operation_id,
        row.normalized_path,
        row.key_digest,
        row.request_digest,
        row.state,
        row.status_code,
        row.response_body,
        row.run_id,
        row.created_at,
        row.expires_at,
    )


class SqlPlatformTransaction:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        criteria = (
            IdempotencyModel.subject_id == subject_id,
            IdempotencyModel.operation_id == operation_id,
            IdempotencyModel.normalized_path == normalized_path,
            IdempotencyModel.key_digest == key_digest,
        )
        existing = (
            await self.session.execute(select(IdempotencyModel).where(*criteria).with_for_update())
        ).scalar_one_or_none()
        if existing is not None and existing.expires_at <= now:
            await self.session.delete(existing)
            await self.session.flush()
            existing = None
        if existing is not None:
            record = _idem(existing)
            if existing.request_digest != request_digest:
                return "reused", record
            if existing.state == "in_progress":
                return "in_progress", record
            return "replay", record
        row = IdempotencyModel(
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
        try:
            async with self.session.begin_nested():
                self.session.add(row)
                await self.session.flush()
        except IntegrityError:
            existing = (
                await self.session.execute(
                    select(IdempotencyModel).where(*criteria).with_for_update()
                )
            ).scalar_one()
            record = _idem(existing)
            if existing.request_digest != request_digest:
                return "reused", record
            return ("in_progress" if existing.state == "in_progress" else "replay"), record
        return "new", _idem(row)

    async def finish_idempotency(
        self,
        record: IdempotencyRecord,
        *,
        status_code: int,
        response_body: dict[str, Any] | None,
        run_id: UUID | None = None,
    ) -> None:
        row = (
            await self.session.execute(
                select(IdempotencyModel).where(
                    IdempotencyModel.subject_id == record.subject_id,
                    IdempotencyModel.operation_id == record.operation_id,
                    IdempotencyModel.normalized_path == record.normalized_path,
                    IdempotencyModel.key_digest == record.key_digest,
                )
            )
        ).scalar_one()
        row.state = "completed"
        row.status_code = status_code
        row.response_body = response_body
        row.run_id = run_id
        await self.session.flush()

    async def create_session(self, owner_id: str, title: str, now: datetime) -> SessionRecord:
        row = SessionModel(
            id=uuid4(),
            owner_id=owner_id,
            title=title,
            created_at=now,
            updated_at=now,
            last_message_at=None,
            memory_summary=None,
            summary_through_created_at=None,
            summary_through_message_id=None,
        )
        self.session.add(row)
        await self.session.flush()
        return _session(row)

    async def list_sessions(
        self, owner_id: str, *, limit: int, after: tuple[datetime | None, UUID] | None
    ) -> list[SessionRecord]:
        statement = select(SessionModel).where(SessionModel.owner_id == owner_id)
        if after:
            timestamp, item_id = after
            if timestamp is None:
                statement = statement.where(
                    SessionModel.last_message_at.is_(None), SessionModel.id < item_id
                )
            else:
                statement = statement.where(
                    or_(
                        SessionModel.last_message_at < timestamp,
                        and_(
                            SessionModel.last_message_at == timestamp,
                            SessionModel.id < item_id,
                        ),
                        SessionModel.last_message_at.is_(None),
                    )
                )
        statement = statement.order_by(
            desc(SessionModel.last_message_at).nulls_last(), desc(SessionModel.id)
        ).limit(limit)
        return [_session(row) for row in (await self.session.execute(statement)).scalars()]

    async def get_session(self, owner_id: str, session_id: UUID) -> SessionRecord | None:
        row = (
            await self.session.execute(
                select(SessionModel).where(
                    SessionModel.id == session_id, SessionModel.owner_id == owner_id
                )
            )
        ).scalar_one_or_none()
        return _session(row) if row else None

    async def update_session_title(
        self, owner_id: str, session_id: UUID, title: str, now: datetime
    ) -> SessionRecord | None:
        row = (
            await self.session.execute(
                select(SessionModel)
                .where(SessionModel.id == session_id, SessionModel.owner_id == owner_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.title = title
        row.updated_at = now
        await self.session.flush()
        return _session(row)

    async def upsert_external_identity_mapping(self, platform_user_id: UUID, external_user_id: str, created_by: UUID, now: datetime) -> ExternalIdentityMappingRecord:
        row = await self.session.get(ExternalIdentityMappingModel, platform_user_id)
        if row is None:
            row = ExternalIdentityMappingModel(platform_user_id=platform_user_id, external_user_id=external_user_id, created_by=created_by, created_at=now, updated_at=now, active=True)
            self.session.add(row)
        else:
            row.external_user_id = external_user_id
            row.updated_at = now
            row.active = True
        await self.session.flush()
        return ExternalIdentityMappingRecord(row.platform_user_id, row.external_user_id, row.created_by, row.created_at, row.updated_at, row.active)

    async def resolve_external_user_id(self, platform_user_id: str) -> str | None:
        try:
            platform_uuid = UUID(platform_user_id)
        except ValueError:
            return None
        row = (await self.session.execute(select(ExternalIdentityMappingModel).where(ExternalIdentityMappingModel.platform_user_id == platform_uuid, ExternalIdentityMappingModel.active.is_(True)))).scalar_one_or_none()
        return row.external_user_id if row else None

    async def update_session_memory_summary(
        self,
        owner_id: str,
        session_id: UUID,
        *,
        summary: str,
        through_created_at: datetime,
        through_message_id: UUID,
        now: datetime,
    ) -> SessionRecord | None:
        row = (
            await self.session.execute(
                select(SessionModel)
                .where(SessionModel.id == session_id, SessionModel.owner_id == owner_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.memory_summary = summary
        row.summary_through_created_at = through_created_at
        row.summary_through_message_id = through_message_id
        row.updated_at = now
        await self.session.flush()
        return _session(row)

    async def list_messages(
        self, owner_id: str, session_id: UUID, *, limit: int, after: tuple[datetime, UUID] | None
    ) -> list[MessageRecord]:
        if await self.get_session(owner_id, session_id) is None:
            raise not_found()
        statement = select(MessageModel).where(
            MessageModel.owner_id == owner_id, MessageModel.session_id == session_id
        )
        if after:
            statement = statement.where(
                or_(
                    MessageModel.created_at > after[0],
                    and_(MessageModel.created_at == after[0], MessageModel.id > after[1]),
                )
            )
        statement = statement.order_by(MessageModel.created_at, MessageModel.id).limit(limit)
        return [_message(row) for row in (await self.session.execute(statement)).scalars()]

    async def list_recent_messages(
        self, owner_id: str, session_id: UUID, *, limit: int
    ) -> list[MessageRecord]:
        if await self.get_session(owner_id, session_id) is None:
            raise not_found()
        statement = (
            select(MessageModel)
            .where(MessageModel.owner_id == owner_id, MessageModel.session_id == session_id)
            .order_by(desc(MessageModel.created_at), desc(MessageModel.id))
            .limit(limit)
        )
        rows = list((await self.session.execute(statement)).scalars())
        rows.reverse()
        return [_message(row) for row in rows]

    async def list_messages_before(
        self,
        owner_id: str,
        session_id: UUID,
        *,
        before: tuple[datetime, UUID],
        limit: int,
    ) -> list[MessageRecord]:
        if await self.get_session(owner_id, session_id) is None:
            raise not_found()
        statement = (
            select(MessageModel)
            .where(
                MessageModel.owner_id == owner_id,
                MessageModel.session_id == session_id,
                or_(
                    MessageModel.created_at < before[0],
                    and_(
                        MessageModel.created_at == before[0],
                        MessageModel.id < before[1],
                    ),
                ),
            )
            .order_by(desc(MessageModel.created_at), desc(MessageModel.id))
            .limit(limit)
        )
        rows = list((await self.session.execute(statement)).scalars())
        rows.reverse()
        return [_message(row) for row in rows]

    async def get_message(self, owner_id: str, message_id: UUID) -> MessageRecord | None:
        row = (
            await self.session.execute(
                select(MessageModel).where(
                    MessageModel.id == message_id, MessageModel.owner_id == owner_id
                )
            )
        ).scalar_one_or_none()
        return _message(row) if row else None

    async def prepare_chat(
        self,
        *,
        owner_id: str,
        session_id: UUID,
        content: str | None,
        original_user_message_id: UUID | None,
        session_title: str | None,
        now: datetime,
    ) -> tuple[MessageRecord, MessageRecord, RunRecord]:
        session_row = (
            await self.session.execute(
                select(SessionModel)
                .where(SessionModel.id == session_id, SessionModel.owner_id == owner_id)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if session_row is None:
            raise not_found()
        if session_title and session_row.title.strip() in {
            "",
            "新会话",
            "New agent session",
            "Agent session",
            "Untitled session",
        }:
            session_row.title = session_title
        if original_user_message_id is None:
            if content is None:
                raise AppError("BAD_REQUEST", "new chat requires content", 400)
            user_row = MessageModel(
                id=uuid4(),
                session_id=session_id,
                owner_id=owner_id,
                role="user",
                status="completed",
                content=content,
                reply_to_message_id=None,
                run_id=None,
                citations=[],
                product_recommendations=[],
                created_at=now,
                updated_at=now,
            )
            self.session.add(user_row)
            await self.session.flush()
            attempt = 1
        else:
            original_row = (
                await self.session.execute(
                    select(MessageModel).where(
                        MessageModel.id == original_user_message_id,
                        MessageModel.owner_id == owner_id,
                        MessageModel.session_id == session_id,
                        MessageModel.role == "user",
                    )
                )
            ).scalar_one_or_none()
            if original_row is None:
                raise not_found()
            user_row = original_row
            active = (
                await self.session.execute(
                    select(RunModel.id).where(
                        RunModel.owner_id == owner_id,
                        RunModel.retry_of_user_message_id == original_user_message_id,
                        RunModel.status.in_(["queued", "running", "needs_review"]),
                    )
                )
            ).first()
            if active:
                raise conflict("an active retry already exists")
            maximum = (
                await self.session.execute(
                    select(func.max(RunModel.attempt)).where(
                        RunModel.retry_of_user_message_id == original_user_message_id
                    )
                )
            ).scalar_one()
            attempt = int(maximum or 1) + 1
        assistant_now = now + timedelta(microseconds=1)
        assistant_row = MessageModel(
            id=uuid4(),
            session_id=session_id,
            owner_id=owner_id,
            role="assistant",
            status="accepted",
            content="",
            reply_to_message_id=user_row.id,
            run_id=None,
            citations=[],
            product_recommendations=[],
            created_at=assistant_now,
            updated_at=assistant_now,
        )
        self.session.add(assistant_row)
        await self.session.flush()
        run_row = RunModel(
            id=uuid4(),
            session_id=session_id,
            owner_id=owner_id,
            user_message_id=user_row.id,
            assistant_message_id=assistant_row.id,
            status="queued",
            attempt=attempt,
            retry_of_user_message_id=original_user_message_id,
            model="unconfigured",
            retrieval_strategy="unconfigured",
            confidence_threshold=0.65,
            started_at=now,
            steps=[],
            citations=[],
        )
        self.session.add(run_row)
        await self.session.flush()
        assistant_row.run_id = run_row.id
        session_row.last_message_at = now
        session_row.updated_at = now
        await self.session.flush()
        return _message(user_row), _message(assistant_row), _run(run_row)

    async def add_event(self, event: StreamEventRecord) -> None:
        latest = (
            await self.session.execute(
                select(StreamEventModel)
                .where(StreamEventModel.run_id == event.run_id)
                .order_by(desc(StreamEventModel.sequence))
                .limit(1)
                .with_for_update()
            )
        ).scalar_one_or_none()
        if latest and latest.event["event_type"] in {"done", "error"}:
            raise conflict("cannot append after terminal stream event")
        expected = 1 if latest is None else latest.sequence + 1
        if event.sequence != expected:
            raise conflict("stream sequence must increase by one")
        self.session.add(
            StreamEventModel(
                run_id=event.run_id,
                sequence=event.sequence,
                event=event.event,
                created_at=event.created_at,
            )
        )
        await self.session.flush()

    async def list_events(self, run_id: UUID, after_sequence: int) -> list[StreamEventRecord]:
        rows = (
            await self.session.execute(
                select(StreamEventModel)
                .where(
                    StreamEventModel.run_id == run_id,
                    StreamEventModel.sequence > after_sequence,
                )
                .order_by(StreamEventModel.sequence)
            )
        ).scalars()
        return [
            StreamEventRecord(row.run_id, row.sequence, row.event, row.created_at) for row in rows
        ]

    async def get_run(self, owner_id: str, run_id: UUID) -> RunRecord | None:
        row = (
            await self.session.execute(
                select(RunModel).where(RunModel.id == run_id, RunModel.owner_id == owner_id)
            )
        ).scalar_one_or_none()
        return _run(row) if row else None

    async def update_run(
        self,
        run_id: UUID,
        *,
        expected_statuses: set[str],
        status: str,
        now: datetime,
        assistant_content: str | None = None,
        model: str | None = None,
        retrieval_strategy: str | None = None,
        confidence_threshold: float | None = None,
        steps: list[dict[str, Any]] | None = None,
        citations: list[dict[str, Any]] | None = None,
        product_recommendations: list[dict[str, Any]] | None = None,
    ) -> RunRecord | None:
        row = (
            await self.session.execute(
                select(RunModel)
                .where(RunModel.id == run_id, RunModel.status.in_(expected_statuses))
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        row.status = status
        if model is not None:
            row.model = model
        if retrieval_strategy is not None:
            row.retrieval_strategy = retrieval_strategy
        if confidence_threshold is not None:
            row.confidence_threshold = confidence_threshold
        if steps is not None:
            row.steps = steps
        if citations is not None:
            row.citations = citations
        if product_recommendations is not None:
            row.product_recommendations = product_recommendations
        if status in {"completed", "cancelled", "failed", "rejected", "needs_review"}:
            row.ended_at = now
        message = await self.session.get(MessageModel, row.assistant_message_id)
        if message is None:
            raise RuntimeError("assistant message missing")
        message.status = status if status != "running" else "generating"
        message.updated_at = now
        if assistant_content is not None and status == "completed":
            message.content = assistant_content
        if citations is not None:
            message.citations = citations
        if product_recommendations is not None:
            message.product_recommendations = product_recommendations
        await self.session.flush()
        return _run(row)

    async def create_feedback(
        self,
        owner_id: str,
        message_id: UUID,
        rating: str,
        reason: str | None,
        comment: str | None,
        now: datetime,
    ) -> FeedbackRecord:
        message = (
            await self.session.execute(
                select(MessageModel).where(
                    MessageModel.id == message_id,
                    MessageModel.owner_id == owner_id,
                    MessageModel.role == "assistant",
                    MessageModel.status == "completed",
                    MessageModel.content != "",
                )
            )
        ).scalar_one_or_none()
        if message is None:
            raise not_found()
        duplicate = (
            await self.session.execute(
                select(FeedbackModel.id).where(
                    FeedbackModel.owner_id == owner_id,
                    FeedbackModel.message_id == message_id,
                )
            )
        ).first()
        if duplicate:
            raise conflict("feedback already exists")
        row = FeedbackModel(
            id=uuid4(),
            message_id=message_id,
            owner_id=owner_id,
            rating=rating,
            reason=reason,
            comment=comment,
            created_at=now,
        )
        self.session.add(row)
        await self.session.flush()
        return FeedbackRecord(
            row.id,
            row.message_id,
            row.owner_id,
            row.rating,
            row.reason,
            row.comment,
            row.created_at,
        )

    async def list_memories(
        self,
        owner_id: str,
        *,
        status: str | None,
        memory_type: str | None,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[MemoryRecord]:
        statement = select(MemoryModel).where(
            MemoryModel.owner_id == owner_id, MemoryModel.status != "deleted"
        )
        if status:
            statement = statement.where(MemoryModel.status == status)
        if memory_type:
            statement = statement.where(MemoryModel.memory_type == memory_type)
        if after:
            statement = statement.where(
                or_(
                    MemoryModel.updated_at < after[0],
                    and_(MemoryModel.updated_at == after[0], MemoryModel.id < after[1]),
                )
            )
        statement = statement.order_by(desc(MemoryModel.updated_at), desc(MemoryModel.id)).limit(
            limit
        )
        return [_memory(row) for row in (await self.session.execute(statement)).scalars()]

    async def get_memory(self, owner_id: str, memory_id: UUID) -> MemoryRecord | None:
        row = (
            await self.session.execute(
                select(MemoryModel).where(
                    MemoryModel.id == memory_id,
                    MemoryModel.owner_id == owner_id,
                    MemoryModel.status != "deleted",
                )
            )
        ).scalar_one_or_none()
        return _memory(row) if row else None

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
        row = MemoryModel(
            id=uuid4(),
            owner_id=owner_id,
            memory_type=memory_type,
            content=content,
            status="active",
            confidence=confidence,
            source_message_id=source_message_id,
            corrected_from_version=None,
            version=1,
            created_at=now,
            updated_at=now,
            deleted_at=None,
        )
        self.session.add(row)
        await self.session.flush()
        return _memory(row)

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
        row = (
            await self.session.execute(
                select(MemoryModel)
                .where(
                    MemoryModel.id == memory_id,
                    MemoryModel.owner_id == owner_id,
                    MemoryModel.status != "deleted",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.version != expected_version:
            raise conflict("memory version conflict")
        if deactivate and row.status != "active":
            raise conflict("only active memory can be deactivated")
        old_version = row.version
        row.version += 1
        row.updated_at = now
        if content is not None:
            row.content = content
            row.corrected_from_version = old_version
        if deactivate:
            row.status = "inactive"
        await self.session.flush()
        return _memory(row)

    async def delete_memory(
        self, owner_id: str, memory_id: UUID, *, expected_version: int, now: datetime
    ) -> MemoryRecord | None:
        row = (
            await self.session.execute(
                select(MemoryModel)
                .where(
                    MemoryModel.id == memory_id,
                    MemoryModel.owner_id == owner_id,
                    MemoryModel.status != "deleted",
                )
                .with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None
        if row.version != expected_version:
            raise conflict("memory version conflict")
        row.status = "deleted"
        row.version += 1
        row.updated_at = now
        row.deleted_at = now
        await self.session.flush()
        return _memory(row)

    async def list_reviews(
        self,
        *,
        status: str | None,
        limit: int,
        after: tuple[datetime, UUID] | None,
    ) -> list[ReviewRecord]:
        statement = select(ReviewModel)
        if status:
            statement = statement.where(ReviewModel.status == status)
        if after:
            statement = statement.where(
                or_(
                    ReviewModel.created_at < after[0],
                    and_(ReviewModel.created_at == after[0], ReviewModel.id < after[1]),
                )
            )
        statement = statement.order_by(desc(ReviewModel.created_at), desc(ReviewModel.id)).limit(
            limit
        )
        return [_review(row) for row in (await self.session.execute(statement)).scalars()]

    async def get_review(self, review_id: UUID) -> ReviewRecord | None:
        row = await self.session.get(ReviewModel, review_id)
        return _review(row) if row else None

    async def create_review(
        self,
        *,
        review_id: UUID | None = None,
        run_id: UUID,
        session_id: UUID,
        owner_id: str,
        user_message_id: UUID,
        candidate_content: str,
        reason_codes: list[str],
        confidence: float,
        now: datetime,
    ) -> ReviewRecord:
        row = ReviewModel(
            id=review_id or uuid4(),
            run_id=run_id,
            session_id=session_id,
            owner_id=owner_id,
            user_message_id=user_message_id,
            candidate_content=candidate_content,
            reason_codes=reason_codes,
            confidence=confidence,
            status="pending",
            version=1,
            created_at=now,
            decided_at=None,
        )
        self.session.add(row)
        await self.session.flush()
        return _review(row)

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
        row = (
            await self.session.execute(
                select(ReviewModel).where(ReviewModel.id == review_id).with_for_update()
            )
        ).scalar_one_or_none()
        if row is None:
            return None, None, False
        target = {
            "approve": "approved",
            "reject": "rejected",
            "edit_and_publish": "edited_and_published",
        }[decision]
        success = row.status == "pending" and row.version == expected_version
        self.session.add(
            ReviewAuditModel(
                id=uuid4(),
                review_id=review_id,
                reviewer_id=reviewer_id,
                request_id=request_id,
                old_status=row.status,
                new_status=target,
                content_digest=hashlib.sha256(
                    (edited_content or row.candidate_content).encode()
                ).hexdigest(),
                success=success,
                created_at=now,
            )
        )
        if not success:
            await self.session.flush()
            return _review(row), None, False
        row.status = target
        row.version += 1
        row.decided_at = now
        run = await self.session.get(RunModel, row.run_id)
        if run is None:
            raise RuntimeError("review run missing")
        message = await self.session.get(MessageModel, run.assistant_message_id)
        if message is None:
            raise RuntimeError("review message missing")
        published: UUID | None = None
        if decision in {"approve", "edit_and_publish"}:
            message.content = (
                edited_content if edited_content is not None else row.candidate_content
            )
            message.status = "completed"
            run.status = "completed"
            published = message.id
        else:
            message.status = "rejected"
            run.status = "rejected"
        message.updated_at = now
        run.ended_at = now
        await self.session.flush()
        return _review(row), published, True


class SqlPlatformRepository:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], engine: AsyncEngine
    ) -> None:
        self._session_factory = session_factory
        self._engine = engine

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[SqlPlatformTransaction]:
        async with self._session_factory() as session:
            async with session.begin():
                yield SqlPlatformTransaction(session)

    async def ping(self) -> bool:
        try:
            async with self._engine.connect() as connection:
                await connection.execute(select(1))
            return True
        except Exception:
            return False

    async def close(self) -> None:
        await self._engine.dispose()
