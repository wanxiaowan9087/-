from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from backend.app.agent.contracts import (
    ConversationMessage,
    LongTermFact,
    MemoryContext,
    MemoryType,
)
from backend.app.repositories.ports import PlatformRepository


class PlatformMemoryRuntime:
    """Async bridge from platform-owned message/memory records to AgentRuntime."""

    def __init__(self, repository: PlatformRepository, *, window_messages: int = 8) -> None:
        self._repository = repository
        self._window_messages = window_messages

    async def build_context(self, session_id: str, subject_id: str) -> MemoryContext:
        session_uuid = UUID(session_id)
        async with self._repository.transaction() as tx:
            messages = await tx.list_messages(
                subject_id,
                session_uuid,
                limit=max(50, self._window_messages),
                after=None,
            )
            memories = await tx.list_memories(
                subject_id,
                status="active",
                memory_type=None,
                limit=12,
                after=None,
            )
        visible_messages = [message for message in messages if message.content]
        window = tuple(
            ConversationMessage(
                message_id=str(message.id),
                role=message.role,
                content=message.content,
                created_at=message.created_at or datetime.now(UTC),
            )
            for message in visible_messages[-self._window_messages :]
        )
        facts = tuple(
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
        return MemoryContext(window=window, summary=None, facts=facts)

    async def extract_best_effort(
        self, subject_id: str, source: ConversationMessage
    ) -> str | None:
        candidate = _explicit_preference(source.content)
        if candidate is None:
            return None
        async with self._repository.transaction() as tx:
            existing = await tx.list_memories(
                subject_id,
                status="active",
                memory_type="preference",
                limit=100,
                after=None,
            )
            if any(memory.content == candidate for memory in existing):
                return None
            await tx.create_memory(
                subject_id,
                memory_type="preference",
                content=candidate,
                confidence=0.9,
                source_message_id=UUID(source.message_id),
                now=datetime.now(UTC),
            )
        return None


def _explicit_preference(content: str) -> str | None:
    normalized = " ".join(content.split())
    markers = ("我偏好", "我喜欢", "请用", "请不要", "i prefer", "please use")
    if not normalized or len(normalized) > 500:
        return None
    if normalized.lower().startswith(markers) or any(
        normalized.lower().startswith(marker) for marker in markers
    ):
        return normalized
    return None
