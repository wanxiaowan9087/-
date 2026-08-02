from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import NAMESPACE_URL, UUID, uuid5

from backend.app.application.cursor import CursorCodec
from backend.app.application.ports import HealthProbePort, RunExecution, RunExecutorPort
from backend.app.application.streaming import RunCoordinator, encode_sse
from backend.app.core.context import request_id_var
from backend.app.core.errors import AppError, conflict, not_found
from backend.app.core.security import Principal
from backend.app.domain.records import IdempotencyRecord, StreamEventRecord
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.models import DocumentRecord, DocumentType
from backend.app.repositories.ports import PlatformRepository, PlatformTransaction
from backend.app.schemas.common import Envelope, Page, PageInfo
from backend.app.schemas.resources import (
    CancelRunResult,
    CorrectMemoryRequest,
    CreateFeedbackRequest,
    CreateSessionRequest,
    DeactivateMemoryRequest,
    DeleteMemoryResult,
    Feedback,
    KnowledgeFile,
    Memory,
    Message,
    NewChatRequest,
    ReadyStatus,
    RetryChatRequest,
    ReviewDecisionRequest,
    ReviewDecisionResult,
    ReviewTask,
    RunTrace,
    Session,
)

DEFAULT_SESSION_TITLES = {"", "新会话", "New agent session", "Agent session", "Untitled session"}
SUPPORTED_KNOWLEDGE_SUFFIXES = {".txt", ".md", ".markdown"}
MAX_KNOWLEDGE_FILE_BYTES = 2 * 1024 * 1024


def summarize_session_title(content: str, *, max_length: int = 28) -> str:
    compact = re.sub(r"\s+", " ", content).strip(" \t\r\n。！？!?，,；;：:")
    if not compact:
        return "新会话"
    if len(compact) <= max_length:
        return compact
    return compact[: max_length - 1].rstrip() + "…"


def is_placeholder_session_title(title: str) -> bool:
    return title.strip() in DEFAULT_SESSION_TITLES


def safe_knowledge_filename(filename: str, payload: bytes) -> str:
    original = Path(filename).name
    suffix = Path(original).suffix.lower()
    if suffix not in SUPPORTED_KNOWLEDGE_SUFFIXES:
        raise AppError(
            "UNSUPPORTED_KNOWLEDGE_FILE",
            "only .txt and .md knowledge files are supported",
            400,
        )
    stem = Path(original).stem.strip() or "knowledge"
    safe_stem = re.sub(r"[^0-9A-Za-z_\-\u4e00-\u9fff]+", "-", stem).strip("-_") or "knowledge"
    digest = hashlib.sha256(payload).hexdigest()[:12]
    return f"{safe_stem[:48]}-{digest}{'.md' if suffix == '.markdown' else suffix}"


class PlatformService:
    def __init__(
        self,
        repository: PlatformRepository,
        executor: RunExecutorPort,
        *,
        cursor_secret: str,
        idempotency_ttl_seconds: int,
        stream_retention_seconds: int,
        redis_probe: HealthProbePort | None = None,
        vector_probe: HealthProbePort | None = None,
    ) -> None:
        self.repository = repository
        self.executor = executor
        self.cursor = CursorCodec(cursor_secret)
        self.idempotency_ttl = timedelta(seconds=idempotency_ttl_seconds)
        self.stream_retention = timedelta(seconds=stream_retention_seconds)
        self.redis_probe = redis_probe
        self.vector_probe = vector_probe
        self.coordinator = RunCoordinator(repository, executor)

    @staticmethod
    def _canonical(value: Any) -> str:
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    async def _claim(
        self,
        tx: PlatformTransaction,
        *,
        principal: Principal,
        operation_id: str,
        path: str,
        key: str,
        request: Any,
        now: datetime,
    ) -> tuple[IdempotencyRecord, dict[str, Any] | None]:
        key_digest = hashlib.sha256(key.encode()).hexdigest()
        request_digest = hashlib.sha256(self._canonical(request).encode()).hexdigest()
        decision, record = await tx.claim_idempotency(
            subject_id=principal.subject_id,
            operation_id=operation_id,
            normalized_path=path,
            key_digest=key_digest,
            request_digest=request_digest,
            now=now,
            expires_at=now + self.idempotency_ttl,
        )
        if record is None:
            raise RuntimeError("repository returned no idempotency record")
        if decision == "reused":
            raise AppError(
                "IDEMPOTENCY_KEY_REUSED", "idempotency key was used for another request", 409
            )
        if decision == "in_progress":
            raise AppError(
                "IDEMPOTENCY_IN_PROGRESS", "the original request is still in progress", 409
            )
        return record, record.response_body if decision == "replay" else None

    @staticmethod
    def _envelope(data: Any) -> Envelope[Any]:
        return Envelope(data=data, request_id=request_id_var.get())

    async def readiness(self) -> Envelope[ReadyStatus]:
        postgres = "available" if await self.repository.ping() else "unavailable"
        redis = await self.redis_probe.health() if self.redis_probe else "not_checked"
        vector_store = await self.vector_probe.health() if self.vector_probe else "not_checked"
        model = await self.executor.health()
        if postgres != "available":
            raise AppError(
                "STORAGE_UNAVAILABLE",
                "required storage is unavailable",
                503,
                {"retryable": True},
            )
        return Envelope(
            data=ReadyStatus(
                status="ready",
                dependencies={
                    "postgresql": postgres,
                    "redis": redis,
                    "vector_store": vector_store,
                    "model": model,
                },
            ),
            request_id=request_id_var.get(),
        )

    async def create_session(
        self, principal: Principal, key: str, request: CreateSessionRequest
    ) -> tuple[int, Envelope[Session], bool]:
        now = datetime.now(UTC)
        canonical = request.model_dump(mode="json")
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="createSession",
                path="/sessions",
                key=key,
                request=canonical,
                now=now,
            )
            if replay is not None:
                return cast(int, idem.status_code), Envelope[Session].model_validate(replay), True
            session = await tx.create_session(principal.subject_id, request.title or "新会话", now)
            body: Envelope[Session] = Envelope(
                data=Session.model_validate(session), request_id=request_id_var.get()
            )
            await tx.finish_idempotency(
                idem, status_code=201, response_body=body.model_dump(mode="json")
            )
            return 201, body, False

    async def list_sessions(
        self, principal: Principal, cursor: str | None, limit: int
    ) -> Envelope[Page[Session]]:
        marker = self.cursor.decode(cursor, kind="sessions", filters={})
        async with self.repository.transaction() as tx:
            items = await tx.list_sessions(principal.subject_id, limit=limit + 1, after=marker)
        has_more = len(items) > limit
        visible = items[:limit]
        next_cursor = (
            self.cursor.encode("sessions", {}, visible[-1].last_message_at, visible[-1].id)
            if has_more and visible
            else None
        )
        return Envelope(
            data=Page(
                items=[Session.model_validate(item) for item in visible],
                page=PageInfo(next_cursor=next_cursor, has_more=has_more),
            ),
            request_id=request_id_var.get(),
        )

    async def get_session(self, principal: Principal, session_id: UUID) -> Envelope[Session]:
        async with self.repository.transaction() as tx:
            item = await tx.get_session(principal.subject_id, session_id)
        if item is None:
            raise not_found()
        return Envelope(data=Session.model_validate(item), request_id=request_id_var.get())

    async def list_messages(
        self, principal: Principal, session_id: UUID, cursor: str | None, limit: int
    ) -> Envelope[Page[Message]]:
        marker = self.cursor.decode(
            cursor, kind="messages", filters={"session_id": str(session_id)}
        )
        typed_marker = cast(tuple[datetime, UUID] | None, marker)
        async with self.repository.transaction() as tx:
            items = await tx.list_messages(
                principal.subject_id, session_id, limit=limit + 1, after=typed_marker
            )
        has_more = len(items) > limit
        visible = items[:limit]
        next_cursor = (
            self.cursor.encode(
                "messages",
                {"session_id": str(session_id)},
                cast(datetime, visible[-1].created_at),
                visible[-1].id,
            )
            if has_more and visible
            else None
        )
        return Envelope(
            data=Page(
                items=[Message.model_validate(item) for item in visible],
                page=PageInfo(next_cursor=next_cursor, has_more=has_more),
            ),
            request_id=request_id_var.get(),
        )

    async def prepare_stream(
        self,
        principal: Principal,
        key: str,
        request: NewChatRequest | RetryChatRequest,
        last_event_id: int,
    ) -> tuple[UUID, UUID, bool, AsyncIterator[bytes]]:
        now = datetime.now(UTC)
        canonical = request.model_dump(mode="json")
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="streamChat",
                path="/chat/stream",
                key=key,
                request=canonical,
                now=now,
            )
            replayed = replay is not None
            if replayed:
                if idem.run_id is None:
                    raise AppError("INTERNAL_ERROR", "stream replay record is invalid", 500)
                run = await tx.get_run(principal.subject_id, idem.run_id)
                if run is None:
                    raise not_found()
                user_message = await tx.get_message(principal.subject_id, run.user_message_id)
                if user_message is None:
                    raise not_found()
                retained_events = await tx.list_events(run.id, 0)
                latest_sequence = retained_events[-1].sequence if retained_events else 0
                if last_event_id > latest_sequence:
                    raise AppError(
                        "BAD_REQUEST",
                        "Last-Event-ID is ahead of the run event stream",
                        400,
                    )
                replay_expired = bool(
                    run.ended_at
                    and now - run.ended_at > self.stream_retention
                    and last_event_id < latest_sequence
                )
                replay_gap = bool(
                    last_event_id > 0
                    and (
                        not retained_events
                        or retained_events[0].sequence > last_event_id + 1
                    )
                )
                if replay_expired or replay_gap:
                    raise AppError(
                        "STREAM_REPLAY_EXPIRED",
                        "the requested stream replay window has expired",
                        410,
                        {"retryable": False},
                    )
            else:
                user_message, assistant, run = await tx.prepare_chat(
                    owner_id=principal.subject_id,
                    session_id=request.session_id,
                    content=request.content if isinstance(request, NewChatRequest) else None,
                    original_user_message_id=(
                        request.original_user_message_id
                        if isinstance(request, RetryChatRequest)
                        else None
                    ),
                    session_title=(
                        summarize_session_title(request.content)
                        if isinstance(request, NewChatRequest)
                        else None
                    ),
                    now=now,
                )
                meta = {
                    "event_type": "meta",
                    "sequence": 1,
                    "request_id": request_id_var.get(),
                    "session_id": str(run.session_id),
                    "run_id": str(run.id),
                    "timestamp": now.isoformat(),
                    "payload": {
                        "user_message_id": str(user_message.id),
                        "assistant_message_id": str(assistant.id),
                        "attempt": run.attempt,
                        "retry_of_user_message_id": (
                            str(run.retry_of_user_message_id)
                            if run.retry_of_user_message_id
                            else None
                        ),
                        "replayed": False,
                    },
                }
                await tx.add_event(StreamEventRecord(run.id, 1, meta, now))
                await tx.finish_idempotency(
                    idem,
                    status_code=200,
                    response_body={
                        "run_id": str(run.id),
                        "session_id": str(run.session_id),
                        "user_message_id": str(user_message.id),
                    },
                    run_id=run.id,
                )
        execution = RunExecution(
            request_id=request_id_var.get(),
            subject_id=principal.subject_id,
            session_id=run.session_id,
            run_id=run.id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            input_content=user_message.content,
            attempt=run.attempt,
            retry_of_user_message_id=run.retry_of_user_message_id,
        )
        await self.coordinator.ensure_started(execution)

        async def follow() -> AsyncIterator[bytes]:
            next_sequence = last_event_id
            terminal_waits = 0
            while True:
                async with self.repository.transaction() as tx:
                    events = await tx.list_events(run.id, next_sequence)
                    current = await tx.get_run(principal.subject_id, run.id)
                saw_terminal = False
                for stored in events:
                    event = dict(stored.event)
                    if replayed:
                        if event["event_type"] == "meta":
                            event["payload"] = {**event["payload"], "replayed": True}
                    next_sequence = stored.sequence
                    yield encode_sse(event)
                    if event["event_type"] in {"done", "error"}:
                        saw_terminal = True
                        return
                if current is None or current.status in {
                    "completed",
                    "cancelled",
                    "failed",
                    "rejected",
                }:
                    if saw_terminal:
                        return
                    terminal_waits += 1
                    if terminal_waits >= 100:
                        return
                else:
                    terminal_waits = 0
                await asyncio.sleep(0.01)

        return run.session_id, run.id, replayed, follow()

    async def create_feedback(
        self,
        principal: Principal,
        message_id: UUID,
        key: str,
        request: CreateFeedbackRequest,
    ) -> tuple[int, Envelope[Feedback], bool]:
        now = datetime.now(UTC)
        canonical = request.model_dump(mode="json")
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="createMessageFeedback",
                path=f"/chat/{message_id}/feedback",
                key=key,
                request=canonical,
                now=now,
            )
            if replay is not None:
                return cast(int, idem.status_code), Envelope[Feedback].model_validate(replay), True
            record = await tx.create_feedback(
                principal.subject_id,
                message_id,
                request.rating,
                request.reason,
                request.comment,
                now,
            )
            body: Envelope[Feedback] = Envelope(
                data=Feedback.model_validate(record), request_id=request_id_var.get()
            )
            await tx.finish_idempotency(
                idem, status_code=201, response_body=body.model_dump(mode="json")
            )
            return 201, body, False

    async def cancel_run(
        self, principal: Principal, run_id: UUID, key: str, reason: str | None
    ) -> tuple[int, Envelope[CancelRunResult], bool]:
        now = datetime.now(UTC)
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="cancelRun",
                path=f"/runs/{run_id}/cancel",
                key=key,
                request={"reason": reason},
                now=now,
            )
            if replay is not None:
                return (
                    cast(int, idem.status_code),
                    Envelope[CancelRunResult].model_validate(replay),
                    True,
                )
            run = await tx.get_run(principal.subject_id, run_id)
            if run is None:
                raise not_found()
            terminal = run.status in {"completed", "cancelled", "failed", "rejected"}
            status_code = 200 if terminal else 202
            status = "already_terminal" if terminal else "cancellation_requested"
            if not terminal:
                events = await tx.list_events(run_id, 0)
                sequence = events[-1].sequence if events else 0
                await tx.update_run(
                    run_id,
                    expected_statuses={"queued", "running"},
                    status="cancelled",
                    now=now,
                )
                for event_type, payload in [
                    ("status", {"phase": "cancelling", "detail": None}),
                    (
                        "done",
                        {
                            "outcome": "cancelled",
                            "assistant_message_id": str(run.assistant_message_id),
                            "finish_reason": "user_cancelled",
                            "usage": None,
                        },
                    ),
                ]:
                    sequence += 1
                    event = {
                        "event_type": event_type,
                        "sequence": sequence,
                        "request_id": request_id_var.get(),
                        "session_id": str(run.session_id),
                        "run_id": str(run.id),
                        "timestamp": now.isoformat(),
                        "payload": payload,
                    }
                    await tx.add_event(StreamEventRecord(run.id, sequence, event, now))
            result = CancelRunResult(run_id=run_id, status=status, requested_at=now)
            body: Envelope[CancelRunResult] = Envelope(data=result, request_id=request_id_var.get())
            await tx.finish_idempotency(
                idem, status_code=status_code, response_body=body.model_dump(mode="json")
            )
        if not terminal:
            await self.coordinator.cancel(run_id)
        return status_code, body, False

    async def get_trace(self, principal: Principal, run_id: UUID) -> Envelope[RunTrace]:
        async with self.repository.transaction() as tx:
            run = await tx.get_run(principal.subject_id, run_id)
            if run is None and principal.role == "reviewer":
                reviews = await tx.list_reviews(status=None, limit=101, after=None)
                linked = next((item for item in reviews if item.run_id == run_id), None)
                if linked:
                    run = await tx.get_run(linked.owner_id, run_id)
        if run is None:
            raise not_found()
        trace = RunTrace(
            run_id=run.id,
            session_id=run.session_id,
            user_message_id=run.user_message_id,
            assistant_message_id=run.assistant_message_id,
            status=run.status,
            model=run.model,
            retrieval_strategy=run.retrieval_strategy,
            confidence_threshold=run.confidence_threshold,
            steps=run.steps,
            citations=run.citations,
            started_at=cast(datetime, run.started_at),
            ended_at=run.ended_at,
        )
        return Envelope(data=trace, request_id=request_id_var.get())

    async def list_memories(
        self,
        principal: Principal,
        cursor: str | None,
        limit: int,
        status: str | None,
        memory_type: str | None,
    ) -> Envelope[Page[Memory]]:
        filters = {"status": status, "memory_type": memory_type}
        marker = cast(
            tuple[datetime, UUID] | None,
            self.cursor.decode(cursor, kind="memories", filters=filters),
        )
        async with self.repository.transaction() as tx:
            items = await tx.list_memories(
                principal.subject_id,
                status=status,
                memory_type=memory_type,
                limit=limit + 1,
                after=marker,
            )
        has_more = len(items) > limit
        visible = items[:limit]
        next_cursor = (
            self.cursor.encode("memories", filters, visible[-1].updated_at, visible[-1].id)
            if has_more and visible
            else None
        )
        return Envelope(
            data=Page(
                items=[Memory.model_validate(item) for item in visible],
                page=PageInfo(next_cursor=next_cursor, has_more=has_more),
            ),
            request_id=request_id_var.get(),
        )

    async def upload_knowledge_file(
        self,
        principal: Principal,
        *,
        filename: str,
        content_type: str,
        payload: bytes,
        uploads_dir: str,
    ) -> Envelope[KnowledgeFile]:
        del principal
        if not payload:
            raise AppError("EMPTY_KNOWLEDGE_FILE", "knowledge file must not be empty", 400)
        if len(payload) > MAX_KNOWLEDGE_FILE_BYTES:
            raise AppError(
                "KNOWLEDGE_FILE_TOO_LARGE",
                "knowledge file must be 2 MB or smaller",
                400,
            )
        if content_type and not (
            content_type.startswith("text/")
            or content_type in {"application/octet-stream", "application/x-markdown"}
        ):
            raise AppError(
                "UNSUPPORTED_KNOWLEDGE_FILE",
                "knowledge upload expects a UTF-8 text or markdown file",
                400,
            )
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError as error:
            raise AppError(
                "INVALID_KNOWLEDGE_FILE",
                "knowledge file must be encoded as UTF-8 text",
                400,
            ) from error
        if not text.strip():
            raise AppError("EMPTY_KNOWLEDGE_FILE", "knowledge file must contain text", 400)

        safe_name = safe_knowledge_filename(filename, payload)
        target_dir = Path(uploads_dir) / "knowledge"
        target_dir.mkdir(parents=True, exist_ok=True)
        target = target_dir / safe_name
        target.write_text(text, encoding="utf-8", newline="\n")

        title = summarize_session_title(Path(filename).stem, max_length=120)
        document = DocumentRecord(
            document_id=str(uuid5(NAMESPACE_URL, f"knowledge-upload:{safe_name}")),
            title=title,
            source=f"file://uploads/knowledge/{safe_name}",
            document_type=(
                DocumentType.MARKDOWN if target.suffix.lower() == ".md" else DocumentType.TEXT
            ),
            content=text,
        )
        chunks = DocumentChunker().split(document)
        return Envelope(
            data=KnowledgeFile(
                id=document.document_id,
                filename=safe_name,
                title=title,
                source=document.source,
                size_bytes=len(payload),
                chunk_count=len(chunks),
                uploaded_at=datetime.now(UTC),
            ),
            request_id=request_id_var.get(),
        )

    async def update_memory(
        self,
        principal: Principal,
        memory_id: UUID,
        key: str,
        request: CorrectMemoryRequest | DeactivateMemoryRequest,
    ) -> tuple[int, Envelope[Memory], bool]:
        now = datetime.now(UTC)
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="updateMemory",
                path=f"/memories/{memory_id}",
                key=key,
                request=request.model_dump(mode="json"),
                now=now,
            )
            if replay is not None:
                return cast(int, idem.status_code), Envelope[Memory].model_validate(replay), True
            item = await tx.update_memory(
                principal.subject_id,
                memory_id,
                expected_version=request.expected_version,
                content=request.content if isinstance(request, CorrectMemoryRequest) else None,
                deactivate=isinstance(request, DeactivateMemoryRequest),
                now=now,
            )
            if item is None:
                raise not_found()
            body: Envelope[Memory] = Envelope(
                data=Memory.model_validate(item), request_id=request_id_var.get()
            )
            await tx.finish_idempotency(
                idem, status_code=200, response_body=body.model_dump(mode="json")
            )
            return 200, body, False

    async def delete_memory(
        self, principal: Principal, memory_id: UUID, key: str, expected_version: int
    ) -> tuple[int, Envelope[DeleteMemoryResult], bool]:
        now = datetime.now(UTC)
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="deleteMemory",
                path=f"/memories/{memory_id}",
                key=key,
                request={"if_match": expected_version},
                now=now,
            )
            if replay is not None:
                return (
                    cast(int, idem.status_code),
                    Envelope[DeleteMemoryResult].model_validate(replay),
                    True,
                )
            item = await tx.delete_memory(
                principal.subject_id,
                memory_id,
                expected_version=expected_version,
                now=now,
            )
            if item is None:
                raise not_found()
            result = DeleteMemoryResult(
                memory_id=memory_id,
                deleted_at=now,
                backup_expiry_note="backup copies expire according to the backup retention policy",
            )
            body: Envelope[DeleteMemoryResult] = Envelope(
                data=result, request_id=request_id_var.get()
            )
            await tx.finish_idempotency(
                idem, status_code=200, response_body=body.model_dump(mode="json")
            )
            return 200, body, False

    async def list_reviews(
        self, cursor: str | None, limit: int, status: str | None
    ) -> Envelope[Page[ReviewTask]]:
        filters = {"status": status}
        marker = cast(
            tuple[datetime, UUID] | None,
            self.cursor.decode(cursor, kind="reviews", filters=filters),
        )
        async with self.repository.transaction() as tx:
            items = await tx.list_reviews(status=status, limit=limit + 1, after=marker)
        has_more = len(items) > limit
        visible = items[:limit]
        next_cursor = (
            self.cursor.encode("reviews", filters, visible[-1].created_at, visible[-1].id)
            if has_more and visible
            else None
        )
        return Envelope(
            data=Page(
                items=[ReviewTask.model_validate(item) for item in visible],
                page=PageInfo(next_cursor=next_cursor, has_more=has_more),
            ),
            request_id=request_id_var.get(),
        )

    async def decide_review(
        self,
        principal: Principal,
        review_id: UUID,
        key: str,
        request: ReviewDecisionRequest,
    ) -> tuple[int, Envelope[ReviewDecisionResult], bool]:
        now = datetime.now(UTC)
        async with self.repository.transaction() as tx:
            idem, replay = await self._claim(
                tx,
                principal=principal,
                operation_id="decideReview",
                path=f"/reviews/{review_id}/decision",
                key=key,
                request=request.model_dump(mode="json"),
                now=now,
            )
            if replay is not None:
                return (
                    cast(int, idem.status_code),
                    Envelope[ReviewDecisionResult].model_validate(replay),
                    True,
                )
            item, published, success = await tx.decide_review(
                review_id,
                reviewer_id=principal.subject_id,
                request_id=request_id_var.get(),
                expected_version=request.expected_version,
                decision=request.decision,
                edited_content=getattr(request, "edited_content", None),
                now=now,
            )
            if item is None:
                raise not_found()
            if not success:
                raise conflict("review version or state conflict")
            result = ReviewDecisionResult(
                review_id=item.id,
                status=item.status,
                published_message_id=published,
                decided_at=cast(datetime, item.decided_at),
                version=item.version,
            )
            body: Envelope[ReviewDecisionResult] = Envelope(
                data=result, request_id=request_id_var.get()
            )
            await tx.finish_idempotency(
                idem, status_code=200, response_body=body.model_dump(mode="json")
            )
            return 200, body, False

    async def close(self) -> None:
        await self.coordinator.close()
        await self.repository.close()
