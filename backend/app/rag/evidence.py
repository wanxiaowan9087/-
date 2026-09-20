from __future__ import annotations

import re
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass

from .lexical import tokenize
from .models import SearchHit


@dataclass(frozen=True)
class EvidenceAssessment:
    supported: bool
    knowledge_boundary_unsupported: bool
    query_coverage: float
    reason: str


_MODEL_ALIASES: tuple[tuple[str, ...], ...] = (
    ("s8-luna", "s8 luna", "皓月"),
    ("s8-air", "s8 air", "轻羽"),
    ("x9-obsidian", "x9 obsidian", "曜石"),
    ("x9-edge", "x9 edge", "曜石edge", "曜石 edge"),
    ("m6-terra", "m6 terra", "霞陶"),
    ("m6-mini", "m6 mini", "小径"),
)

_SNAPSHOT_BOUNDARIES = (
    re.compile(r"(今天|当前|现在|实时).{0,20}(门店|库存|现货)"),
    re.compile(r"(私人|个人).{0,10}(手机号|电话|联系方式)"),
    re.compile(r"(管理员|数据库|用户).{0,10}(密钥|密码|手机号清单)"),
    re.compile(r"(下一版|下次|未来|未发布|未公开|尚未公布).{0,25}(固件|功能|日期|时间|奖项)"),
    re.compile(r"(202[7-9]|20[3-9]\d).{0,15}(未公布|奖项|获奖)"),
    re.compile(r"(全球|每个地区|所有地区).{0,20}(保修|质保|政策)"),
    re.compile(r"(第三方).{0,12}(清洁液|消毒液|洗涤液).{0,12}(加|使用|支持|兼容)"),
    re.compile(r"(支持|兼容|使用|加入).{0,15}(第三方).{0,12}(清洁液|消毒液|洗涤液)"),
    re.compile(r"(支持|兼容|使用|加入).{0,15}(任意|任何).{0,8}(清洁液|消毒液|洗涤液)"),
    re.compile(r"(双十一|618|促销).{0,15}(最低|成交价|价格)"),
    re.compile(r"(保修|质保).{0,30}(还是|or).{0,20}(月|年)", re.IGNORECASE),
    re.compile(r"(是否|能否).{0,8}(保证|一定|必然).{0,24}(支持|能|可以|带|进入)"),
    re.compile(r"(是不是|是否).{0,10}(一定|必然).{0,24}(有|带|支持|具备)"),
    re.compile(r"(所以|因此).{0,8}(一定|必然).{0,20}(有|带|支持|具备)"),
    re.compile(r"(按照|参考|根据).{0,15}(其他|另一|别的).{0,12}(型号|机型).{0,20}(推断|推测|估算)"),
)

_PARAMETER_REQUIREMENTS: tuple[tuple[re.Pattern[str], re.Pattern[str]], ...] = (
    (
        re.compile(r"(电池容量|多少毫安时|多少mah)", re.IGNORECASE),
        re.compile(r"\d{3,6}\s*(?:mah|毫安时)", re.IGNORECASE),
    ),
    (
        re.compile(r"(机身高度|越障高度|精确高度|多少毫米)"),
        re.compile(r"\d+(?:\.\d+)?\s*(?:mm|毫米|厘米|cm)", re.IGNORECASE),
    ),
    (
        re.compile(r"(ip\s*防水等级|防水等级)", re.IGNORECASE),
        re.compile(r"\bip\s*\d{2}\b", re.IGNORECASE),
    ),
    (
        re.compile(r"(保修|质保).{0,12}(几年|多久|期限)"),
        re.compile(r"(?:保修|质保).{0,20}\d+(?:\.\d+)?\s*年"),
    ),
    (
        re.compile(r"(售后|客服).{0,8}(电话|手机号)"),
        re.compile(r"(?:400[-\s]?\d{3}[-\s]?\d{4}|1[3-9]\d{9})"),
    ),
    (
        re.compile(r"(价格|成交价|售价).{0,12}(多少|最低)"),
        re.compile(r"(?:¥|￥|人民币)?\s*\d{2,6}\s*元"),
    ),
    (
        re.compile(r"(检测证书|证书编号|认证编号)"),
        re.compile(r"(?:证书|认证).{0,16}[A-Z0-9][A-Z0-9-]{4,}", re.IGNORECASE),
    ),
)


def assess_evidence(
    query: str,
    hits: Sequence[SearchHit],
    *,
    confidence: float,
) -> EvidenceAssessment:
    """Apply a deterministic, zero-token evidence gate shared by runtime and evals."""

    normalized_query = _normalize(query)
    if not hits:
        return EvidenceAssessment(False, False, 0.0, "no_evidence")

    evidence_text = _normalize(
        "\n".join(
            " ".join(
                (
                    hit.chunk.title,
                    str(hit.chunk.metadata.get("model", "")),
                    str(hit.chunk.metadata.get("heading", "")),
                    hit.chunk.content,
                )
            )
            for hit in hits
        )
    )
    if any(pattern.search(normalized_query) for pattern in _SNAPSHOT_BOUNDARIES):
        return EvidenceAssessment(False, True, 0.0, "snapshot_boundary")

    requested_models = [
        aliases
        for aliases in _MODEL_ALIASES
        if any(alias in normalized_query for alias in aliases)
    ]
    if requested_models and not all(
        any(alias in evidence_text for alias in aliases) for aliases in requested_models
    ):
        return EvidenceAssessment(False, True, 0.0, "model_mismatch")

    parameter_evidence_text = evidence_text
    if requested_models:
        model_evidence = []
        for hit in hits:
            hit_text = _normalize(
                " ".join(
                    (
                        hit.chunk.title,
                        str(hit.chunk.metadata.get("model", "")),
                        str(hit.chunk.metadata.get("heading", "")),
                        hit.chunk.content,
                    )
                )
            )
            if any(
                any(alias in hit_text for alias in aliases)
                for aliases in requested_models
            ):
                model_evidence.append(hit_text)
        parameter_evidence_text = "\n".join(model_evidence)

    for query_pattern, evidence_pattern in _PARAMETER_REQUIREMENTS:
        if query_pattern.search(normalized_query) and not evidence_pattern.search(
            parameter_evidence_text
        ):
            return EvidenceAssessment(False, True, 0.0, "missing_explicit_parameter")

    query_terms = {term for term in tokenize(normalized_query) if len(term) > 1}
    evidence_terms = {term for term in tokenize(evidence_text) if len(term) > 1}
    coverage = len(query_terms & evidence_terms) / len(query_terms) if query_terms else 0.0
    top = hits[0]
    channel_confidence = max(
        min(
            1.0,
            max(
                value
                for value in (hit.vector_score, hit.keyword_score, 0.0)
                if value is not None
            )
            + (0.08 if hit.modalities == 2 else 0.0),
        )
        for hit in hits[:4]
    )
    reliable = bool(
        confidence >= 0.65
        or (channel_confidence >= 0.65 and coverage >= 0.08)
        or (top.modalities == 2 and confidence >= 0.50 and coverage >= 0.12)
        or (top.score >= 0.48 and coverage >= 0.20)
    )
    return EvidenceAssessment(
        supported=reliable,
        knowledge_boundary_unsupported=False,
        query_coverage=round(coverage, 6),
        reason="supported" if reliable else "weak_or_unrelated_evidence",
    )


def select_citation_hits(
    query: str,
    answer: str,
    hits: Sequence[SearchHit],
    *,
    limit: int = 3,
) -> tuple[SearchHit, ...]:
    """Choose evidence that supports the generated answer, not just rank position."""

    if limit < 1 or not hits:
        return ()
    query_terms = {term for term in tokenize(_normalize(query)) if len(term) > 1}
    answer_terms = {term for term in tokenize(_normalize(answer)) if len(term) > 1}
    ranked: list[tuple[float, float, int, SearchHit]] = []
    for index, hit in enumerate(hits):
        evidence_terms = {
            term
            for term in tokenize(
                _normalize(
                    " ".join(
                        (
                            hit.chunk.title,
                            str(hit.chunk.metadata.get("model", "")),
                            str(hit.chunk.metadata.get("heading", "")),
                            hit.chunk.content,
                        )
                    )
                )
            )
            if len(term) > 1
        }
        answer_coverage = (
            len(answer_terms & evidence_terms) / len(answer_terms) if answer_terms else 0.0
        )
        query_coverage = (
            len(query_terms & evidence_terms) / len(query_terms) if query_terms else 0.0
        )
        relevance = 0.55 * hit.score + 0.30 * answer_coverage + 0.15 * query_coverage
        ranked.append((relevance, answer_coverage, index, hit))
    ranked.sort(key=lambda item: (-item[0], -item[1], item[2]))
    top_score = ranked[0][0]
    selected = [
        hit
        for score, answer_coverage, _, hit in ranked
        if score >= top_score - 0.12 and (answer_coverage > 0.0 or not answer_terms)
    ][:limit]
    return tuple(selected or [ranked[0][3]])


def select_query_evidence_hits(
    query: str,
    hits: Sequence[SearchHit],
    *,
    limit: int = 3,
) -> tuple[SearchHit, ...]:
    """Select citations before generation when no answer text exists yet."""

    return select_citation_hits(query, "", hits, limit=limit)


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return re.sub(r"\s+", " ", normalized).strip()
