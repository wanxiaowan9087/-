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
        # Keep both the user's wording and the deterministic normalization.
        # The two retrieval branches run concurrently, so colloquial recall
        # improves without adding an LLM intent call or serial model latency.
        return QueryPlan(
            normalized,
            (normalized, canonical),
            "deterministic-dual-query-normalization",
        )


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
    chinese_area = {
        "二十": "20", "三十": "30", "四十": "40", "五十": "50",
        "六十": "60", "七十": "70", "八十": "80", "九十": "90",
    }
    for source, target in chinese_area.items():
        canonical = re.sub(
            rf"{source}\s*(?:来|多)?\s*(?:平|平米|平方米)",
            f"{target} 平方米",
            canonical,
        )
    # Extract explicit constraints from natural Chinese phrasing.  These are
    # lexical hints for hybrid retrieval, not an intent/model decision.
    if re.search(r"\d+(?:\.\d+)?\s*(?:平米|平方米|㎡|平)", canonical):
        canonical = re.sub(
            r"(\d+(?:\.\d+)?)\s*(?:平米|平方米|㎡|平)",
            r"小户型 \1 平方米",
            canonical,
            count=1,
        )
    if any(marker in canonical for marker in ("小家", "小房子", "房子不大", "房间不大")):
        canonical = canonical.replace("小家", "小户型").replace("小房子", "小户型")
        canonical = canonical.replace("房子不大", "小户型").replace("房间不大", "小户型")
    if re.search(r"预算\s*(?:大概|差不多)?\s*\d", canonical):
        canonical = re.sub(r"预算\s*(大概|差不多)\s*", "预算 ", canonical)
    selection_markers = (
        "买哪个", "买哪款", "该买什么", "咋选", "怎么选", "选哪款", "挑一款", "挑个"
    )
    if any(marker in canonical for marker in selection_markers):
        canonical += " 推荐 型号 选择"
    if any(marker in canonical for marker in ("猫毛", "猫咪", "养猫", "养狗", "宠物")):
        canonical += " 宠物家庭"
    if "木地板" in canonical:
        canonical += " 木地板"
    canonical = re.sub(r"(推荐|宠物家庭|木地板)(?:\s+\1)+", r"\1", canonical)
    # A preference-only follow-up such as “我喜欢白色的” relies on the
    # preceding chat turn for its subject. Add only known catalog terms so
    # retrieval can find robot products; no model is inferred here.
    if any(
        color in canonical
        for color in (
            "白色", "白的", "月白", "云白", "黑色", "黑的", "曜石黑",
            "灰色", "灰的", "岩灰",
            "绿色", "绿的", "草木绿",
        )
    ) and not any(
        marker in canonical for marker in ("机器人", "扫地", "扫拖", "型号", "机型")
    ):
        canonical = f"{canonical} 扫地机器人 型号 推荐"
    return _normalize(canonical)[:240]
