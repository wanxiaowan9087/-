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


@dataclass(frozen=True)
class ClaimEvidence:
    """Evidence selected for one atomic answer claim.

    Citations used to be selected against the whole answer, which allowed a
    chunk about one model to be attached to a different model's sentence.
    Keeping the claim boundary here lets the runtime build a conservative
    union of citations without treating the complete answer as one claim.
    """

    claim: str
    hits: tuple[SearchHit, ...]


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
    requested_models = set(_models_in_text(query))
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
        declared_models = set(
            _models_in_text(str(hit.chunk.metadata.get("model", "")))
        )
        model_match = bool(requested_models & declared_models)
        model_conflict = bool(requested_models and declared_models and not model_match)
        # Section intent is a stronger signal than generic lexical overlap for
        # product manuals.  A model's identity/overview chunk often contains
        # the same words as a feature question, but it must not outrank the
        # model's feature section merely because its fused score is higher.
        section_bonus = _section_intent_score(query, hit)
        relevance = (
            0.30 * hit.score
            + 0.20 * answer_coverage
            + 0.10 * query_coverage
            + 0.40 * section_bonus
            + (0.22 if model_match else 0.0)
            - (0.22 if model_conflict else 0.0)
        )
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


_SECTION_INTENTS: tuple[tuple[tuple[str, ...], tuple[str, ...]], ...] = (
    (("功能", "特点", "配置", "具备", "能力"), ("组件与功能", "功能", "特点")),
    (("电池", "充电", "电量", "续航", "长期不用", "充电异常"), ("电池与充电", "充电")),
    (("保养", "维护", "清理", "清洗", "耗材", "更换"), ("维护建议", "保养", "耗材")),
    (("安全", "风险", "注意", "禁止", "发热", "漏水"), ("安全使用", "安全", "风险")),
    (("故障", "报错", "异常", "无法", "怎么办"), ("常见问题", "故障", "排除")),
    (("首次", "设置", "安装", "建图", "启动"), ("首次使用", "推荐使用方式", "设置")),
    (("适合", "场景", "家庭", "定位", "擅长"), ("型号定位", "适用场景", "定位")),
)


def _section_intent_score(query: str, hit: SearchHit) -> float:
    """Return a bounded section-match score for citation selection.

    This remains deterministic and local.  It only boosts a heading that
    explicitly matches the user's intent; it never makes an unrelated chunk
    eligible on its own.
    """

    normalized = _normalize(query)
    identity = _normalize(
        " ".join(
            (
                str(hit.chunk.metadata.get("heading", "")),
                hit.chunk.location.section or "",
            )
        )
    )
    if not identity:
        return 0.0
    for markers, sections in _SECTION_INTENTS:
        if any(marker in normalized for marker in markers):
            if any(section in identity for section in sections):
                return 1.0
            return 0.0
    return 0.0


def select_claim_evidence(
    query: str,
    answer: str,
    hits: Sequence[SearchHit],
    *,
    limit: int = 3,
) -> tuple[ClaimEvidence, ...]:
    """Bind citations to individual answer claims conservatively.

    This is intentionally deterministic and local.  It is not an entailment
    model: it prevents the most damaging class of errors first by enforcing
    model identity and exact numeric/value presence, then uses lexical overlap
    plus the retrieval score to choose the supporting chunk for each claim.
    Claims without a sufficiently supporting chunk are returned with no hits;
    the caller's existing release policy can then refuse the answer instead
    of attaching an unrelated citation.
    """

    if limit < 1 or not hits:
        return ()
    claims = _split_claims(answer)
    if not claims:
        return ()
    output: list[ClaimEvidence] = []
    for claim in claims:
        claim_models = _models_in_text(claim)
        scored: list[tuple[float, int, SearchHit]] = []
        for index, hit in enumerate(hits):
            score = _claim_support_score(query, claim, hit, claim_models)
            if score is not None:
                scored.append((score, index, hit))
        scored.sort(key=lambda item: (-item[0], item[1]))
        if not scored:
            output.append(ClaimEvidence(claim=claim, hits=()))
            continue
        # A comparison sentence can name multiple products.  Keep one best
        # chunk per named model; ordinary claims keep only their best chunk.
        selected: list[SearchHit] = []
        if len(claim_models) > 1:
            for model in claim_models:
                matching = [item for item in scored if model in _models_in_text(_hit_text(item[2]))]
                if matching:
                    selected.append(matching[0][2])
        if not selected:
            selected = [scored[0][2]]
        output.append(ClaimEvidence(claim=claim, hits=tuple(selected[:limit])))
    return tuple(output)


def claim_requires_binding(claim: str) -> bool:
    """Return whether a claim contains a model or parameter boundary."""

    return bool(_models_in_text(claim) or _number_tokens(claim))


def _split_claims(answer: str) -> tuple[str, ...]:
    claims: list[str] = []
    for line in answer.splitlines():
        cleaned = re.sub(r"^\s*(?:[-*•]|\d+[.、)])\s*", "", line).strip()
        if not cleaned:
            continue
        for sentence in re.split(r"(?<=[。！？!?；;])\s*", cleaned):
            sentence = sentence.strip()
            if sentence:
                claims.append(sentence)
    return tuple(claims)


def _claim_support_score(
    query: str,
    claim: str,
    hit: SearchHit,
    claim_models: tuple[str, ...],
) -> float | None:
    evidence_text = _hit_text(hit)
    evidence_models = _models_in_text(evidence_text)
    declared_models = _models_in_text(str(hit.chunk.metadata.get("model", "")))
    if claim_models and declared_models and not set(claim_models) & set(declared_models):
        return None
    if claim_models and any(model not in evidence_models for model in claim_models):
        return None
    claim_numbers = _number_tokens(claim)
    evidence_compact = re.sub(r"\s+", "", _normalize(evidence_text))
    if claim_numbers and not all(number in evidence_compact for number in claim_numbers):
        return None
    claim_terms = {term for term in tokenize(_normalize(claim)) if len(term) > 1}
    evidence_terms = {term for term in tokenize(_normalize(evidence_text)) if len(term) > 1}
    if not claim_terms:
        return None
    overlap = len(claim_terms & evidence_terms) / len(claim_terms)
    query_terms = {term for term in tokenize(_normalize(query)) if len(term) > 1}
    query_overlap = (
        len(query_terms & evidence_terms) / len(query_terms) if query_terms else 0.0
    )
    model_bonus = 0.16 if claim_models else 0.0
    score = 0.58 * overlap + 0.22 * query_overlap + 0.20 * hit.score + model_bonus
    return score if overlap >= 0.20 else None


def _hit_text(hit: SearchHit) -> str:
    return " ".join(
        (
            hit.chunk.title,
            str(hit.chunk.metadata.get("model", "")),
            str(hit.chunk.metadata.get("product_id", "")),
            str(hit.chunk.metadata.get("heading", "")),
            hit.chunk.content,
        )
    )


def _models_in_text(value: str) -> tuple[str, ...]:
    normalized = _normalize(value).replace("-", "").replace("_", "")
    found: list[str] = []
    for aliases in _MODEL_ALIASES:
        if any(alias.replace("-", "").replace("_", "") in normalized for alias in aliases):
            found.append(aliases[0])
    return tuple(found)


def _number_tokens(value: str) -> tuple[str, ...]:
    normalized = _normalize(value)
    # Only enforce exact numeric support for parameter-like claims.  Ordinary
    # scene descriptions may mention an area or count that is not a product
    # specification and should not make an otherwise grounded answer fail.
    if not any(
        marker in normalized
        for marker in (
            "电池",
            "容量",
            "高度",
            "越障",
            "价格",
            "售价",
            "参考价",
            "保修",
            "质保",
            "防水",
            "mah",
            "毫米",
        )
    ):
        return ()
    return tuple(
        token.casefold().replace(" ", "")
        for token in re.findall(
            r"\d+(?:\.\d+)?\s*(?:mah|毫安时|毫米|mm|厘米|cm|元|年|个月|分钟|小时|%)?",
            normalized,
            flags=re.IGNORECASE,
        )
    )


def _normalize(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).casefold()
    normalized = "".join(
        character
        for character in normalized
        if unicodedata.category(character) != "Cf"
    )
    return re.sub(r"\s+", " ", normalized).strip()
