from __future__ import annotations

import json
import logging
from uuid import uuid4

import httpx
import pytest

from backend.app.adapters.rerank.dashscope import DashScopeReranker
from backend.app.rag.models import Chunk, DocumentType, SearchHit
from backend.app.rag.retrieval import (
    FallbackReranker,
    LexicalReranker,
    RerankObservation,
    should_use_cloud_rerank,
)


def _hit(name: str, fused_score: float) -> SearchHit:
    chunk = Chunk(
        document_id=str(uuid4()),
        document_version="v1",
        chunk_id=f"chunk-{name}",
        title=name,
        source=f"kb://{name}",
        content=f"{name} 的产品资料",
        document_type=DocumentType.MARKDOWN,
    )
    return SearchHit(
        chunk=chunk,
        vector_score=0.7,
        keyword_score=0.6,
        fused_score=fused_score,
    )


def _model_hit(name: str, model: str, fused_score: float) -> SearchHit:
    hit = _hit(name, fused_score)
    return SearchHit(
        chunk=Chunk(
            document_id=hit.chunk.document_id,
            document_version=hit.chunk.document_version,
            chunk_id=hit.chunk.chunk_id,
            title=hit.chunk.title,
            source=hit.chunk.source,
            content=hit.chunk.content,
            document_type=hit.chunk.document_type,
            metadata={"model": model},
        ),
        vector_score=hit.vector_score,
        keyword_score=hit.keyword_score,
        fused_score=hit.fused_score,
    )


def test_adaptive_rerank_route_keeps_high_confidence_single_model_local() -> None:
    assert not should_use_cloud_rerank(
        "S8 皓月平时怎么保养？",
        (_model_hit("luna", "S8-LUNA", 0.91), _model_hit("luna-2", "S8-LUNA", 0.72)),
    )


def test_adaptive_rerank_route_uses_cloud_for_comparison_and_collection() -> None:
    hits = (_model_hit("luna", "S8-LUNA", 0.91), _model_hit("obsidian", "X9-OBSIDIAN", 0.72))
    assert should_use_cloud_rerank("皓月和曜石分别适合什么场景？", hits)
    assert should_use_cloud_rerank("一共有多少款机器人？", hits)


def test_adaptive_rerank_route_uses_cloud_for_low_confidence_candidates() -> None:
    assert should_use_cloud_rerank(
        "S8 皓月怎么处理异常？",
        (_model_hit("luna", "S8-LUNA", 0.42), _model_hit("luna-2", "S8-LUNA", 0.40)),
    )
    assert not should_use_cloud_rerank(
        "S8 皓月怎么保养？",
        (_model_hit("luna", "S8-LUNA", 0.78), _model_hit("other", "X9-EDGE", 0.77)),
    )


def _fixed_response_transport(
    status_code: int,
    payload: dict[str, object],
) -> httpx.MockTransport:
    async def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code, json=payload, request=request)

    return httpx.MockTransport(handle)


async def test_dashscope_reranker_maps_ranked_indexes_back_to_search_hits() -> None:
    captured: dict[str, object] = {}

    async def handle(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["authorization"] = request.headers.get("Authorization")
        captured["payload"] = json.loads(request.content)
        return httpx.Response(
            200,
            json={
                "output": {
                    "results": [
                        {"index": 1, "relevance_score": 0.92},
                        {"index": 0, "relevance_score": 0.71},
                    ]
                }
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    reranker = DashScopeReranker(client, api_key="test-key", model="qwen3.7-text-rerank")
    hits = (_hit("S8 皓月", 0.8), _hit("X9 曜石", 0.7), _hit("M6 Mini", 0.6))

    ranked = await reranker.rerank("适合地毯的机器人", hits, limit=2)

    assert [hit.chunk.title for hit in ranked] == ["X9 曜石", "S8 皓月"]
    assert [hit.rerank_score for hit in ranked] == [0.92, 0.71]
    assert captured == {
        "url": (
            "https://dashscope.aliyuncs.com/api/v1/services/"
            "rerank/text-rerank/text-rerank"
        ),
        "authorization": "Bearer test-key",
        "payload": {
            "model": "qwen3.7-text-rerank",
            "input": {
                "query": "适合地毯的机器人",
                "documents": [hit.chunk.content for hit in hits],
            },
            "parameters": {"top_n": 2, "return_documents": True},
        },
    }

    await client.aclose()


async def test_dashscope_reranker_caps_top_n_to_available_candidates() -> None:
    payloads: list[dict[str, object]] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        payloads.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={"output": {"results": [{"index": 0, "relevance_score": 0.8}]}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    reranker = DashScopeReranker(client, api_key="test-key", model="test-model")

    await reranker.rerank("query", (_hit("only", 0.8),), limit=8)

    assert payloads[0]["parameters"] == {"top_n": 1, "return_documents": True}
    await client.aclose()


async def test_reranker_falls_back_without_leaking_credentials(
    caplog: pytest.LogCaptureFixture,
) -> None:
    secret = "secret-must-not-appear"

    async def handle(_: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout(f"provider timeout {secret}")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    primary = DashScopeReranker(client, api_key=secret, model="test-model")
    observations: list[RerankObservation] = []
    reranker = FallbackReranker(primary, LexicalReranker(), observe=observations.append)
    hits = (_hit("S8 皓月", 0.8), _hit("X9 曜石", 0.7))

    with caplog.at_level(logging.WARNING):
        ranked = await reranker.rerank("曜石", hits, limit=1)

    assert [hit.chunk.title for hit in ranked] == ["X9 曜石"]
    assert secret not in caplog.text
    assert caplog.records[0].error_type == "ReadTimeout"
    assert len(observations) == 1
    assert not observations[0].success
    assert observations[0].error_type == "ReadTimeout"
    await client.aclose()


async def test_reranker_falls_back_for_provider_and_payload_failures() -> None:
    responses = (
        httpx.Response(429, json={"message": "rate limited"}),
        httpx.Response(503, json={"message": "unavailable"}),
        httpx.Response(200, json={"output": {"results": [{"index": 99}]}}),
    )

    for provider_response in responses:
        client = httpx.AsyncClient(
            transport=_fixed_response_transport(
                provider_response.status_code,
                provider_response.json(),
            )
        )
        primary = DashScopeReranker(client, api_key="test-key", model="test-model")
        reranker = FallbackReranker(primary, LexicalReranker())
        hits = (_hit("S8 皓月", 0.8), _hit("X9 曜石", 0.7))

        ranked = await reranker.rerank("曜石", hits, limit=1)

        assert [hit.chunk.title for hit in ranked] == ["X9 曜石"]
        await client.aclose()


async def test_reranker_uses_local_fallback_when_cloud_is_not_configured() -> None:
    hits = (_hit("S8 皓月", 0.8), _hit("X9 曜石", 0.7))
    reranker = FallbackReranker(None, LexicalReranker())

    ranked = await reranker.rerank("曜石", hits, limit=1)

    assert [hit.chunk.title for hit in ranked] == ["X9 曜石"]
