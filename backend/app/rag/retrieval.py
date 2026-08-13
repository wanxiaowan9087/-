from __future__ import annotations

import asyncio
import logging
from collections.abc import Sequence
from dataclasses import replace
from typing import Any

from .lexical import tokenize
from .models import RetrievalResult, ScoredChunk, SearchHit
from .ports import (
    EmbeddingPort,
    KeywordSearchPort,
    RerankerPort,
    RetrieverPort,
    VectorStorePort,
)
from .query_rewrite import QueryRewriterPort

logger = logging.getLogger(__name__)


class RetrievalUnavailable(RuntimeError):
    pass


class IdentityReranker:
    async def rerank(
        self, query: str, hits: Sequence[SearchHit], limit: int
    ) -> Sequence[SearchHit]:
        return tuple(hits[:limit])


class LexicalReranker:
    """Replaceable deterministic reranker used by fixed evaluations."""

    async def rerank(
        self, query: str, hits: Sequence[SearchHit], limit: int
    ) -> Sequence[SearchHit]:
        query_terms = set(tokenize(query))
        reranked: list[SearchHit] = []
        for hit in hits:
            document_terms = set(tokenize(hit.chunk.content))
            overlap = (
                len(query_terms & document_terms) / len(query_terms)
                if query_terms
                else 0.0
            )
            score = min(1.0, 0.62 * overlap + 0.38 * hit.fused_score)
            reranked.append(replace(hit, rerank_score=score))
        reranked.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(reranked[:limit])


class HybridRetriever:
    def __init__(
        self,
        embeddings: EmbeddingPort,
        vector_store: VectorStorePort,
        keyword_search: KeywordSearchPort,
        reranker: RerankerPort,
        *,
        candidate_limit: int = 10,
        result_limit: int = 5,
        rrf_k: int = 60,
    ) -> None:
        if candidate_limit < result_limit or result_limit < 1:
            raise ValueError("candidate_limit must cover result_limit")
        if rrf_k < 1:
            raise ValueError("rrf_k must be positive")
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._keyword_search = keyword_search
        self._reranker = reranker
        self._candidate_limit = candidate_limit
        self._result_limit = result_limit
        self._rrf_k = rrf_k

    async def retrieve(self, query: str) -> RetrievalResult:
        vector_task = asyncio.create_task(self._vector_search(query))
        keyword_task = asyncio.create_task(
            self._keyword_search.search(query, self._candidate_limit)
        )
        vector_result, keyword_result = await asyncio.gather(
            vector_task, keyword_task, return_exceptions=True
        )
        degraded: list[str] = []
        if isinstance(vector_result, BaseException):
            degraded.append("vector_store")
            vectors: Sequence[ScoredChunk] = ()
        else:
            vectors = vector_result
        if isinstance(keyword_result, BaseException):
            degraded.append("keyword_index")
            keywords: Sequence[ScoredChunk] = ()
        else:
            keywords = keyword_result
        if not vectors and not keywords and len(degraded) == 2:
            raise RetrievalUnavailable("all retrieval dependencies failed")

        fused = reciprocal_rank_fusion(
            vectors, keywords, rrf_k=self._rrf_k
        )
        try:
            reranked = tuple(
                await self._reranker.rerank(
                    query, fused, self._result_limit
                )
            )
        except Exception:
            logger.warning(
                "reranker degraded; using fused order",
                exc_info=True,
                extra={"component": "reranker"},
            )
            degraded.append("reranker")
            reranked = tuple(fused[: self._result_limit])
        confidence = _evidence_confidence(reranked)
        return RetrievalResult(
            hits=reranked,
            confidence=confidence,
            degraded_dependencies=tuple(degraded),
            conflicting_sources=_has_conflicts(reranked),
        )

    async def _vector_search(self, query: str) -> Sequence[ScoredChunk]:
        query_vector = await self._embeddings.embed_query(query)
        return await self._vector_store.search(
            query_vector, self._candidate_limit
        )

    async def rerank_candidates(
        self, query: str, hits: Sequence[SearchHit], limit: int
    ) -> tuple[SearchHit, ...]:
        """Expose final reranking without leaking the concrete reranker adapter."""
        return tuple(await self._reranker.rerank(query, hits, limit))


class MergedRetriever:
    """Merge a primary retriever with a dynamic local corpus retriever.

    Uploaded text/markdown files are intentionally available immediately through
    lexical retrieval even before they have been embedded into the durable vector
    store. This keeps the upload workflow useful in local demos and internships.
    """

    def __init__(
        self,
        primary: RetrieverPort,
        secondary: RetrieverPort,
        *,
        result_limit: int = 5,
    ) -> None:
        self._primary = primary
        self._secondary = secondary
        self._result_limit = result_limit

    async def retrieve(self, query: str) -> RetrievalResult:
        primary_task = asyncio.create_task(self._primary.retrieve(query))
        secondary_task = asyncio.create_task(self._secondary.retrieve(query))
        primary, secondary = await asyncio.gather(
            primary_task, secondary_task, return_exceptions=True
        )
        results: list[RetrievalResult] = []
        degraded: list[str] = []
        for name, item in (("primary_retriever", primary), ("local_corpus", secondary)):
            if isinstance(item, BaseException):
                degraded.append(name)
            else:
                results.append(item)
                degraded.extend(item.degraded_dependencies)
        if not results:
            raise RetrievalUnavailable("all merged retrieval dependencies failed")
        merged = _merge_hits([hit for result in results for hit in result.hits])
        return RetrievalResult(
            hits=merged[: self._result_limit],
            confidence=max(
                max(result.confidence for result in results),
                _evidence_confidence(merged[: self._result_limit]),
            ),
            strategy="+".join(dict.fromkeys(result.strategy for result in results)),
            degraded_dependencies=tuple(dict.fromkeys(degraded)),
            conflicting_sources=any(result.conflicting_sources for result in results)
            or _has_conflicts(merged),
        )


class MultiQueryRetriever:
    """Runs rewritten queries through a retriever, then fuses candidates before reranking."""

    def __init__(
        self,
        retriever: HybridRetriever,
        query_rewriter: QueryRewriterPort,
        *,
        result_limit: int = 5,
        rrf_k: int = 60,
    ) -> None:
        self._retriever = retriever
        self._query_rewriter = query_rewriter
        self._result_limit = result_limit
        self._rrf_k = rrf_k

    async def retrieve(self, query: str) -> RetrievalResult:
        plan = await self._query_rewriter.rewrite(query)
        branch_results = await asyncio.gather(
            *(self._retriever.retrieve(item) for item in plan.queries),
            return_exceptions=True,
        )
        successes = [item for item in branch_results if isinstance(item, RetrievalResult)]
        if not successes:
            raise RetrievalUnavailable("all multi-query retrieval branches failed")
        hits = reciprocal_rank_fusion_hits(
            [item.hits for item in successes], rrf_k=self._rrf_k
        )
        try:
            reranked = tuple(
                await self._retriever.rerank_candidates(plan.original, hits, self._result_limit)
            )
            reranker_degraded: tuple[str, ...] = ()
        except Exception:
            logger.warning("multi-query reranker degraded", exc_info=True)
            reranked = hits[: self._result_limit]
            reranker_degraded = ("reranker",)
        return RetrievalResult(
            hits=reranked,
            confidence=_evidence_confidence(reranked),
            strategy=f"{plan.strategy}+multi-query+vector+bm25+rrf+rerank",
            degraded_dependencies=tuple(dict.fromkeys(
                dependency for item in successes for dependency in item.degraded_dependencies
            )) + reranker_degraded,
            conflicting_sources=any(item.conflicting_sources for item in successes)
            or _has_conflicts(reranked),
        )


def _merge_hits(hits: Sequence[SearchHit]) -> tuple[SearchHit, ...]:
    merged: dict[str, SearchHit] = {}
    for hit in hits:
        key = f"{hit.chunk.document_id}:{hit.chunk.chunk_id}"
        existing = merged.get(key)
        if existing is None or hit.score > existing.score:
            merged[key] = hit
    ordered = list(merged.values())
    ordered.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
    return tuple(ordered)


def reciprocal_rank_fusion(
    vector_hits: Sequence[ScoredChunk],
    keyword_hits: Sequence[ScoredChunk],
    *,
    rrf_k: int = 60,
    vector_weight: float = 1.0,
    keyword_weight: float = 1.0,
) -> tuple[SearchHit, ...]:
    combined: dict[str, dict[str, Any]] = {}
    for rank, scored in enumerate(vector_hits, 1):
        state = combined.setdefault(
            scored.chunk.chunk_id,
            {
                "chunk": scored.chunk,
                "vector_score": None,
                "keyword_score": None,
                "rrf": 0.0,
            },
        )
        state["vector_score"] = scored.score
        state["rrf"] += vector_weight / (rrf_k + rank)
    for rank, scored in enumerate(keyword_hits, 1):
        state = combined.setdefault(
            scored.chunk.chunk_id,
            {
                "chunk": scored.chunk,
                "vector_score": None,
                "keyword_score": None,
                "rrf": 0.0,
            },
        )
        state["keyword_score"] = scored.score
        state["rrf"] += keyword_weight / (rrf_k + rank)
    maximum = (vector_weight + keyword_weight) / (rrf_k + 1)
    hits = [
        SearchHit(
            chunk=state["chunk"],
            vector_score=state["vector_score"],
            keyword_score=state["keyword_score"],
            fused_score=min(1.0, state["rrf"] / maximum),
        )
        for state in combined.values()
    ]
    hits.sort(key=lambda item: (-item.fused_score, item.chunk.chunk_id))
    return tuple(hits)


def reciprocal_rank_fusion_hits(
    ranked_lists: Sequence[Sequence[SearchHit]], *, rrf_k: int = 60
) -> tuple[SearchHit, ...]:
    merged: dict[str, tuple[SearchHit, float]] = {}
    for hits in ranked_lists:
        for rank, hit in enumerate(hits, 1):
            key = f"{hit.chunk.document_id}:{hit.chunk.chunk_id}"
            existing = merged.get(key)
            score = (existing[1] if existing else 0.0) + 1 / (rrf_k + rank)
            merged[key] = (hit, score)
    maximum = len(ranked_lists) / (rrf_k + 1)
    output = [
        replace(hit, fused_score=min(1.0, score / maximum), rerank_score=None)
        for hit, score in merged.values()
    ]
    output.sort(key=lambda item: (-item.fused_score, item.chunk.chunk_id))
    return tuple(output)


def _evidence_confidence(hits: Sequence[SearchHit]) -> float:
    if not hits:
        return 0.0
    top = hits[0]
    raw = max(
        value
        for value in (top.vector_score, top.keyword_score, 0.0)
        if value is not None
    )
    dual_bonus = 0.08 if top.modalities == 2 else 0.0
    agreement_bonus = 0.04 if len(hits) > 1 and hits[1].score >= 0.55 else 0.0
    rank_calibration = top.score + (0.12 if top.modalities == 2 else 0.03)
    return round(
        min(1.0, max(raw + dual_bonus + agreement_bonus, rank_calibration)),
        6,
    )


def _has_conflicts(hits: Sequence[SearchHit]) -> bool:
    claims: dict[str, set[str]] = {}
    for hit in hits[:5]:
        if hit.score < 0.58:
            continue
        claim_id = hit.chunk.metadata.get("claim_id")
        claim_value = hit.chunk.metadata.get("claim_value")
        if claim_id is None or claim_value is None:
            continue
        claims.setdefault(str(claim_id), set()).add(str(claim_value))
    return any(len(values) > 1 for values in claims.values())
