from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.rag.models import Chunk, DocumentType, RetrievalResult, SearchHit
from backend.app.rag.retrieval import MergedRetriever


def _hit(index: int, *, model: str, score: float) -> SearchHit:
    return SearchHit(
        chunk=Chunk(
            document_id=str(uuid4()),
            document_version="v1",
            chunk_id=f"chunk-{index}",
            title=f"{model} 型号定位",
            source="kb://catalog",
            content=f"{model} 适合对应场景。",
            document_type=DocumentType.MARKDOWN,
            metadata={"model": model, "heading": "型号定位"},
        ),
        vector_score=score,
        keyword_score=score,
        fused_score=score,
        rerank_score=score,
    )


class _FixedRetriever:
    def __init__(self, result: RetrievalResult) -> None:
        self._result = result

    async def retrieve(self, _query: str) -> RetrievalResult:
        return self._result


@pytest.mark.asyncio
async def test_merged_retriever_preserves_each_explicit_model_after_final_truncation() -> None:
    # This is the shape produced when the inner multi-query retriever restores
    # a missing model at the end of its reranked list.  The low-scoring model
    # must not disappear when MergedRetriever applies its own result limit.
    primary_hits = tuple(
        [_hit(0, model="X9-EDGE", score=0.95)]
        + [_hit(index, model="X9-EDGE", score=0.90 - index * 0.01) for index in range(1, 8)]
        + [_hit(8, model="M6-TERRA", score=0.20)]
    )
    merged = await MergedRetriever(
        _FixedRetriever(
            RetrievalResult(hits=primary_hits, confidence=0.9, strategy="multi-query")
        ),
        _FixedRetriever(RetrievalResult(hits=(), confidence=0.0, strategy="local")),
        result_limit=8,
    ).retrieve("曜石 Edge 和霞陶分别擅长什么？")

    assert {hit.chunk.metadata["model"] for hit in merged.hits} == {
        "X9-EDGE",
        "M6-TERRA",
    }
