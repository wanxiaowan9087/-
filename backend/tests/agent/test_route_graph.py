from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.agent.contracts import ConversationMessage, MemoryContext
from backend.app.agent.route_graph import (
    contextualize_retrieval_query,
    route_memory_then_knowledge,
)
from backend.app.rag.models import Chunk, DocumentType, RetrievalResult, SearchHit


def _retrieval() -> RetrievalResult:
    chunk = Chunk(
        document_id="00000000-0000-0000-0000-000000000111",
        document_version="v1",
        chunk_id="chunk-1",
        title="滤网维护",
        source="kb://filter",
        content="每月清洁一次滤网。",
        document_type=DocumentType.FAQ,
    )
    return RetrievalResult(
        hits=(SearchHit(chunk, 0.9, 0.8, 0.9),), confidence=0.9, strategy="test"
    )


@pytest.mark.asyncio
async def test_grounded_route_polishes_retrieved_evidence_before_generation() -> None:
    calls: list[str] = []

    async def retrieve(query: str) -> RetrievalResult:
        return _retrieval()

    async def polish(query: str, retrieval: RetrievalResult) -> str:
        calls.append(query)
        return "整理后的中文事实摘要"

    result = await route_memory_then_knowledge(
        "滤网多久清理一次？",
        MemoryContext(
            window=(
                ConversationMessage("m1", "user", "滤网多久清理一次？", datetime.now(UTC)),
            )
        ),
        answer_memory=lambda _query, _context: None,
        retrieve=retrieve,
        polish=polish,
    )

    assert result["route"] == "knowledge"
    assert result["polished_context"] == "整理后的中文事实摘要"
    assert calls == ["滤网多久清理一次？"]


def test_contextual_followup_carries_previous_subject_into_retrieval() -> None:
    context = MemoryContext(
        window=(
            ConversationMessage(
                "m1", "user", "夏季怎么保养扫地机器人？", datetime.now(UTC)
            ),
            ConversationMessage(
                "m2", "assistant", "夏季应定期清洁滤网。", datetime.now(UTC)
            ),
            ConversationMessage("m3", "user", "秋季呢", datetime.now(UTC)),
        )
    )

    assert contextualize_retrieval_query("秋季呢", context) == "怎么保养扫地机器人 秋季呢"


def test_non_elliptical_query_is_not_changed() -> None:
    context = MemoryContext(
        window=(
            ConversationMessage("m1", "user", "夏季怎么保养扫地机器人？", datetime.now(UTC)),
        )
    )

    assert contextualize_retrieval_query(
        "秋季怎么保养扫地机器人", context
    ) == "秋季怎么保养扫地机器人"


@pytest.mark.asyncio
async def test_route_passes_contextualized_followup_to_retriever() -> None:
    queries: list[str] = []

    async def retrieve(query: str) -> RetrievalResult:
        queries.append(query)
        return _retrieval()

    context = MemoryContext(
        window=(
            ConversationMessage("m1", "user", "夏季怎么保养扫地机器人？", datetime.now(UTC)),
            ConversationMessage("m2", "assistant", "夏季应清洁滤网。", datetime.now(UTC)),
            ConversationMessage("m3", "user", "秋季呢", datetime.now(UTC)),
        )
    )
    result = await route_memory_then_knowledge(
        "秋季呢", context, answer_memory=lambda _query, _context: None, retrieve=retrieve
    )

    assert result["route"] == "knowledge"
    assert queries == ["怎么保养扫地机器人 秋季呢"]


@pytest.mark.asyncio
async def test_grounded_route_falls_back_to_raw_evidence_when_polish_fails() -> None:
    retrieval = _retrieval()

    async def polish(_query: str, _retrieval: RetrievalResult) -> str:
        raise RuntimeError("qwen unavailable")

    result = await route_memory_then_knowledge(
        "滤网多久清理一次？",
        MemoryContext(),
        answer_memory=lambda _query, _context: None,
        retrieve=lambda _query: _retrieve_result(retrieval),
        polish=polish,
    )

    assert result["route"] == "knowledge"
    assert result.get("polished_context") is None


@pytest.mark.asyncio
async def test_grounded_route_does_not_polish_without_evidence() -> None:
    calls = 0

    async def polish(_query: str, _retrieval: RetrievalResult) -> str:
        nonlocal calls
        calls += 1
        return "不应调用"

    result = await route_memory_then_knowledge(
        "资料中没有的问题",
        MemoryContext(),
        answer_memory=lambda _query, _context: None,
        retrieve=lambda _query: _retrieve_result(
            RetrievalResult(hits=(), confidence=0.0)
        ),
        polish=polish,
    )

    assert result["route"] == "insufficient"
    assert result.get("polished_context") is None
    assert calls == 0


@pytest.mark.asyncio
async def test_high_confidence_route_skips_extra_evidence_model_call() -> None:
    calls = 0

    async def polish(_query: str, _retrieval: RetrievalResult) -> str:
        nonlocal calls
        calls += 1
        return "不应调用"

    high_confidence = RetrievalResult(
        hits=_retrieval().hits, confidence=0.97, strategy="test"
    )
    result = await route_memory_then_knowledge(
        "滤网多久清理一次？",
        MemoryContext(),
        answer_memory=lambda _query, _context: None,
        retrieve=lambda _query: _retrieve_result(high_confidence),
        polish=polish,
    )

    assert result["route"] == "knowledge"
    assert result.get("polished_context") is None
    assert calls == 0


async def _retrieve_result(result: RetrievalResult) -> RetrievalResult:
    return result
