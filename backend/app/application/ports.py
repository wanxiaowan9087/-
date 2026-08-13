from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any, Protocol
from uuid import UUID


@dataclass(frozen=True, slots=True)
class RunExecution:
    request_id: str
    subject_id: str
    session_id: UUID
    run_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID
    input_content: str
    attempt: int = 1
    retry_of_user_message_id: UUID | None = None


class RunExecutorPort(Protocol):
    """Seam owned by AI/RAG; platform only consumes typed event drafts."""

    def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, Any]]]: ...

    async def request_cancel(self, run_id: UUID) -> None: ...

    async def health(self) -> str: ...

    async def close(self) -> None: ...


class RunOutcomePort(Protocol):
    """Optional rich outcome seam for persistence after terminal streaming."""

    async def get_outcome(self, run_id: UUID) -> Any | None: ...


class HealthProbePort(Protocol):
    """Small seam for optional infrastructure readiness checks."""

    async def health(self) -> str: ...


class UnavailableRunExecutor:
    """Honest placeholder until the AI/RAG adapter is integrated."""

    async def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        del execution
        yield (
            "error",
            {
                "code": "MODEL_UNAVAILABLE",
                "message": "run executor is not configured",
                "retryable": True,
                "retry_after_seconds": None,
            },
        )

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id

    async def health(self) -> str:
        return "not_checked"

    async def close(self) -> None:
        return None

    async def get_outcome(self, run_id: UUID) -> Any | None:
        del run_id
        return None
