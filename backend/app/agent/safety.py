from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Sequence

from .contracts import ReviewReason, ToolExecution, ToolOutcome


class PolicyAction(StrEnum):
    PUBLISH = "publish"
    REFUSE = "refuse"
    WITHHOLD_FOR_REVIEW = "withhold_for_review"


@dataclass(frozen=True)
class InjectionSignal:
    source: str
    pattern: str


class PromptInjectionDetector:
    """Conservative detector; retrieved text remains data even when unflagged."""

    _PATTERNS = (
        re.compile(
            r"(?i)\b(ignore|disregard|override|forget)\b.{0,40}"
            r"\b(previous|system|developer|instructions?|prompt)\b"
        ),
        re.compile(
            r"(?i)\b(reveal|print|show|leak)\b.{0,30}"
            r"\b(system prompt|developer message|secret|api key)\b"
        ),
        re.compile(r"忽略.{0,20}(之前|以上|系统|开发者).{0,20}(指令|提示词)"),
        re.compile(r"(泄露|输出|展示).{0,20}(系统提示词|内部策略|密钥)"),
        re.compile(r"(?i)<\s*(system|assistant|developer)\s*>"),
        re.compile(r"(?i)\btool\s*:\s*[a-z_][a-z0-9_]*\s*\("),
    )

    def scan(self, text: str, *, source: str) -> tuple[InjectionSignal, ...]:
        return tuple(
            InjectionSignal(source=source, pattern=pattern.pattern[:120])
            for pattern in self._PATTERNS
            if pattern.search(text)
        )


@dataclass(frozen=True)
class PolicyInput:
    confidence: float
    has_evidence: bool
    citations_valid: bool
    high_risk: bool = False
    safety_or_repair_claim: bool = False
    warranty_claim: bool = False
    missing_required_fields: bool = False
    conflicting_sources: bool = False
    prompt_injection: bool = False
    unauthorized_request: bool = False
    user_requested_human: bool = False
    policy_rule_hit: bool = False
    tool_executions: tuple[ToolExecution, ...] = ()


@dataclass(frozen=True)
class PolicyDecision:
    action: PolicyAction
    reasons: tuple[ReviewReason, ...]
    confidence: float
    public_content: str | None = None


class DeterministicReviewPolicy:
    def __init__(self, *, confidence_threshold: float = 0.65) -> None:
        if not 0.0 <= confidence_threshold <= 1.0:
            raise ValueError("confidence threshold must be between 0 and 1")
        self.confidence_threshold = confidence_threshold

    def decide(self, policy_input: PolicyInput) -> PolicyDecision:
        reasons: list[ReviewReason] = []

        # Frozen priority 1: do not generate a publishable conclusion.
        if (
            policy_input.prompt_injection
            or policy_input.unauthorized_request
            or policy_input.high_risk
        ):
            if policy_input.prompt_injection:
                reasons.append(ReviewReason.PROMPT_INJECTION)
            if policy_input.high_risk or policy_input.unauthorized_request:
                reasons.append(ReviewReason.HIGH_RISK)
            return self._review(reasons, policy_input.confidence)

        # Frozen priority 2: sensitive claims require reliable evidence.
        sensitive_claim = (
            policy_input.safety_or_repair_claim or policy_input.warranty_claim
        )
        if (
            sensitive_claim
            and (
                not policy_input.has_evidence
                or not policy_input.citations_valid
                or policy_input.missing_required_fields
            )
        ) or policy_input.conflicting_sources:
            if policy_input.conflicting_sources:
                reasons.append(ReviewReason.CONFLICTING_SOURCES)
            reasons.append(ReviewReason.POLICY_RULE)
            return self._review(reasons, policy_input.confidence)

        # Frozen priority 3: exhausted critical writes never become success.
        critical_failure = any(
            execution.critical
            and execution.outcome
            in {
                ToolOutcome.FAILED,
                ToolOutcome.TIMEOUT,
                ToolOutcome.CANCELLED,
            }
            for execution in policy_input.tool_executions
        )
        if critical_failure:
            return self._review(
                [ReviewReason.CRITICAL_TOOL_FAILURE],
                policy_input.confidence,
            )

        if policy_input.user_requested_human:
            return self._review(
                [ReviewReason.POLICY_RULE], policy_input.confidence
            )

        # Frozen priority 4: an ordinary unsupported question gets one prompt.
        if not policy_input.has_evidence:
            return PolicyDecision(
                action=PolicyAction.REFUSE,
                reasons=(),
                confidence=policy_input.confidence,
                public_content=(
                    "现有资料不足以支持具体结论。请补充扫地机器人的准确型号。"
                ),
            )

        if (
            policy_input.confidence < self.confidence_threshold
            or not policy_input.citations_valid
            or policy_input.missing_required_fields
        ):
            reasons.append(ReviewReason.LOW_CONFIDENCE)
            if policy_input.policy_rule_hit:
                reasons.append(ReviewReason.POLICY_RULE)
            return self._review(reasons, policy_input.confidence)

        if policy_input.policy_rule_hit:
            return self._review(
                [ReviewReason.POLICY_RULE], policy_input.confidence
            )

        return PolicyDecision(
            action=PolicyAction.PUBLISH,
            reasons=(),
            confidence=policy_input.confidence,
        )

    def _review(
        self, reasons: Sequence[ReviewReason], confidence: float
    ) -> PolicyDecision:
        return PolicyDecision(
            action=PolicyAction.WITHHOLD_FOR_REVIEW,
            reasons=tuple(dict.fromkeys(reasons)),
            confidence=confidence,
        )


@dataclass(frozen=True)
class DraftRelease:
    public_content: str
    candidate_content: str | None
    withheld: bool


class DraftGate:
    """The only module allowed to move a candidate into public content."""

    def release(
        self, candidate_content: str, decision: PolicyDecision
    ) -> DraftRelease:
        if decision.action is PolicyAction.WITHHOLD_FOR_REVIEW:
            return DraftRelease(
                public_content="",
                candidate_content=candidate_content,
                withheld=True,
            )
        if decision.action is PolicyAction.REFUSE:
            return DraftRelease(
                public_content=decision.public_content
                or "现有资料不足，暂时无法给出可靠结论。",
                candidate_content=None,
                withheld=False,
            )
        return DraftRelease(
            public_content=candidate_content,
            candidate_content=None,
            withheld=False,
        )


_HIGH_RISK_PATTERNS = (
    re.compile(r"(拆机|短接|绕过.{0,8}保护|电池.{0,8}(刺穿|拆解)|明火)"),
    re.compile(r"(?i)\b(disable|bypass)\b.{0,20}\b(safety|sensor|lock)\b"),
    re.compile(r"(删除全部|永久删除|重置账号|转账|付款)"),
    re.compile(r"(游泳池|水下|浸水|进水).{0,12}(清洁|运行|开启)?"),
)
_SENSITIVE_CLAIM_PATTERNS = (
    re.compile(r"(漏电|起火|电池鼓包|异味|冒烟|维修|故障码)"),
    re.compile(r"(保修|质保|换新|退货政策)"),
)


def classify_user_risk(text: str) -> tuple[bool, bool, bool]:
    high_risk = any(pattern.search(text) for pattern in _HIGH_RISK_PATTERNS)
    sensitive = any(
        pattern.search(text) for pattern in _SENSITIVE_CLAIM_PATTERNS
    )
    warranty = bool(re.search(r"(保修|质保|换新|退货政策)", text))
    return high_risk, sensitive, warranty


def required_fields_missing(text: str) -> bool:
    if re.search(r"(报错|故障码|错误码|换哪个零件)", text):
        return re.search(r"(?i)\b[A-Z]\d{1,5}\b", text) is None
    return False
