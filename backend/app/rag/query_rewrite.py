from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class QueryPlan:
    original: str
    queries: tuple[str, ...]
    strategy: str


class QueryRewriterPort(Protocol):
    async def rewrite(self, query: str) -> QueryPlan: ...


class DeterministicQueryRewriter:
    """Safe retrieval fallback when the model-based rewrite is unavailable."""

    async def rewrite(self, query: str) -> QueryPlan:
        normalized = _normalize(query)
        return QueryPlan(normalized, (normalized,), "original-query-fallback")


def normalize_queries(original: str, candidates: list[str], *, limit: int = 4) -> QueryPlan:
    normalized_original = _normalize(original)
    queries = [normalized_original]
    for candidate in candidates:
        normalized = _normalize(candidate)
        if normalized and normalized not in queries:
            queries.append(normalized)
        if len(queries) >= limit:
            break
    return QueryPlan(normalized_original, tuple(queries), "llm-query-rewrite")


def _normalize(value: str) -> str:
    return re.sub(r"\s+", " ", value).strip()
