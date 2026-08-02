from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol
from uuid import uuid4

from ..rag.citations import CitationService
from ..rag.models import Citation, RetrievalResult
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
from .tracing import RunStateMachine, TraceRecorder

logger = logging.getLogger(__name__)


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


def answer_identity_intent(user_text: str) -> str | None:
    compact = re.sub(r"\s+", "", user_text.lower())
    if not any(pattern in compact for pattern in IDENTITY_INTENT_PATTERNS):
        return None
    return (
        "我是小智智能客服，一个面向扫地/扫拖机器人场景的受控知识库 Agent。"
        "我会优先基于已接入的产品资料回答选购、使用、维护和故障排查问题；"
        "如果资料不足，我会说明无法确认，避免编造。\n\n"
        "你也可以上传新的 `.txt` 或 `.md` 知识文件，上传后系统会把它加入本地知识库检索范围，"
        "后续提问就能基于新资料回答。"
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
            memory_context = await self._prepare_memory(
                request, trace, degraded
            )
            token.checkpoint()
            retrieval = await self._retrieve(
                request.user_text, trace, degraded
            )
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
        for execution in draft.tool_executions:
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
