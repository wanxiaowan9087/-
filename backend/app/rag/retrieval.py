from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from time import monotonic
from typing import Any

from .evidence import assess_evidence
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
from .retrieval_planning import (
    cohere_reranked_hits,
    is_collection_query,
    route_candidates,
)

logger = logging.getLogger(__name__)


class RetrievalUnavailable(RuntimeError):
    pass


class IdentityReranker:
    async def rerank(
        self, query: str, hits: Sequence[SearchHit], limit: int
    ) -> Sequence[SearchHit]:
        return tuple(hits[:limit])


class LexicalReranker:
    """Replaceable deterministic reranker used by fixed evaluations.

    The identity signal gives explicit model/title/section matches a small
    boost. This is important for manuals whose operational language is nearly
    identical across models, and costs no extra embedding or LLM call.
    """

    async def rerank(
        self, query: str, hits: Sequence[SearchHit], limit: int
    ) -> Sequence[SearchHit]:
        query_terms = set(tokenize(query))
        reranked: list[SearchHit] = []
        for hit in hits:
            document_terms = set(tokenize(hit.chunk.content))
            overlap = len(query_terms & document_terms) / len(query_terms) if query_terms else 0.0
            identity = " ".join(
                (
                    hit.chunk.title,
                    str(hit.chunk.metadata.get("model", "")),
                    str(hit.chunk.metadata.get("heading", "")),
                    hit.chunk.location.section or "",
                )
            )
            identity_terms = set(tokenize(identity))
            identity_overlap = (
                len(query_terms & identity_terms) / len(query_terms) if query_terms else 0.0
            )
            # V3 ablation over the complete 24-document corpus showed that
            # generic body overlap was drowning out exact model/section
            # identity. This zero-token mix improved Recall@5, MRR and nDCG
            # together while retaining the fused signal.
            score = min(
                1.0,
                0.50 * overlap + 0.20 * hit.fused_score + 0.30 * identity_overlap,
            )
            reranked.append(replace(hit, rerank_score=score))
        reranked.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(reranked[:limit])


@dataclass(frozen=True)
class RerankObservation:
    success: bool
    duration_ms: float
    error_type: str | None = None


class FallbackReranker:
    """Use a local reranker when the optional cloud boundary is unavailable."""

    def __init__(
        self,
        primary: RerankerPort | None,
        fallback: RerankerPort,
        *,
        observe: Callable[[RerankObservation], None] | None = None,
    ) -> None:
        self._primary = primary
        self._fallback = fallback
        self._observe = observe

    async def rerank(
        self,
        query: str,
        hits: Sequence[SearchHit],
        limit: int,
    ) -> Sequence[SearchHit]:
        if self._primary is None:
            return await self._fallback.rerank(query, hits, limit)
        started = monotonic()
        try:
            ranked = await self._primary.rerank(query, hits, limit)
        except Exception as error:
            duration_ms = round((monotonic() - started) * 1000, 3)
            self._record(
                RerankObservation(
                    success=False,
                    duration_ms=duration_ms,
                    error_type=type(error).__name__,
                )
            )
            logger.warning(
                "cloud reranker degraded; using local fallback",
                extra={
                    "component": "reranker",
                    "provider": "dashscope",
                    "error_type": type(error).__name__,
                    "duration_ms": round(duration_ms),
                },
            )
            return await self._fallback.rerank(query, hits, limit)
        duration_ms = round((monotonic() - started) * 1000, 3)
        self._record(RerankObservation(success=True, duration_ms=duration_ms))
        logger.info(
            "cloud reranker completed",
            extra={
                "component": "reranker",
                "provider": "dashscope",
                "duration_ms": round(duration_ms),
                "candidate_count": len(hits),
                "result_count": len(ranked),
            },
        )
        return ranked

    def _record(self, observation: RerankObservation) -> None:
        if self._observe is None:
            return
        try:
            self._observe(observation)
        except Exception as error:
            logger.warning(
                "reranker observation callback failed",
                extra={
                    "component": "reranker",
                    "error_type": type(error).__name__,
                },
            )


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

        fused = reciprocal_rank_fusion(vectors, keywords, rrf_k=self._rrf_k)
        routed = route_candidates(query, fused)
        final_limit = max(self._result_limit, 8 if is_collection_query(query) else 4)
        try:
            reranked = tuple(await self._reranker.rerank(query, routed, final_limit))
        except Exception:
            logger.warning(
                "reranker degraded; using fused order",
                exc_info=True,
                extra={"component": "reranker"},
            )
            degraded.append("reranker")
            reranked = tuple(routed[:final_limit])
        confidence = _evidence_confidence(reranked)
        return RetrievalResult(
            hits=reranked,
            confidence=confidence,
            degraded_dependencies=tuple(degraded),
            conflicting_sources=_has_conflicts(reranked),
        )

    async def _vector_search(self, query: str) -> Sequence[ScoredChunk]:
        query_vector = await self._embeddings.embed_query(query)
        return await self._vector_store.search(query_vector, self._candidate_limit)

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
        started = monotonic()
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
        logger.info(
            "merged retrieval completed",
            extra={
                "component": "retrieval",
                "stage": "merged",
                "duration_ms": round((monotonic() - started) * 1000),
                "result_count": len(merged[: self._result_limit]),
            },
        )
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
        candidate_limit: int = 20,
        rrf_k: int = 60,
        final_reranker: RerankerPort | None = None,
    ) -> None:
        if candidate_limit < result_limit or result_limit < 1:
            raise ValueError("candidate_limit must cover result_limit")
        self._retriever = retriever
        self._query_rewriter = query_rewriter
        self._result_limit = result_limit
        self._candidate_limit = candidate_limit
        self._rrf_k = rrf_k
        self._final_reranker = final_reranker

    async def retrieve(self, query: str) -> RetrievalResult:
        started = monotonic()
        plan = await self._query_rewriter.rewrite(query)
        rewrite_finished = monotonic()
        branch_results = await asyncio.gather(
            *(self._retriever.retrieve(item) for item in plan.queries),
            return_exceptions=True,
        )
        successes = [item for item in branch_results if isinstance(item, RetrievalResult)]
        if not successes:
            raise RetrievalUnavailable("all multi-query retrieval branches failed")
        retrieval_finished = monotonic()
        hits = reciprocal_rank_fusion_hits([item.hits for item in successes], rrf_k=self._rrf_k)
        routed = route_candidates(plan.original, hits)
        candidates = routed[: self._candidate_limit]
        final_limit = min(
            self._candidate_limit,
            max(
                self._result_limit,
                8 if is_collection_query(plan.original) else self._result_limit,
            ),
        )
        try:
            if self._final_reranker is None:
                reranked = tuple(
                    await self._retriever.rerank_candidates(
                        plan.original, candidates, final_limit
                    )
                )
            else:
                reranked = tuple(
                    await self._final_reranker.rerank(
                        plan.original, candidates, final_limit
                    )
                )
            reranker_degraded: tuple[str, ...] = ()
        except Exception:
            logger.warning("multi-query reranker degraded", exc_info=True)
            reranked = candidates[:final_limit]
            reranker_degraded = ("reranker",)
        reranked = cohere_reranked_hits(plan.original, reranked)
        logger.info(
            "multi-query retrieval timings",
            extra={
                "component": "retrieval",
                "stage": "multi_query",
                "query_rewrite_ms": round((rewrite_finished - started) * 1000),
                "candidate_retrieval_ms": round((retrieval_finished - rewrite_finished) * 1000),
                "rerank_ms": round((monotonic() - retrieval_finished) * 1000),
                "branch_count": len(plan.queries),
                "result_count": len(reranked),
            },
        )
        return RetrievalResult(
            hits=reranked,
            confidence=_evidence_confidence(reranked),
            strategy=f"{plan.strategy}+multi-query+vector+bm25+rrf+rerank",
            degraded_dependencies=tuple(
                dict.fromkeys(
                    dependency for item in successes for dependency in item.degraded_dependencies
                )
            )
            + reranker_degraded,
            conflicting_sources=any(item.conflicting_sources for item in successes)
            or _has_conflicts(reranked),
        )


class LowConfidenceRetryRetriever:
    """Retry weak retrieval once with an LLM-expanded query plan."""

    def __init__(
        self,
        primary: RetrieverPort,
        retry: RetrieverPort,
        *,
        confidence_threshold: float = 0.5,
    ) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence threshold must be normalized")
        self._primary = primary
        self._retry = retry
        self._confidence_threshold = confidence_threshold

    async def retrieve(self, query: str) -> RetrievalResult:
        primary = await self._primary.retrieve(query)
        primary_assessment = assess_evidence(
            query,
            primary.hits[:4],
            confidence=primary.confidence,
        )
        if primary_assessment.knowledge_boundary_unsupported:
            return primary
        if (
            primary.confidence >= self._confidence_threshold
            and primary_assessment.supported
            and _query_evidence_coverage(query, primary.hits[:4]) >= 0.08
        ):
            return primary
        started = monotonic()
        try:
            retried = await self._retry.retrieve(query)
        except Exception as error:
            logger.warning(
                "low-confidence query rewrite degraded; retaining original retrieval",
                extra={
                    "component": "query-rewrite",
                    "error_type": type(error).__name__,
                    "duration_ms": round((monotonic() - started) * 1000),
                },
            )
            return replace(
                primary,
                degraded_dependencies=tuple(
                    dict.fromkeys((*primary.degraded_dependencies, "query_rewrite"))
                ),
            )
        logger.info(
            "low-confidence query rewrite retry completed",
            extra={
                "component": "query-rewrite",
                "duration_ms": round((monotonic() - started) * 1000),
                "original_confidence": primary.confidence,
                "retry_confidence": retried.confidence,
            },
        )
        retry_assessment = assess_evidence(
            query,
            retried.hits[:4],
            confidence=retried.confidence,
        )
        if (
            retry_assessment.supported
            and _query_evidence_coverage(query, retried.hits[:4])
            > _query_evidence_coverage(query, primary.hits[:4])
        ):
            return retried
        return retried if retried.confidence >= primary.confidence else primary


def _query_evidence_coverage(query: str, hits: Sequence[SearchHit]) -> float:
    query_terms = {term for term in tokenize(query) if len(term) > 1}
    if not query_terms or not hits:
        return 0.0
    evidence_terms = {
        term
        for hit in hits
        for term in tokenize(
            " ".join(
                (
                    hit.chunk.title,
                    str(hit.chunk.metadata.get("model", "")),
                    str(hit.chunk.metadata.get("heading", "")),
                    hit.chunk.content,
                )
            )
        )
        if len(term) > 1
    }
    return len(query_terms & evidence_terms) / len(query_terms)


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
    raw = max(value for value in (top.vector_score, top.keyword_score, 0.0) if value is not None)
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
