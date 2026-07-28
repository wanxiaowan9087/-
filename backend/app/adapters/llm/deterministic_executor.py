from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

from backend.app.application.ports import RunExecution


class DeterministicRunExecutor:
    """Offline executor used only for end-to-end test environments."""

    async def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        yield "status", {"phase": "accepted", "detail": None}
        yield "delta", {
            "index": 0,
            "content": f"Deterministic test response: {execution.input_content}",
        }
        yield (
            "done",
            {
                "outcome": "completed",
                "assistant_message_id": str(execution.assistant_message_id),
                "finish_reason": "stop",
                "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2},
            },
        )

    async def request_cancel(self, run_id: UUID) -> None:
        del run_id

    async def health(self) -> str:
        return "available"

    async def close(self) -> None:
        return None

    async def get_outcome(self, run_id: UUID) -> None:
        del run_id
        return None
