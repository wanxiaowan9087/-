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
    (("适合", "场景", "家庭", "定位", "擅长"), ("型号定位",)),
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
    compact = re.sub(r"\s+", "", query).casefold()
    requested_models = _requested_models(query)
    preferred_sections = tuple(
        section
        for markers, sections in _QUERY_SECTION_MARKERS
        if any(marker in compact for marker in markers)
        for section in sections
    )
    if not categories and not requested_models:
        return tuple(hits)
    routed: list[SearchHit] = []
    for hit in hits:
        category = _document_category(hit)
        identity = " ".join(
            (
                str(hit.chunk.metadata.get("heading", "")),
                hit.chunk.location.section or "",
            )
        ).casefold()
        model = _hit_model(hit)
        section_match = bool(
            preferred_sections
            and any(section.casefold() in identity for section in preferred_sections)
        )
        model_match = bool(requested_models and model in requested_models)
        boost = 0.08 if category in categories else 0.0
        adjusted_score = min(1.0, hit.fused_score + boost)
        if model_match and section_match:
            # This is a coverage guarantee, not a relevance claim: a clearly
            # named model's intent section must survive the bounded cutoff.
            adjusted_score = max(adjusted_score, 0.90)
        routed.append(
            replace(hit, fused_score=adjusted_score)
        )
    routed.sort(key=lambda item: (-item.fused_score, item.chunk.chunk_id))
    return tuple(routed)


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
    requested_models = () if collection else _requested_models(query)
    requested_model = requested_models[0] if len(requested_models) == 1 else None
    candidates = tuple(hits)
    if requested_models:
        matching = tuple(hit for hit in candidates if _hit_model(hit) in requested_models)
        if matching:
            generic = tuple(
                hit for hit in candidates if _hit_model(hit) is None
            )
            # A comparison question may explicitly name several models. Keep
            # every named model in the candidate set; the previous singular
            # filter silently discarded all but the first one.
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

    # A comparison must carry at least one evidence slot for every explicitly
    # named model. Otherwise several strong chunks for the first model can
    # consume the bounded context before the second model is represented.
    if len(requested_models) > 1:
        for model in requested_models:
            representative = next(
                (hit for hit in candidates if _hit_model(hit) == model),
                None,
            )
            if representative is None:
                continue
            selected.append(representative)
            product_id = str(representative.chunk.metadata.get("product_id", "")).strip()
            counts[product_id or representative.chunk.document_id] += 1

    for hit in candidates:
        if hit in selected:
            continue
        product_id = str(hit.chunk.metadata.get("product_id", "")).strip()
        group = product_id or hit.chunk.document_id
        if counts[group] >= per_group_limit:
            continue
        selected.append(hit)
        counts[group] += 1
        if len(selected) >= limit:
            break
    return tuple(selected)


def ensure_explicit_model_coverage(
    query: str,
    reranked_hits: Sequence[SearchHit],
    candidate_hits: Sequence[SearchHit],
    *,
    limit: int | None = None,
) -> tuple[SearchHit, ...]:
    """Restore one candidate for each explicitly named model after truncation.

    When a downstream merge has its own result budget, ``limit`` keeps the
    coverage repair bounded by evicting the lowest-relevance duplicate model
    before evicting the only representative of an explicitly named model.
    """

    requested_models = _requested_models(query)
    if not requested_models:
        return tuple(reranked_hits[:limit] if limit is not None else reranked_hits)
    output = list(reranked_hits[:limit] if limit is not None else reranked_hits)
    present = {_hit_model(hit) for hit in output}
    for model in requested_models:
        if model in present:
            continue
        representative = next(
            (
                hit
                for hit in sorted(
                    candidate_hits,
                    key=lambda item: -_context_relevance(query, item),
                )
                if _hit_model(hit) == model
            ),
            None,
        )
        if representative is not None:
            if limit is not None and len(output) >= limit:
                requested_counts = Counter(_hit_model(hit) for hit in output)
                removable = [
                    (index, hit)
                    for index, hit in enumerate(output)
                    if _hit_model(hit) not in requested_models
                    or requested_counts[_hit_model(hit)] > 1
                ]
                if removable:
                    remove_index, _ = min(
                        removable,
                        key=lambda item: _context_relevance(query, item[1]),
                    )
                    output.pop(remove_index)
            output.append(representative)
            present.add(model)
    return tuple(output)


def _requested_model(query: str) -> str | None:
    models = _requested_models(query)
    return models[0] if models else None


def _requested_models(query: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", query).casefold()
    matches: list[tuple[int, str, str]] = []
    for model, aliases in _MODEL_ALIASES:
        for alias in aliases:
            normalized_alias = re.sub(r"\s+", "", alias).casefold()
            if normalized_alias in compact:
                matches.append((len(normalized_alias), model, normalized_alias))
    found: list[str] = []
    accepted_aliases: list[tuple[str, str]] = []
    for _, model, alias in sorted(matches, key=lambda item: -item[0]):
        # Prefer a more specific alias.  For example, “曜石 Edge” must not
        # also activate the shorter plain “曜石” alias for X9-OBSIDIAN.
        if model == "X9-OBSIDIAN" and any(
            marker in compact for marker in ("x9edge", "曜石edge", "边角")
        ):
            continue
        if any(
            model != selected_model
            and alias in accepted_alias
            for selected_model, accepted_alias in accepted_aliases
        ):
            continue
        if model not in found:
            found.append(model)
            accepted_aliases.append((model, alias))
    return tuple(found)


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
    compact = re.sub(r"\s+", "", query).casefold()
    preferred_sections = tuple(
        section
        for markers, sections in _QUERY_SECTION_MARKERS
        if any(marker in compact for marker in markers)
        for section in sections
    )
    section_match = bool(
        preferred_sections
        and any(marker.casefold() in heading.casefold() for marker in preferred_sections)
    )
    return (
        0.62 * hit.score
        + 0.18 * evidence_coverage
        + 0.08 * heading_coverage
        + (0.18 if section_match else 0.0)
    )
