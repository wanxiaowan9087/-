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
    """Zero-token query normalizer for colloquial Chinese support questions."""

    async def rewrite(self, query: str) -> QueryPlan:
        normalized = _normalize(query)
        canonical = _canonicalize(normalized)
        if canonical == normalized:
            return QueryPlan(normalized, (normalized,), "original-query-fallback")
        return QueryPlan(normalized, (canonical,), "deterministic-query-normalization")


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


_COLLOQUIAL_REPLACEMENTS = (
    ("湿哒哒", "潮湿"),
    ("湿漉漉", "潮湿"),
    ("老是", "持续"),
    ("机子", "机器人"),
    ("咋办", "怎么办"),
    ("咋弄", "如何处理"),
    ("咋设置", "如何设置"),
    ("干啥", "做什么"),
    ("弄啥", "做什么"),
    ("行不行", "是否适合"),
    ("能不能", "是否可以"),
    ("咋", "怎么"),
    ("弄", "处理"),
)


def _canonicalize(value: str) -> str:
    """Normalize common colloquialisms without inventing model facts."""
    canonical = value
    for source, target in _COLLOQUIAL_REPLACEMENTS:
        canonical = canonical.replace(source, target)
    return _normalize(canonical)[:240]
