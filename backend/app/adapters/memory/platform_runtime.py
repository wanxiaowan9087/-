from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Protocol
from uuid import UUID

from backend.app.agent.contracts import (
    ConversationMessage,
    LongTermFact,
    MemoryContext,
    MemoryType,
)
from backend.app.domain.records import MessageRecord
from backend.app.repositories.ports import PlatformRepository


class _HasMessageFields(Protocol):
    role: str
    content: str


class PlatformMemoryRuntime:
    """Builds persistent summary, short-term window, and sourced user memories."""

    def __init__(
        self,
        repository: PlatformRepository,
        *,
        window_messages: int = 8,
        summary_batch_messages: int = 200,
        summary_max_chars: int = 2400,
        max_fact_chars: int = 280,
        max_total_fact_chars: int = 2400,
    ) -> None:
        if window_messages < 1:
            raise ValueError("window_messages must be positive")
        if summary_batch_messages < 1:
            raise ValueError("summary_batch_messages must be positive")
        if summary_max_chars < 200:
            raise ValueError("summary_max_chars must be at least 200")
        self._repository = repository
        self._window_messages = window_messages
        self._summary_batch_messages = summary_batch_messages
        self._summary_max_chars = summary_max_chars
        self._max_fact_chars = max_fact_chars
        self._max_total_fact_chars = max_total_fact_chars

    async def build_context(self, session_id: str, subject_id: str) -> MemoryContext:
        session_uuid = UUID(session_id)
        async with self._repository.transaction() as tx:
            session = await tx.get_session(subject_id, session_uuid)
            if session is None:
                return MemoryContext()
            recent_messages = await tx.list_recent_messages(
                subject_id, session_uuid, limit=self._window_messages + 1
            )
            visible_recent = [message for message in recent_messages if message.content]
            window_records = visible_recent[-self._window_messages :]
            summary = session.memory_summary
            if window_records:
                first_window = window_records[0]
                after = (
                    (session.summary_through_created_at, session.summary_through_message_id)
                    if session.summary_through_created_at is not None
                    and session.summary_through_message_id is not None
                    else None
                )
                pending = await tx.list_messages(
                    subject_id,
                    session_uuid,
                    limit=self._summary_batch_messages + self._window_messages,
                    after=after,
                )
                eligible = _messages_before(pending, first_window)[: self._summary_batch_messages]
                if eligible:
                    summary = _append_summary(
                        summary, eligible, max_chars=self._summary_max_chars
                    )
                    last = eligible[-1]
                    assert last.created_at is not None
                    await tx.update_session_memory_summary(
                        subject_id,
                        session_uuid,
                        summary=summary,
                        through_created_at=last.created_at,
                        through_message_id=last.id,
                        now=datetime.now(UTC),
                    )
            memories = await tx.list_memories(
                subject_id,
                status="active",
                memory_type=None,
                limit=12,
                after=None,
            )

        window = tuple(
            ConversationMessage(
                message_id=str(message.id),
                role=message.role,
                content=message.content,
                created_at=message.created_at or datetime.now(UTC),
            )
            for message in window_records
        )
        raw_facts = tuple(
            LongTermFact(
                memory_id=str(memory.id),
                memory_type=MemoryType(memory.memory_type),
                content=memory.content,
                confidence=memory.confidence,
                source_message_id=str(memory.source_message_id),
                version=memory.version,
                active=memory.status == "active",
            )
            for memory in memories
            if memory.status == "active"
        )
        # Present durable facts in a stable, useful order: concrete device/user
        # facts first, then communication preferences, while preserving the
        # repository's recency ordering within each category.
        facts = tuple(
            sorted(
                raw_facts,
                key=lambda item: (item.memory_type is not MemoryType.USER_FACT,),
            )
        )
        bounded_facts: list[LongTermFact] = []
        fact_chars = 0
        for fact in facts:
            content = " ".join(fact.content.split())[: self._max_fact_chars]
            if fact_chars + len(content) > self._max_total_fact_chars:
                break
            bounded_facts.append(
                LongTermFact(
                    memory_id=fact.memory_id,
                    memory_type=fact.memory_type,
                    content=content,
                    confidence=fact.confidence,
                    source_message_id=fact.source_message_id,
                    version=fact.version,
                    active=fact.active,
                )
            )
            fact_chars += len(content)
        return MemoryContext(
            window=window,
            summary=(summary or "")[: self._summary_max_chars] or None,
            facts=tuple(bounded_facts),
        )

    async def get_context(self, subject_id: str, session_id: str) -> MemoryContext:
        """User-context tool adapter; keeps repository access behind this port."""
        return await self.build_context(session_id, subject_id)

    async def extract_best_effort(
        self, subject_id: str, source: ConversationMessage
    ) -> str | None:
        extracted = _explicit_memory(source.content)
        if extracted is None:
            return None
        memory_type, candidate = extracted
        async with self._repository.transaction() as tx:
            existing = await tx.list_memories(
                subject_id,
                status="active",
                memory_type=memory_type.value,
                limit=100,
                after=None,
            )
            canonical = _canonical_memory(candidate)
            if any(_canonical_memory(memory.content) == canonical for memory in existing):
                return None
            await tx.create_memory(
                subject_id,
                memory_type=memory_type.value,
                content=candidate,
                confidence=0.9,
                source_message_id=UUID(source.message_id),
                now=datetime.now(UTC),
            )
        return None


def _messages_before(
    messages: list[MessageRecord], boundary: MessageRecord
) -> list[MessageRecord]:
    if boundary.created_at is None:
        return []
    return [
        message
        for message in messages
        if message.content
        and message.created_at is not None
        and (message.created_at, message.id.int)
        < (boundary.created_at, boundary.id.int)
    ]


def _explicit_memory(content: str) -> tuple[MemoryType, str] | None:
    normalized = " ".join(content.split())
    if not normalized or len(normalized) > 500:
        return None
    lowered = normalized.casefold()
    sensitive_markers = (
        "\u5bc6\u7801",
        "\u8eab\u4efd\u8bc1",
        "\u94f6\u884c\u5361",
        "\u4f4f\u5740",
        "\u7535\u8bdd",
        "\u90ae\u7bb1",
        "password",
        "bank card",
        "address",
        "phone",
        "email",
    )
    if any(marker in lowered for marker in sensitive_markers):
        return None
    preference_markers = (
        "\u6211\u504f\u597d", "\u6211\u559c\u6b22", "\u8bf7\u7528", "\u8bf7\u4e0d\u8981",
        "i prefer", "please use", "please do not",
    )
    if lowered.startswith(preference_markers):
        return MemoryType.PREFERENCE, normalized
    fact_markers = (
        "\u6211\u7684\u578b\u53f7\u662f",
        "\u6211\u4f7f\u7528",
        "\u6211\u7684\u8bbe\u5907\u662f",
        "\u6211\u7684\u95ee\u9898\u662f",
        "my model is",
        "i use",
        "my device is",
    )
    if lowered.startswith(fact_markers):
        return MemoryType.USER_FACT, normalized
    return None


def _canonical_memory(content: str) -> str:
    return " ".join(content.casefold().split())


def _append_summary(
    previous: str | None,
    messages: Sequence[_HasMessageFields],
    *,
    max_chars: int,
) -> str:
    lines = [previous.strip()] if previous and previous.strip() else []
    for message in messages:
        role = "\u7528\u6237" if message.role == "user" else "\u52a9\u624b"
        content = " ".join(message.content.split())
        lines.append(f"{role}: {content[:360]}")
    merged = "\n".join(lines)
    if len(merged) <= max_chars:
        return merged
    marker = "[\u65e9\u671f\u5bf9\u8bdd\u5df2\u538b\u7f29]\n"
    return marker + merged[-(max_chars - len(marker)) :]
