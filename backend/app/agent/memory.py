from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .contracts import (
    ConversationMessage,
    LongTermFact,
    MemoryContext,
)
from .ports import (
    ConversationHistoryPort,
    ConversationSummaryPort,
    LongTermMemoryPort,
    MemoryExtractionPort,
)


@dataclass(frozen=True)
class MemoryConfig:
    window_messages: int = 8
    summarize_after_messages: int = 12
    max_long_term_facts: int = 12
    min_fact_confidence: float = 0.65

    def __post_init__(self) -> None:
        if self.window_messages < 1:
            raise ValueError("window_messages must be positive")
        if self.summarize_after_messages < self.window_messages:
            raise ValueError(
                "summarize_after_messages must cover the active window"
            )
        if self.max_long_term_facts < 0:
            raise ValueError("max_long_term_facts must not be negative")
        if not 0.0 <= self.min_fact_confidence <= 1.0:
            raise ValueError("min_fact_confidence must be between 0 and 1")


class MemoryCoordinator:
    """Combines a short window, summary, and sourced long-term facts."""

    def __init__(
        self,
        history: ConversationHistoryPort,
        summary: ConversationSummaryPort,
        long_term: LongTermMemoryPort,
        extractor: MemoryExtractionPort,
        *,
        config: MemoryConfig = MemoryConfig(),
    ) -> None:
        self._history = history
        self._summary = summary
        self._long_term = long_term
        self._extractor = extractor
        self._config = config
        self._summary_cache: dict[str, str] = {}

    async def build_context(
        self, session_id: str, subject_id: str
    ) -> MemoryContext:
        messages = tuple(await self._history.list_messages(session_id))
        summary: str | None = self._summary_cache.get(session_id)
        if len(messages) > self._config.summarize_after_messages:
            older_messages = messages[: -self._config.window_messages]
            summary = await self._summary.summarize(older_messages, summary)
            self._summary_cache[session_id] = summary
        active_facts = tuple(await self._long_term.list_active(subject_id))
        facts = tuple(
            fact
            for fact in active_facts
            if fact.active
            and fact.confidence >= self._config.min_fact_confidence
        )[: self._config.max_long_term_facts]
        return MemoryContext(
            window=messages[-self._config.window_messages :],
            summary=summary,
            facts=facts,
        )

    async def extract_best_effort(
        self, subject_id: str, source: ConversationMessage
    ) -> str | None:
        try:
            extracted = tuple(await self._extractor.extract(source))
            facts = tuple(
                fact
                for fact in extracted
                if fact.source_message_id == source.message_id
                and fact.confidence >= self._config.min_fact_confidence
            )
            if facts:
                await self._long_term.save_extracted(subject_id, facts)
            return None
        except Exception:
            return "memory_extraction_degraded"


class NullMemoryCoordinator:
    async def build_context(
        self, session_id: str, subject_id: str
    ) -> MemoryContext:
        return MemoryContext()

    async def extract_best_effort(
        self, subject_id: str, source: ConversationMessage
    ) -> str | None:
        return None


class InMemoryConversationHistory:
    def __init__(
        self, messages: Sequence[ConversationMessage] = ()
    ) -> None:
        self.messages = list(messages)

    async def list_messages(
        self, session_id: str
    ) -> Sequence[ConversationMessage]:
        return tuple(self.messages)


class InMemoryLongTermMemory:
    """Test/demo adapter. Production persistence stays platform-owned."""

    def __init__(
        self, facts_by_subject: dict[str, list[LongTermFact]] | None = None
    ) -> None:
        self._facts = facts_by_subject or {}

    async def list_active(self, subject_id: str) -> Sequence[LongTermFact]:
        return tuple(
            fact for fact in self._facts.get(subject_id, ()) if fact.active
        )

    async def save_extracted(
        self, subject_id: str, facts: Sequence[LongTermFact]
    ) -> None:
        current = self._facts.setdefault(subject_id, [])
        known = {(item.memory_type, item.content) for item in current}
        current.extend(
            fact
            for fact in facts
            if (fact.memory_type, fact.content) not in known
        )

    async def correct(
        self,
        subject_id: str,
        memory_id: str,
        expected_version: int,
        content: str,
    ) -> LongTermFact:
        index, fact = self._find(subject_id, memory_id)
        if fact.version != expected_version:
            raise ValueError("memory version conflict")
        corrected = fact.corrected(content)
        self._facts[subject_id][index] = corrected
        return corrected

    async def deactivate(
        self,
        subject_id: str,
        memory_id: str,
        expected_version: int,
    ) -> LongTermFact:
        index, fact = self._find(subject_id, memory_id)
        if fact.version != expected_version:
            raise ValueError("memory version conflict")
        deactivated = fact.deactivated()
        self._facts[subject_id][index] = deactivated
        return deactivated

    async def delete(
        self, subject_id: str, memory_id: str, expected_version: int
    ) -> None:
        index, fact = self._find(subject_id, memory_id)
        if fact.version != expected_version:
            raise ValueError("memory version conflict")
        del self._facts[subject_id][index]

    def _find(
        self, subject_id: str, memory_id: str
    ) -> tuple[int, LongTermFact]:
        for index, fact in enumerate(self._facts.get(subject_id, ())):
            if fact.memory_id == memory_id:
                return index, fact
        raise KeyError(memory_id)
