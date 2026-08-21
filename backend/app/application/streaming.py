from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import asdict, is_dataclass
from datetime import UTC, datetime
from enum import Enum
from typing import Any, cast
from uuid import UUID, uuid4

from pydantic import TypeAdapter, ValidationError

from backend.app.application.ports import RunExecution, RunExecutorPort
from backend.app.application.security_rate_limit import rate_limited
from backend.app.core.context import bind_context
from backend.app.domain.records import StreamEventRecord
from backend.app.repositories.ports import PlatformRepository
from backend.app.schemas.events import SseEvent

event_adapter: TypeAdapter[SseEvent] = TypeAdapter(SseEvent)
logger = logging.getLogger(__name__)


def encode_sse(event: dict[str, Any]) -> bytes:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['sequence']}\nevent: {event['event_type']}\ndata: {data}\n\n".encode()


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


class RunCoordinator:
    """Keeps execution alive independently from any individual SSE connection."""

    def __init__(
        self,
        repository: PlatformRepository,
        executor: RunExecutorPort,
        *,
        max_concurrent_runs: int = 4,
        max_concurrent_runs_per_user: int = 1,
    ) -> None:
        self._repository = repository
        self._executor = executor
        self._max_concurrent_runs = max_concurrent_runs
        self._max_concurrent_runs_per_user = max_concurrent_runs_per_user
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._owners: dict[UUID, str] = {}
        self._reservations: dict[UUID, str] = {}
        self._guard = asyncio.Lock()

    def _prune_finished(self) -> None:
        for run_id, task in list(self._tasks.items()):
            if task.done():
                self._tasks.pop(run_id, None)
                self._owners.pop(run_id, None)

    async def reserve(self, subject_id: str) -> UUID:
        """Reserve bounded local capacity before a new chat Run is persisted."""
        async with self._guard:
            self._prune_finished()
            active_subject_runs = sum(owner == subject_id for owner in self._owners.values())
            reserved_subject_runs = sum(
                owner == subject_id for owner in self._reservations.values()
            )
            if len(self._tasks) + len(self._reservations) >= self._max_concurrent_runs:
                raise rate_limited(10)
            if active_subject_runs + reserved_subject_runs >= self._max_concurrent_runs_per_user:
                raise rate_limited(10)
            reservation_id = uuid4()
            self._reservations[reservation_id] = subject_id
            return reservation_id

    async def release_reservation(self, reservation_id: UUID) -> None:
        async with self._guard:
            self._reservations.pop(reservation_id, None)

    async def ensure_started(
        self, execution: RunExecution, reservation_id: UUID | None = None
    ) -> None:
        async with self._guard:
            if reservation_id is not None:
                owner = self._reservations.pop(reservation_id, None)
                if owner != execution.subject_id:
                    raise RuntimeError("chat capacity reservation is invalid")
            task = self._tasks.get(execution.run_id)
            if task is None or task.done():
                self._tasks[execution.run_id] = asyncio.create_task(
                    self._execute(execution), name=f"run-{execution.run_id}"
                )
                self._owners[execution.run_id] = execution.subject_id

    async def _execute(self, execution: RunExecution) -> None:
        tokens = bind_context(
            execution.request_id, str(execution.session_id), str(execution.run_id)
        )
        content_parts: list[str] = []
        product_recommendations: list[dict[str, object]] = []
        try:
            async with self._repository.transaction() as tx:
                await tx.update_run(
                    execution.run_id,
                    expected_statuses={"queued"},
                    status="running",
                    now=datetime.now(UTC),
                )
                sequence = len(await tx.list_events(execution.run_id, 0))
            async for event_type, payload in self._executor.stream(execution):
                now = datetime.now(UTC)
                async with self._repository.transaction() as tx:
                    run = await tx.get_run(execution.subject_id, execution.run_id)
                    if run is None or run.status in {
                        "cancelled",
                        "completed",
                        "failed",
                        "rejected",
                    }:
                        return
                    sequence += 1
                    event = {
                        "event_type": event_type,
                        "sequence": sequence,
                        "request_id": execution.request_id,
                        "session_id": str(execution.session_id),
                        "run_id": str(execution.run_id),
                        "timestamp": now.isoformat(),
                        "payload": payload,
                    }
                    event_adapter.validate_python(event)
                    await tx.add_event(StreamEventRecord(execution.run_id, sequence, event, now))
                    if event_type == "delta":
                        content_parts.append(str(payload["content"]))
                    elif event_type == "product_recommendation":
                        product_recommendations.append(dict(payload))
                    elif event_type == "done":
                        outcome = str(payload["outcome"])
                        await tx.update_run(
                            execution.run_id,
                            expected_statuses={"running"},
                            status=outcome,
                            now=now,
                            assistant_content="".join(content_parts)
                            if outcome == "completed"
                            else None,
                        )
                        get_outcome = getattr(self._executor, "get_outcome", None)
                        rich_outcome = (
                            await get_outcome(execution.run_id) if callable(get_outcome) else None
                        )
                        if rich_outcome is not None:
                            await tx.update_run(
                                execution.run_id,
                                expected_statuses={outcome},
                                status=outcome,
                                now=now,
                                model=rich_outcome.model_name,
                                retrieval_strategy=rich_outcome.retrieval_strategy,
                                confidence_threshold=rich_outcome.confidence_threshold,
                                steps=[_jsonable(step) for step in rich_outcome.trace],
                                citations=[
                                    _jsonable(citation) for citation in rich_outcome.citations
                                ],
                                product_recommendations=product_recommendations,
                            )
                            if (
                                outcome == "needs_review"
                                and rich_outcome.candidate_content
                                and rich_outcome.review_id
                            ):
                                await tx.create_review(
                                    review_id=UUID(rich_outcome.review_id),
                                    run_id=execution.run_id,
                                    session_id=execution.session_id,
                                    owner_id=execution.subject_id,
                                    user_message_id=execution.user_message_id,
                                    candidate_content=rich_outcome.candidate_content,
                                    reason_codes=[
                                        reason.value for reason in rich_outcome.review_reasons
                                    ],
                                    confidence=rich_outcome.confidence,
                                    now=now,
                                )
                        if outcome == "completed":
                            await tx.record_product_recommendations(
                                owner_id=execution.subject_id,
                                session_id=execution.session_id,
                                message_id=execution.assistant_message_id,
                                recommendations=product_recommendations,
                                now=now,
                            )
                            await tx.enqueue_summary_update(
                                owner_id=execution.subject_id,
                                session_id=execution.session_id,
                                trigger_message_id=execution.assistant_message_id,
                                now=now,
                            )
                    elif event_type == "error":
                        await tx.update_run(
                            execution.run_id,
                            expected_statuses={"queued", "running"},
                            status="failed",
                            now=now,
                        )
                    if event_type in {"done", "error"}:
                        return
            raise RuntimeError("run executor ended without a terminal event")
        except (Exception, ValidationError):
            logger.exception(
                "agent run execution failed",
                extra={
                    "error_code": "INTERNAL_ERROR",
                    "event_sequence": sequence if "sequence" in locals() else 0,
                },
            )
            now = datetime.now(UTC)
            async with self._repository.transaction() as tx:
                run = await tx.get_run(execution.subject_id, execution.run_id)
                if run is None or run.status in {"cancelled", "completed", "failed", "rejected"}:
                    return
                sequence += 1
                event = {
                    "event_type": "error",
                    "sequence": sequence,
                    "request_id": execution.request_id,
                    "session_id": str(execution.session_id),
                    "run_id": str(execution.run_id),
                    "timestamp": now.isoformat(),
                    "payload": {
                        "code": "INTERNAL_ERROR",
                        "message": "run execution failed",
                        "retryable": False,
                        "retry_after_seconds": None,
                    },
                }
                await tx.add_event(StreamEventRecord(execution.run_id, sequence, event, now))
                await tx.update_run(
                    execution.run_id,
                    expected_statuses={"queued", "running"},
                    status="failed",
                    now=now,
                )
        finally:
            tokens.reset()

    async def cancel(self, run_id: UUID) -> None:
        await self._executor.request_cancel(run_id)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._tasks.clear()
        self._owners.clear()
        self._reservations.clear()
        await self._executor.close()
