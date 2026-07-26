from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError

from backend.app.application.ports import RunExecution, RunExecutorPort
from backend.app.domain.records import StreamEventRecord
from backend.app.repositories.ports import PlatformRepository
from backend.app.schemas.events import SseEvent

event_adapter: TypeAdapter[SseEvent] = TypeAdapter(SseEvent)


def encode_sse(event: dict[str, Any]) -> bytes:
    data = json.dumps(event, ensure_ascii=False, separators=(",", ":"))
    return f"id: {event['sequence']}\nevent: {event['event_type']}\ndata: {data}\n\n".encode()


class RunCoordinator:
    """Keeps execution alive independently from any individual SSE connection."""

    def __init__(self, repository: PlatformRepository, executor: RunExecutorPort) -> None:
        self._repository = repository
        self._executor = executor
        self._tasks: dict[UUID, asyncio.Task[None]] = {}
        self._guard = asyncio.Lock()

    async def ensure_started(self, execution: RunExecution) -> None:
        async with self._guard:
            task = self._tasks.get(execution.run_id)
            if task is None or task.done():
                self._tasks[execution.run_id] = asyncio.create_task(
                    self._execute(execution), name=f"run-{execution.run_id}"
                )

    async def _execute(self, execution: RunExecution) -> None:
        content_parts: list[str] = []
        async with self._repository.transaction() as tx:
            await tx.update_run(
                execution.run_id,
                expected_statuses={"queued"},
                status="running",
                now=datetime.now(UTC),
            )
            sequence = len(await tx.list_events(execution.run_id, 0))
        try:
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

    async def cancel(self, run_id: UUID) -> None:
        await self._executor.request_cancel(run_id)

    async def close(self) -> None:
        tasks = list(self._tasks.values())
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        await self._executor.close()
