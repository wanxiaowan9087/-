from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

import pytest

from backend.app.rag.models import Chunk, DocumentType, ScoredChunk, SearchHit
from backend.app.rag.retrieval import HybridRetriever, IdentityReranker
from backend.app.rag.retrieval_planning import (
    cohere_reranked_hits,
    route_candidates,
    select_context_hits,
)
from backend.app.rag.security import render_untrusted_context


def _hit(index: int, *, product_id: str | None = None) -> SearchHit:
    document_id = str(uuid5(NAMESPACE_URL, "catalog" if product_id else f"doc-{index}"))
    return SearchHit(
        chunk=Chunk(
            document_id=document_id,
            document_version="v1",
            chunk_id=f"{document_id}:{index}",
            title=f"资料 {index}",
            source="kb://test",
            content=f"第 {index} 条可靠证据",
            document_type=DocumentType.MARKDOWN,
            metadata={"product_id": product_id} if product_id else {},
        ),
        vector_score=0.9 - index * 0.01,
        keyword_score=0.8 - index * 0.01,
        fused_score=0.9 - index * 0.01,
    )


def test_context_budget_is_small_for_normal_questions_but_complete_for_catalog_lists() -> None:
    ordinary = tuple(_hit(index) for index in range(8))
    products = tuple(_hit(index, product_id=f"product-{index}") for index in range(6))

    assert len(select_context_hits("机器人漏水怎么处理", ordinary)) == 4
    selected = select_context_hits("请介绍全部产品并列出所有型号", products)

    assert len(selected) == 6
    assert {hit.chunk.metadata["product_id"] for hit in selected} == {
        f"product-{index}" for index in range(6)
    }
    rendered = render_untrusted_context(selected)
    for hit in selected:
        assert hit.chunk.chunk_id in rendered


def test_context_selector_prefers_requested_model_and_matching_section() -> None:
    def product_hit(
        index: int,
        *,
        model: str,
        heading: str,
        content: str,
        score: float,
    ) -> SearchHit:
        base = _hit(index)
        return SearchHit(
            chunk=Chunk(
                document_id=base.chunk.document_id,
                document_version="v1",
                chunk_id=base.chunk.chunk_id,
                title=f"{model} 使用说明",
                source="kb://manual",
                content=content,
                document_type=DocumentType.MARKDOWN,
                metadata={"model": model, "heading": heading},
            ),
            vector_score=score,
            keyword_score=score,
            fused_score=score,
            rerank_score=score,
        )

    candidates = (
        product_hit(
            0,
            model="X9-EDGE",
            heading="核心功能",
            content="其他型号的核心功能。",
            score=0.99,
        ),
        product_hit(
            1,
            model="S8-LUNA",
            heading="型号定位",
            content="皓月适合夜间清洁。",
            score=0.96,
        ),
        product_hit(
            2,
            model="S8-LUNA",
            heading="组件与核心功能",
            content="皓月的激光导航、自动集尘与拖布烘干。",
            score=0.91,
        ),
    )

    selected = select_context_hits("皓月明确具备哪些核心功能？", candidates)

    assert selected[0].chunk.metadata["heading"] == "组件与核心功能"
    assert all(hit.chunk.metadata["model"] == "S8-LUNA" for hit in selected)


def test_post_rerank_cohesion_keeps_requested_model_section_together() -> None:
    def product_hit(index: int, model: str, heading: str, score: float) -> SearchHit:
        base = _hit(index)
        return SearchHit(
            chunk=Chunk(
                document_id=base.chunk.document_id,
                document_version="v1",
                chunk_id=base.chunk.chunk_id,
                title=f"{model} 使用说明",
                source="kb://manual",
                content=f"{model} {heading} 的可靠资料。",
                document_type=DocumentType.MARKDOWN,
                metadata={"model": model, "heading": heading},
            ),
            vector_score=score,
            keyword_score=score,
            fused_score=score,
            rerank_score=score,
        )

    hits = (
        product_hit(0, "S8-LUNA", "组件与功能", 0.78),
        product_hit(1, "X9-EDGE", "组件与功能", 0.82),
        product_hit(2, "S8-LUNA", "维护建议", 0.75),
    )

    ordered = cohere_reranked_hits("皓月有哪些核心功能？", hits)

    assert [hit.chunk.metadata["model"] for hit in ordered[:2]] == ["S8-LUNA", "S8-LUNA"]
    assert ordered[0].chunk.metadata["heading"] == "组件与功能"


def test_fault_route_prioritizes_fault_and_safety_without_deleting_candidates() -> None:
    def titled(index: int, title: str, content: str) -> SearchHit:
        base = _hit(index)
        return SearchHit(
            chunk=Chunk(
                document_id=base.chunk.document_id,
                document_version="v1",
                chunk_id=base.chunk.chunk_id,
                title=title,
                source="kb://test",
                content=content,
                document_type=DocumentType.TEXT,
            ),
            vector_score=base.vector_score,
            keyword_score=base.keyword_score,
            fused_score=base.fused_score,
        )

    candidates = (
        titled(0, "主流品牌与型号速查", "品牌定位与代表型号"),
        titled(1, "工作原理与技术解析", "导航与电机工作原理"),
        titled(2, "故障排除", "拖地时水箱漏水的检查步骤"),
        titled(3, "安全使用规范", "发生漏水后应断电并停止运行"),
    )

    selected = route_candidates("机器人漏水了应该怎么处理", candidates)

    assert [hit.chunk.title for hit in selected[:2]] == ["故障排除", "安全使用规范"]
    assert {hit.chunk.title for hit in selected} == {
        "主流品牌与型号速查",
        "工作原理与技术解析",
        "故障排除",
        "安全使用规范",
    }


@pytest.mark.asyncio
async def test_hybrid_retriever_applies_domain_route_before_final_top_k() -> None:
    candidates = tuple(
        ScoredChunk(hit.chunk, hit.fused_score)
        for hit in (
            _hit_with_title(0, "主流品牌与型号速查", "品牌定位"),
            _hit_with_title(1, "工作原理与技术解析", "导航原理"),
            _hit_with_title(2, "故障排除", "水箱漏水的检查步骤"),
            _hit_with_title(3, "安全使用规范", "漏水后断电停用"),
        )
    )

    class Embeddings:
        async def embed_query(self, _query: str) -> tuple[float, ...]:
            return (1.0,)

    class Search:
        async def search(self, _value: object, _limit: int) -> tuple[ScoredChunk, ...]:
            return candidates

    result = await HybridRetriever(
        Embeddings(), Search(), Search(), IdentityReranker(), candidate_limit=4, result_limit=4
    ).retrieve("机器人漏水了应该怎么处理")

    assert [hit.chunk.title for hit in result.hits[:2]] == ["故障排除", "安全使用规范"]
    assert len(result.hits) == 4


@pytest.mark.asyncio
async def test_retriever_keeps_ranking_depth_while_model_context_stays_bounded() -> None:
    candidates = tuple(
        ScoredChunk(_hit(index).chunk, 0.95 - index * 0.01) for index in range(10)
    )

    class Embeddings:
        async def embed_query(self, _query: str) -> tuple[float, ...]:
            return (1.0,)

    class Search:
        async def search(self, _value: object, _limit: int) -> tuple[ScoredChunk, ...]:
            return candidates

    result = await HybridRetriever(
        Embeddings(), Search(), Search(), IdentityReranker(), candidate_limit=10, result_limit=10
    ).retrieve("机器人平时如何使用")

    assert len(result.hits) == 10
    assert len(select_context_hits("机器人平时如何使用", result.hits)) == 4


def _hit_with_title(index: int, title: str, content: str) -> SearchHit:
    base = _hit(index)
    return SearchHit(
        chunk=Chunk(
            document_id=base.chunk.document_id,
            document_version="v1",
            chunk_id=base.chunk.chunk_id,
            title=title,
            source="kb://test",
            content=content,
            document_type=DocumentType.TEXT,
        ),
        vector_score=base.vector_score,
        keyword_score=base.keyword_score,
        fused_score=base.fused_score,
    )
