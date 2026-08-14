from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5

from ..rag.citations import CitationService
from ..rag.models import Chunk, Citation, DocumentType, RetrievalResult, SearchHit
from ..rag.ports import RetrieverPort
from ..rag.retrieval import RetrievalUnavailable
from ..rag.security import (
    render_untrusted_context,
    scan_retrieved_content,
)
from .contracts import (
    AgentModelRequest,
    AgentRequest,
    AgentRunResult,
    ConversationMessage,
    ConversationMode,
    ErrorCode,
    MemoryContext,
    ModelDraft,
    RunStatus,
    StepStatus,
    StepType,
    ToolOutcome,
    utc_now,
)
from .memory import NullMemoryCoordinator
from .ports import ModelTimeout, ModelUnavailable, ReActEnginePort
from .safety import (
    DeterministicReviewPolicy,
    DraftGate,
    PolicyAction,
    PolicyDecision,
    PolicyInput,
    PromptInjectionDetector,
    classify_user_risk,
    required_fields_missing,
)
from .tooling import CancellationToken
from .customer_tools import reset_request_context, set_request_context
from .tracing import RunStateMachine, TraceRecorder

logger = logging.getLogger(__name__)

_LABELED_LOCAL_PATH = re.compile(
    r"(?:文件路径|本地路径|file path|source path)\s*[:：]\s*`?file://[^\s`)\]）]+`?",
    flags=re.IGNORECASE,
)
_RAW_LOCAL_PATH = re.compile(r"`?file://[^\s`)\]）]+`?", flags=re.IGNORECASE)
_DOCUMENT_ID = re.compile(
    r"(?:文档\s*ID|document\s*ID)\s*[:：]\s*`?[0-9a-f-]{8,}`?",
    flags=re.IGNORECASE,
)
_DOCUMENT_VERSION = re.compile(
    r"(?:文档\s*)?(?:版本|version)\s*[:：]\s*`?[A-Za-z0-9._-]+`?",
    flags=re.IGNORECASE,
)


def redact_local_source_paths(content: str) -> str:
    """Keep citations traceable internally without exposing local paths in model text."""
    redacted = _LABELED_LOCAL_PATH.sub("", content)
    redacted = _RAW_LOCAL_PATH.sub("受控知识库资料", redacted)
    redacted = _DOCUMENT_ID.sub("", redacted)
    redacted = _DOCUMENT_VERSION.sub("", redacted)
    redacted = re.sub(r"[（(]\s*[，,;；\s]*[）)]", "", redacted)
    return re.sub(r"\s{2,}", " ", redacted).strip()


def format_user_visible_answer(content: str) -> str:
    """Normalize compact model lists so each recommendation remains scannable."""
    formatted = re.sub(
        r"\s+-\s+(?=(?:\*\*)?[A-Z][A-Z0-9-]{1,})",
        "\n\n- ",
        content,
    )
    return re.sub(r"\*\*(.+?)\*\*", r"\1", formatted)


IDENTITY_INTENT_PATTERNS = (
    "你是谁",
    "你能做什么",
    "你可以做什么",
    "介绍一下你",
    "怎么上传知识",
    "如何上传知识",
    "上传新文件",
    "上传资料",
    "知识库怎么更新",
)

MODEL_IDENTITY_PATTERNS = (
    "\u4ec0\u4e48\u6a21\u578b", "\u4ec0\u4e48\u5927\u6a21\u578b",
    "\u4f60\u662f\u6a21\u578b\u5417", "\u4f60\u662f\u4e0d\u662f\u5c0f\u667a",
    "\u4f60\u662f\u5c0f\u667a\u5417", "qwen", "\u901a\u4e49", "\u5343\u95ee",
    "\u5e95\u5c42\u6a21\u578b", "\u6a21\u578b\u63d0\u4f9b\u5546",
)

PROFILE_INTENT_PATTERNS = ("我的个人信息", "我的资料", "用户信息", "总结我的使用习惯", "我的使用习惯", "我的偏好")


def classify_meaningless_input(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    if not compact:
        return True
    # Unicode \w is not reliable across clients with mixed encodings. Treat
    # input as meaningful when it contains a letter, CJK character, or digit;
    # punctuation-only and numeric-only messages are otherwise ignored.
    if not re.search(r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", compact):
        return True
    return compact in {"嗯", "嗯嗯", "好的", "好", "ok", "okay", "收到", "谢谢"}


def answer_profile_intent(user_text: str, context: MemoryContext) -> str | None:
    compact = re.sub(r"\s+", "", user_text)
    if not any(pattern in compact for pattern in PROFILE_INTENT_PATTERNS):
        return None
    facts = [fact.content for fact in context.facts]
    preferences = [fact for fact in facts if "偏好" in fact or "喜欢" in fact or "请用" in fact]
    lines = [f"会话摘要：{context.summary}" if context.summary else "会话摘要：暂无已生成摘要。"]
    lines.append("已确认的个人记录：" + ("；".join(facts) if facts else "暂无"))
    lines.append("使用偏好：" + ("；".join(preferences) if preferences else "暂无稳定偏好记录"))
    return "\n".join(lines)


def answer_identity_intent(user_text: str) -> str | None:
    compact = re.sub(r"\s+", "", user_text.lower())
    if any(pattern in compact for pattern in MODEL_IDENTITY_PATTERNS):
        return (
            "我是小智，ZENMOP 的智能客服助手。我会基于受控知识库协助处理机器人选购、"
            "使用、维护、故障排查和已授权的使用报告问题。"
        )
    if not any(pattern in compact for pattern in IDENTITY_INTENT_PATTERNS):
        return None
    return (
        "我是小智智能客服，一个面向扫地/扫拖机器人场景的受控知识库 Agent。"
        "我会优先基于已接入的产品资料回答选购、使用、维护和故障排查问题；"
        "如果资料不足，我会说明无法确认，避免编造。资料更新请联系管理员处理。"
    )


class MemoryRuntimePort(Protocol):
    async def build_context(self, session_id: str, subject_id: str) -> MemoryContext: ...

    async def extract_best_effort(
        self, subject_id: str, source: ConversationMessage
    ) -> str | None: ...


@dataclass(frozen=True)
class RuntimeConfig:
    confidence_threshold: float = 0.65
    citation_limit: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if self.citation_limit < 1:
            raise ValueError("citation_limit must be positive")


class AgentRuntime:
    """Deep runtime interface around ReAct, retrieval, memory, and policy."""

    def __init__(
        self,
        *,
        react_engine: ReActEnginePort,
        retriever: RetrieverPort,
        memory: MemoryRuntimePort | None = None,
        policy: DeterministicReviewPolicy | None = None,
        citation_service: CitationService | None = None,
        injection_detector: PromptInjectionDetector | None = None,
        draft_gate: DraftGate | None = None,
        config: RuntimeConfig = RuntimeConfig(),
    ) -> None:
        self._react_engine = react_engine
        self._retriever = retriever
        self._memory = memory or NullMemoryCoordinator()
        self._policy = policy or DeterministicReviewPolicy(
            confidence_threshold=config.confidence_threshold
        )
        self._citations = citation_service or CitationService()
        self._detector = injection_detector or PromptInjectionDetector()
        self._draft_gate = draft_gate or DraftGate()
        self._config = config

    async def execute(
        self,
        request: AgentRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        token = cancellation or CancellationToken()
        run_id = request.run_id or str(uuid4())
        state = RunStateMachine()
        trace = TraceRecorder()
        state.transition(RunStatus.RUNNING)
        degraded: list[str] = []

        try:
            token.checkpoint()
            context_token = set_request_context(request.subject_id, request.session_id)
            identity_answer = answer_identity_intent(request.user_text)
            if identity_answer is not None:
                step = trace.start(
                    StepType.POLICY,
                    "answering supported assistant identity and usage intent",
                )
                state.transition(RunStatus.COMPLETED)
                trace.finish(
                    step,
                    StepStatus.SUCCEEDED,
                    "deterministic identity answer selected before retrieval",
                )
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=identity_answer,
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=(),
                    memory_warning=memory_warning,
                    model_name="deterministic-identity",
                    retrieval_strategy="identity-intent",
                )
            memory_context = await self._prepare_memory(request, trace, degraded)
            profile_answer = answer_profile_intent(request.user_text, memory_context)
            if profile_answer is not None:
                state.transition(RunStatus.COMPLETED)
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id, status=state.status, public_content=profile_answer,
                    candidate_content=None, citations=(), trace=trace.snapshot(),
                    confidence=1.0, confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=tuple(dict.fromkeys(degraded)), memory_warning=memory_warning,
                    model_name="deterministic-user-context", retrieval_strategy="memory-context",
                )
            if classify_meaningless_input(request.user_text):
                state.transition(RunStatus.COMPLETED)
                message = "请继续描述具体需求，例如机器人型号、故障现象、使用场景或报告月份。"
                if memory_context.window or memory_context.summary:
                    message = "我还在当前会话中。请补充完整问题，或继续上一个问题的具体细节。"
                return AgentRunResult(
                    run_id=run_id, status=state.status, public_content=message,
                    candidate_content=None, citations=(), trace=trace.snapshot(),
                    confidence=1.0, confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=tuple(dict.fromkeys(degraded)),
                    model_name="deterministic-input-guard", retrieval_strategy="input-guard",
                )
            if request.report_tool_executions:
                self._record_tool_executions(trace, request.report_tool_executions)
            token.checkpoint()
            retrieval = await self._retrieve(
                request.user_text, trace, degraded
            )
            if request.mode is ConversationMode.REPORT and request.report_context:
                retrieval = _with_report_evidence(retrieval, request)
            token.checkpoint()

            user_injection = self._detector.scan(
                request.user_text, source="user"
            )
            retrieved_injection = scan_retrieved_content(
                retrieval.hits, self._detector
            )
            high_risk, sensitive_claim, warranty_claim = classify_user_risk(
                request.user_text
            )
            missing_required_fields = required_fields_missing(
                request.user_text
            )
            preliminary_citations = self._citations.build(
                retrieval.hits, limit=self._config.citation_limit
            )
            preliminary_validation = self._citations.validate(
                preliminary_citations,
                [hit.chunk for hit in retrieval.hits],
            )
            preliminary_decision = self._policy.decide(
                PolicyInput(
                    confidence=retrieval.confidence,
                    has_evidence=retrieval.has_evidence,
                    citations_valid=(
                        bool(preliminary_citations)
                        and preliminary_validation.valid
                    ),
                    high_risk=high_risk,
                    safety_or_repair_claim=sensitive_claim,
                    warranty_claim=warranty_claim,
                    missing_required_fields=missing_required_fields,
                    conflicting_sources=retrieval.conflicting_sources,
                    prompt_injection=bool(
                        user_injection or retrieved_injection
                    ),
                    user_requested_human=request.user_requested_human,
                )
            )
            if preliminary_decision.action is not PolicyAction.PUBLISH:
                candidate = (
                    "检测到需要人工确认的请求，未生成可发布结论。"
                    if preliminary_decision.action
                    is PolicyAction.WITHHOLD_FOR_REVIEW
                    else ""
                )
                return await self._finalize_without_model(
                    request=request,
                    run_id=run_id,
                    state=state,
                    trace=trace,
                    retrieval=retrieval,
                    decision=preliminary_decision,
                    candidate=candidate,
                    citations=preliminary_citations,
                    degraded=degraded,
                )

            generation_step = trace.start(
                StepType.GENERATION, "generating answer with ReAct engine"
            )
            try:
                draft = await self._react_engine.generate(
                    AgentModelRequest(
                        run_id=run_id,
                        mode=request.mode,
                        user_text=request.user_text,
                        rendered_context=render_untrusted_context(
                            retrieval.hits
                        ),
                        short_term_messages=memory_context.window,
                        conversation_summary=memory_context.summary,
                        long_term_facts=memory_context.facts,
                        report_scope=request.report_scope,
                        report_context=request.report_context,
                    ),
                    token,
                )
                token.checkpoint()
                trace.finish(
                    generation_step,
                    StepStatus.SUCCEEDED,
                    "ReAct engine produced a candidate draft",
                )
            except ModelTimeout:
                trace.finish(
                    generation_step,
                    StepStatus.TIMEOUT,
                    "model timed out",
                    ErrorCode.MODEL_TIMEOUT,
                )
                state.transition(RunStatus.FAILED)
                return self._failure_result(
                    run_id,
                    state,
                    trace,
                    retrieval,
                    degraded,
                    ErrorCode.MODEL_TIMEOUT,
                )
            except ModelUnavailable:
                trace.finish(
                    generation_step,
                    StepStatus.FAILED,
                    "model unavailable",
                    ErrorCode.MODEL_UNAVAILABLE,
                )
                state.transition(RunStatus.FAILED)
                return self._failure_result(
                    run_id,
                    state,
                    trace,
                    retrieval,
                    degraded,
                    ErrorCode.MODEL_UNAVAILABLE,
                )

            draft = replace(
                draft,
                content=format_user_visible_answer(redact_local_source_paths(draft.content)),
            )
            self._record_tool_steps(trace, draft)
            citations = self._citations.build(
                retrieval.hits,
                selected_chunk_ids=draft.cited_chunk_ids,
                limit=self._config.citation_limit,
            )
            validation = self._citations.validate(
                citations, [hit.chunk for hit in retrieval.hits]
            )
            policy_step = trace.start(
                StepType.POLICY, "checking deterministic release policy"
            )
            final_decision = self._policy.decide(
                PolicyInput(
                    confidence=retrieval.confidence,
                    has_evidence=retrieval.has_evidence,
                    citations_valid=bool(citations) and validation.valid,
                    high_risk=high_risk,
                    safety_or_repair_claim=sensitive_claim,
                    warranty_claim=warranty_claim,
                    missing_required_fields=missing_required_fields,
                    conflicting_sources=retrieval.conflicting_sources,
                    prompt_injection=False,
                    user_requested_human=request.user_requested_human,
                    tool_executions=draft.tool_executions,
                )
            )
            release = self._draft_gate.release(
                draft.content, final_decision
            )
            if final_decision.action is PolicyAction.WITHHOLD_FOR_REVIEW:
                state.transition(RunStatus.NEEDS_REVIEW)
                trace.finish(
                    policy_step,
                    StepStatus.WAITING,
                    "candidate withheld for reviewer decision",
                    ErrorCode.REVIEW_REQUIRED,
                )
            else:
                state.transition(RunStatus.COMPLETED)
                trace.finish(
                    policy_step,
                    StepStatus.SUCCEEDED,
                    "candidate passed deterministic release policy",
                )
            memory_warning = await self._extract_memory(request)
            review_id = str(uuid4()) if release.withheld else None
            return AgentRunResult(
                run_id=run_id,
                status=state.status,
                public_content=release.public_content,
                candidate_content=release.candidate_content,
                citations=citations,
                trace=trace.snapshot(),
                confidence=retrieval.confidence,
                confidence_threshold=self._config.confidence_threshold,
                review_reasons=final_decision.reasons,
                degraded_dependencies=tuple(dict.fromkeys(degraded)),
                memory_warning=memory_warning,
                error_code=(
                    ErrorCode.REVIEW_REQUIRED
                    if release.withheld
                    else None
                ),
                model_name=draft.model_name,
                retrieval_strategy=retrieval.strategy,
                review_id=review_id,
            )
        except asyncio.CancelledError:
            trace.cancel_open_steps()
            if state.status is RunStatus.RUNNING:
                state.transition(RunStatus.CANCELLED)
            return AgentRunResult(
                run_id=run_id,
                status=RunStatus.CANCELLED,
                public_content="",
                candidate_content=None,
                citations=(),
                trace=trace.snapshot(),
                confidence=0.0,
                confidence_threshold=self._config.confidence_threshold,
                degraded_dependencies=tuple(dict.fromkeys(degraded)),
                error_code=ErrorCode.CANCELLED,
            )
        except Exception:
            logger.exception("agent runtime failed", extra={"error_code": "INTERNAL_ERROR"})
            trace.cancel_open_steps()
            if state.status is RunStatus.RUNNING:
                state.transition(RunStatus.FAILED)
            return AgentRunResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                public_content="",
                candidate_content=None,
                citations=(),
                trace=trace.snapshot(),
                confidence=0.0,
                confidence_threshold=self._config.confidence_threshold,
                degraded_dependencies=tuple(dict.fromkeys(degraded)),
                error_code=ErrorCode.INTERNAL_ERROR,
            )
        finally:
            if 'context_token' in locals():
                reset_request_context(context_token)

    async def _prepare_memory(
        self,
        request: AgentRequest,
        trace: TraceRecorder,
        degraded: list[str],
    ) -> MemoryContext:
        step = trace.start(
            StepType.CONTEXT, "preparing window, summary, and sourced facts"
        )
        try:
            context = await self._memory.build_context(
                request.session_id, request.subject_id
            )
            trace.finish(
                step,
                StepStatus.SUCCEEDED,
                (
                    f"context prepared: window={len(context.window)}, "
                    f"facts={len(context.facts)}, "
                    f"summary={'yes' if context.summary else 'no'}"
                ),
            )
            return context
        except Exception:
            logger.warning("memory context degraded", exc_info=True, extra={"component": "memory"})
            degraded.append("memory")
            trace.finish(
                step,
                StepStatus.FAILED,
                "memory unavailable; continued with empty context",
            )
            return await NullMemoryCoordinator().build_context(
                request.session_id, request.subject_id
            )

    async def _retrieve(
        self,
        query: str,
        trace: TraceRecorder,
        degraded: list[str],
    ) -> RetrievalResult:
        step = trace.start(
            StepType.RETRIEVAL,
            "running vector and keyword retrieval with fusion",
        )
        try:
            result = await self._retriever.retrieve(query)
            degraded.extend(result.degraded_dependencies)
            trace.finish(
                step,
                StepStatus.SUCCEEDED,
                (
                    f"retrieval completed: hits={len(result.hits)}, "
                    f"confidence={result.confidence:.3f}"
                ),
            )
            return result
        except RetrievalUnavailable:
            degraded.extend(("vector_store", "keyword_index"))
            trace.finish(
                step,
                StepStatus.FAILED,
                "retrieval unavailable; applying no-evidence degradation",
                ErrorCode.RETRIEVAL_FAILED,
            )
            return RetrievalResult(
                hits=(),
                confidence=0.0,
                degraded_dependencies=(
                    "vector_store",
                    "keyword_index",
                ),
            )

    async def _finalize_without_model(
        self,
        *,
        request: AgentRequest,
        run_id: str,
        state: RunStateMachine,
        trace: TraceRecorder,
        retrieval: RetrievalResult,
        decision: PolicyDecision,
        candidate: str,
        citations: Sequence[Citation],
        degraded: list[str],
    ) -> AgentRunResult:
        step = trace.start(
            StepType.POLICY, "applying deterministic pre-generation policy"
        )
        release = self._draft_gate.release(candidate, decision)
        if decision.action is PolicyAction.WITHHOLD_FOR_REVIEW:
            state.transition(RunStatus.NEEDS_REVIEW)
            trace.finish(
                step,
                StepStatus.WAITING,
                "generation blocked and draft withheld for review",
                ErrorCode.REVIEW_REQUIRED,
            )
        else:
            state.transition(RunStatus.COMPLETED)
            trace.finish(
                step,
                StepStatus.SUCCEEDED,
                "safe no-evidence refusal selected",
            )
        memory_warning = await self._extract_memory(request)
        review_id = str(uuid4()) if release.withheld else None
        return AgentRunResult(
            run_id=run_id,
            status=state.status,
            public_content=release.public_content,
            candidate_content=release.candidate_content,
            citations=tuple(citations),
            trace=trace.snapshot(),
            confidence=retrieval.confidence,
            confidence_threshold=self._config.confidence_threshold,
            review_reasons=decision.reasons,
            degraded_dependencies=tuple(dict.fromkeys(degraded)),
            memory_warning=memory_warning,
            error_code=(
                ErrorCode.REVIEW_REQUIRED if release.withheld else None
            ),
            retrieval_strategy=retrieval.strategy,
            review_id=review_id,
        )

    async def _extract_memory(self, request: AgentRequest) -> str | None:
        try:
            return await self._memory.extract_best_effort(
                request.subject_id,
                ConversationMessage(
                    message_id=request.user_message_id,
                    role="user",
                    content=request.user_text,
                    created_at=utc_now(),
                ),
            )
        except Exception:
            logger.warning(
                "memory extraction degraded", exc_info=True, extra={"component": "memory"}
            )
            # Extraction is explicitly best-effort and cannot turn a valid
            # answer into an internal failure.
            return "memory_extraction_degraded"

    def _record_tool_steps(
        self, trace: TraceRecorder, draft: ModelDraft
    ) -> None:
        self._record_tool_executions(trace, draft.tool_executions)

    def _record_tool_executions(
        self, trace: TraceRecorder, executions: Sequence[object]
    ) -> None:
        for execution in executions:
            step = trace.start(
                StepType.TOOL,
                f"tool {execution.tool_name} finished",
            )
            if execution.outcome is ToolOutcome.SUCCEEDED:
                status = StepStatus.SUCCEEDED
            elif execution.outcome is ToolOutcome.TIMEOUT:
                status = StepStatus.TIMEOUT
            elif execution.outcome is ToolOutcome.CANCELLED:
                status = StepStatus.CANCELLED
            else:
                status = StepStatus.FAILED
            trace.finish(
                step,
                status,
                (
                    f"tool={execution.tool_name}, "
                    f"outcome={execution.outcome.value}, "
                    f"attempts={execution.attempts}, "
                    f"duration_ms={execution.duration_ms}"
                ),
                execution.error_code,
            )

    def _failure_result(
        self,
        run_id: str,
        state: RunStateMachine,
        trace: TraceRecorder,
        retrieval: RetrievalResult,
        degraded: list[str],
        error_code: ErrorCode,
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=run_id,
            status=state.status,
            public_content="",
            candidate_content=None,
            citations=(),
            trace=trace.snapshot(),
            confidence=retrieval.confidence,
            confidence_threshold=self._config.confidence_threshold,
            degraded_dependencies=tuple(dict.fromkeys(degraded)),
            error_code=error_code,
            retrieval_strategy=retrieval.strategy,
        )


def _with_report_evidence(
    retrieval: RetrievalResult, request: AgentRequest
) -> RetrievalResult:
    """Treat a completed report preflight as grounded, traceable evidence."""
    document_id = str(uuid5(NAMESPACE_URL, "agent://external-usage-records"))
    month = (
        request.report_scope.start_at.strftime("%Y-%m")
        if request.report_scope
        else "unknown"
    )
    chunk = Chunk(
        document_id=document_id,
        document_version=month,
        chunk_id=f"{document_id}:{request.subject_id}:{month}",
        title="External monthly usage record",
        source="file://data/external/records.csv",
        content=request.report_context or "",
        document_type=DocumentType.TABLE,
    )
    evidence = SearchHit(
        chunk=chunk,
        vector_score=None,
        keyword_score=1.0,
        fused_score=1.0,
        rerank_score=1.0,
    )
    return RetrievalResult(
        hits=(evidence, *retrieval.hits),
        confidence=max(retrieval.confidence, 0.95),
        strategy=f"{retrieval.strategy}+external-report-preflight",
        degraded_dependencies=retrieval.degraded_dependencies,
        conflicting_sources=retrieval.conflicting_sources,
    )
