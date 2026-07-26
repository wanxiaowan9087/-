from __future__ import annotations

from typing import Protocol, Sequence

from .contracts import (
    AgentModelRequest,
    ConversationMessage,
    LongTermFact,
    ModelDraft,
)
from .tooling import CancellationToken


class ModelTimeout(TimeoutError):
    pass


class ModelUnavailable(RuntimeError):
    pass


class ReActEnginePort(Protocol):
    """Seam for LangChain ReAct and deterministic fake adapters."""

    async def generate(
        self,
        request: AgentModelRequest,
        cancellation: CancellationToken | None = None,
    ) -> ModelDraft: ...


class ConversationHistoryPort(Protocol):
    async def list_messages(
        self, session_id: str
    ) -> Sequence[ConversationMessage]: ...


class ConversationSummaryPort(Protocol):
    async def summarize(
        self,
        messages: Sequence[ConversationMessage],
        previous_summary: str | None,
    ) -> str: ...


class LongTermMemoryPort(Protocol):
    async def list_active(self, subject_id: str) -> Sequence[LongTermFact]: ...

    async def save_extracted(
        self, subject_id: str, facts: Sequence[LongTermFact]
    ) -> None: ...

    async def correct(
        self,
        subject_id: str,
        memory_id: str,
        expected_version: int,
        content: str,
    ) -> LongTermFact: ...

    async def deactivate(
        self,
        subject_id: str,
        memory_id: str,
        expected_version: int,
    ) -> LongTermFact: ...

    async def delete(
        self, subject_id: str, memory_id: str, expected_version: int
    ) -> None: ...


class MemoryExtractionPort(Protocol):
    async def extract(
        self, message: ConversationMessage
    ) -> Sequence[LongTermFact]: ...
