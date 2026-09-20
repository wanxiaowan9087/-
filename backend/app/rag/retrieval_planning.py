from __future__ import annotations

import re
from collections import Counter
from collections.abc import Sequence
from dataclasses import replace
from enum import StrEnum

from .lexical import tokenize
from .models import SearchHit

_COLLECTION_MARKERS = (
    "全部",
    "所有",
    "完整",
    "全系",
    "产品目录",
    "产品清单",
    "型号列表",
    "有哪些产品",
    "有多少款",
    "一共几款",
)

_MODEL_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("X9-EDGE", ("x9-edge", "x9 edge", "曜石 edge", "曜石edge")),
    ("S8-LUNA", ("s8-luna", "s8 luna", "皓月")),
    ("S8-AIR", ("s8-air", "s8 air", "轻羽")),
    ("X9-OBSIDIAN", ("x9-obsidian", "x9 obsidian", "曜石")),
    ("M6-TERRA", ("m6-terra", "m6 terra", "霞陶")),
    ("M6-MINI", ("m6-mini", "m6 mini", "小径")),
)


class RetrievalCategory(StrEnum):
    UNKNOWN = "unknown"
    PRODUCT = "product"
    SELECTION = "selection"
    TROUBLESHOOTING = "troubleshooting"
    SAFETY = "safety"
    MAINTENANCE = "maintenance"
    TECHNICAL = "technical"
    FAQ = "faq"


_QUERY_ROUTES: tuple[tuple[tuple[str, ...], frozenset[RetrievalCategory]], ...] = (
    (
        (
            "故障",
            "报错",
            "错误码",
            "漏水",
            "不出水",
            "不转",
            "异响",
            "无法回充",
            "找不到充电座",
            "开不了机",
            "没反应",
        ),
        frozenset(
            {
                RetrievalCategory.TROUBLESHOOTING,
                RetrievalCategory.SAFETY,
                RetrievalCategory.MAINTENANCE,
                RetrievalCategory.FAQ,
            }
        ),
    ),
    (
        ("保养", "维护", "清理", "清洗", "滤网", "主刷", "边刷", "拖布", "耗材"),
        frozenset(
            {
                RetrievalCategory.MAINTENANCE,
                RetrievalCategory.SAFETY,
                RetrievalCategory.FAQ,
            }
        ),
    ),
    (
        ("安全", "冒烟", "焦味", "异味", "发热", "鼓包", "儿童", "宠物"),
        frozenset(
            {
                RetrievalCategory.SAFETY,
                RetrievalCategory.TROUBLESHOOTING,
                RetrievalCategory.FAQ,
            }
        ),
    ),
    (
        ("选购", "推荐", "买哪", "怎么选", "适合", "预算", "品牌", "对比"),
        frozenset(
            {
                RetrievalCategory.PRODUCT,
                RetrievalCategory.SELECTION,
                RetrievalCategory.FAQ,
            }
        ),
    ),
    (
        ("原理", "什么是", "术语", "lds", "slam", "tof", "为什么"),
        frozenset({RetrievalCategory.TECHNICAL, RetrievalCategory.FAQ}),
    ),
)

_QUERY_SECTION_MARKERS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("功能", "特点", "配置", "具备"), ("型号定位", "组件与功能", "功能")),
    (("保养", "维护", "清理", "清洗", "耗材"), ("维护建议", "常见问题", "保养")),
    (("安全", "风险", "注意", "发热", "漏水"), ("安全使用", "安全", "常见问题")),
    (("故障", "报错", "异常", "无法", "怎么办"), ("常见问题", "故障", "排除")),
    (("首次", "建图", "设置", "安装"), ("首次", "推荐使用方式", "设置")),
)


def _query_categories(query: str) -> frozenset[RetrievalCategory]:
    compact = re.sub(r"\s+", "", query).casefold()
    categories: set[RetrievalCategory] = set()
    for markers, routed in _QUERY_ROUTES:
        if any(marker in compact for marker in markers):
            categories.update(routed)
    if any(
        marker in compact
        for marker in (
            "s8",
            "x9",
            "m6",
            "皓月",
            "轻羽",
            "曜石",
            "霞陶",
            "小径",
            "型号",
            "产品",
        )
    ):
        categories.add(RetrievalCategory.PRODUCT)
    return frozenset(categories)


def _document_category(hit: SearchHit) -> RetrievalCategory:
    identity = " ".join(
        (
            hit.chunk.title,
            hit.chunk.source,
            str(hit.chunk.metadata.get("heading", "")),
            str(hit.chunk.metadata.get("document_category", "")),
        )
    ).casefold()
    if "100问" in identity:
        return RetrievalCategory.FAQ
    if any(marker in identity for marker in ("故障", "排除", "决策树")):
        return RetrievalCategory.TROUBLESHOOTING
    if "安全" in identity:
        return RetrievalCategory.SAFETY
    if any(marker in identity for marker in ("维护", "保养", "耗材", "更换指南")):
        return RetrievalCategory.MAINTENANCE
    if any(marker in identity for marker in ("工作原理", "技术解析", "核心术语")):
        return RetrievalCategory.TECHNICAL
    if any(marker in identity for marker in ("选购", "主流品牌", "家庭场景", "功能与组件")):
        return RetrievalCategory.SELECTION
    model = str(hit.chunk.metadata.get("model", "")).strip().casefold()
    if model and model not in {"通用", "通用型号", "unknown", "all"}:
        return RetrievalCategory.PRODUCT
    if any(
        marker in identity
        for marker in (
            "s8-luna",
            "s8-air",
            "x9-obsidian",
            "x9-edge",
            "m6-terra",
            "m6-mini",
            "皓月",
            "轻羽",
            "曜石",
            "霞陶",
            "小径",
            "型号目录",
        )
    ):
        return RetrievalCategory.PRODUCT
    return RetrievalCategory.UNKNOWN


def route_candidates(query: str, hits: Sequence[SearchHit]) -> tuple[SearchHit, ...]:
    """Soft-route domain evidence without deleting recall candidates."""

    categories = _query_categories(query)
    if not categories:
        return tuple(hits)
    routed: list[SearchHit] = []
    fallback: list[SearchHit] = []
    for hit in hits:
        category = _document_category(hit)
        if category in categories:
            routed.append(
                replace(hit, fused_score=min(1.0, hit.fused_score + 0.08))
            )
        else:
            fallback.append(hit)
    return tuple((*routed, *fallback)) if routed else tuple(hits)


def cohere_reranked_hits(query: str, hits: Sequence[SearchHit]) -> tuple[SearchHit, ...]:
    """Apply a deterministic, post-provider tie-break for intent and section cohesion.

    The cloud reranker remains the primary signal. This local correction keeps
    a requested model/section together without another model call or a larger
    retrieval budget.
    """

    if len(hits) < 2:
        return tuple(hits)
    compact = re.sub(r"\s+", "", query).casefold()
    requested_model = None if is_collection_query(query) else _requested_model(query)
    preferred_sections = tuple(
        section
        for markers, sections in _QUERY_SECTION_MARKERS
        if any(marker in compact for marker in markers)
        for section in sections
    )
    query_terms = set(tokenize(query))

    def score(item: tuple[int, SearchHit]) -> tuple[float, int]:
        index, hit = item
        identity = " ".join(
            (
                hit.chunk.title,
                str(hit.chunk.metadata.get("model", "")),
                str(hit.chunk.metadata.get("heading", "")),
                hit.chunk.location.section or "",
            )
        ).casefold()
        evidence_terms = set(tokenize(identity + " " + hit.chunk.content))
        coverage = (
            len(query_terms & evidence_terms) / len(query_terms) if query_terms else 0.0
        )
        heading = str(hit.chunk.metadata.get("heading", ""))
        section_match = bool(
            preferred_sections
            and any(marker.casefold() in heading.casefold() for marker in preferred_sections)
        )
        model_match = requested_model is not None and _hit_model(hit) == requested_model
        other_model = requested_model is not None and _hit_model(hit) not in {
            None,
            requested_model,
        }
        adjusted = (
            0.78 * hit.score
            + 0.12 * coverage
            + (0.08 if section_match else 0.0)
            + (0.12 if model_match else 0.0)
            - (0.12 if other_model else 0.0)
        )
        return adjusted, -index

    return tuple(hit for _, hit in sorted(enumerate(hits), key=score, reverse=True))


def is_collection_query(query: str) -> bool:
    compact = re.sub(r"\s+", "", query).casefold()
    return any(marker in compact for marker in _COLLECTION_MARKERS)


def select_context_hits(
    query: str,
    hits: Sequence[SearchHit],
    *,
    ordinary_limit: int = 4,
    collection_limit: int = 8,
) -> tuple[SearchHit, ...]:
    """Apply a bounded evidence budget without truncating collection results.

    Product catalog entries share one document ID, so ``product_id`` is the
    evidence group when present. Other sources are capped per document to stop
    one long generic file from filling the whole model context.
    """

    collection = is_collection_query(query)
    limit = collection_limit if collection else ordinary_limit
    requested_model = None if collection else _requested_model(query)
    candidates = tuple(hits)
    if requested_model is not None:
        matching = tuple(hit for hit in candidates if _hit_model(hit) == requested_model)
        if matching:
            generic = tuple(
                hit for hit in candidates if _hit_model(hit) is None
            )
            candidates = (*matching, *generic)
    if not collection:
        candidates = tuple(
            sorted(
                candidates,
                key=lambda hit: -_context_relevance(query, hit),
            )
        )
    per_group_limit = limit if collection else (3 if requested_model else 2)
    selected: list[SearchHit] = []
    counts: Counter[str] = Counter()
    for hit in candidates:
        product_id = str(hit.chunk.metadata.get("product_id", "")).strip()
        group = product_id or hit.chunk.document_id
        if counts[group] >= per_group_limit:
            continue
        selected.append(hit)
        counts[group] += 1
        if len(selected) >= limit:
            break
    return tuple(selected)


def _requested_model(query: str) -> str | None:
    compact = re.sub(r"\s+", " ", query).casefold()
    for model, aliases in _MODEL_ALIASES:
        if any(alias in compact for alias in aliases):
            return model
    return None


def _hit_model(hit: SearchHit) -> str | None:
    declared = str(hit.chunk.metadata.get("model", "")).strip().upper()
    if declared and declared not in {"通用", "通用型号", "UNKNOWN", "ALL"}:
        return declared
    identity = " ".join(
        (
            hit.chunk.title,
            str(hit.chunk.metadata.get("heading", "")),
            hit.chunk.location.section or "",
        )
    ).casefold()
    for model, aliases in _MODEL_ALIASES:
        if any(alias in identity for alias in aliases):
            return model
    return None


def _context_relevance(query: str, hit: SearchHit) -> float:
    query_terms = set(tokenize(query))
    if not query_terms:
        return hit.score
    heading = " ".join(
        (
            hit.chunk.title,
            str(hit.chunk.metadata.get("heading", "")),
            hit.chunk.location.section or "",
        )
    )
    heading_terms = set(tokenize(heading))
    evidence_terms = heading_terms | set(tokenize(hit.chunk.content))
    evidence_coverage = len(query_terms & evidence_terms) / len(query_terms)
    heading_coverage = len(query_terms & heading_terms) / len(query_terms)
    return 0.70 * hit.score + 0.20 * evidence_coverage + 0.10 * heading_coverage
