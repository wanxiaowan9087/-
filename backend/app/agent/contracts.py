from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping, Sequence


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ConversationMode(StrEnum):
    CHAT = "chat"
    REPORT = "report"


class RunStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    NEEDS_REVIEW = "needs_review"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    REJECTED = "rejected"


class StepType(StrEnum):
    CONTEXT = "context"
    RETRIEVAL = "retrieval"
    REASONING = "reasoning"
    TOOL = "tool"
    POLICY = "policy"
    GENERATION = "generation"
    REVIEW = "review"


class StepStatus(StrEnum):
    STARTED = "started"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    WAITING = "waiting"


class ErrorCode(StrEnum):
    VALIDATION_ERROR = "VALIDATION_ERROR"
    MODEL_TIMEOUT = "MODEL_TIMEOUT"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    TOOL_TIMEOUT = "TOOL_TIMEOUT"
    TOOL_FAILED = "TOOL_FAILED"
    RETRIEVAL_FAILED = "RETRIEVAL_FAILED"
    REVIEW_REQUIRED = "REVIEW_REQUIRED"
    CANCELLED = "CANCELLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class ToolOutcome(StrEnum):
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"


class ReviewReason(StrEnum):
    LOW_CONFIDENCE = "low_confidence"
    HIGH_RISK = "high_risk"
    PROMPT_INJECTION = "prompt_injection"
    CRITICAL_TOOL_FAILURE = "critical_tool_failure"
    CONFLICTING_SOURCES = "conflicting_sources"
    POLICY_RULE = "policy_rule"


@dataclass(frozen=True)
class ReportScope:
    subject_id: str
    start_at: datetime
    end_at: datetime

    def __post_init__(self) -> None:
        if not self.subject_id.strip():
            raise ValueError("report subject_id is required")
        if self.start_at.tzinfo is None or self.end_at.tzinfo is None:
            raise ValueError("report timestamps must be timezone-aware")
        if self.start_at > self.end_at:
            raise ValueError("report start_at must not be after end_at")


@dataclass(frozen=True)
class AgentRequest:
    request_id: str
    session_id: str
    subject_id: str
    user_message_id: str
    user_text: str
    run_id: str | None = None
    mode: ConversationMode = ConversationMode.CHAT
    report_scope: ReportScope | None = None
    user_requested_human: bool = False

    def __post_init__(self) -> None:
        if not self.request_id.strip():
            raise ValueError("request_id is required")
        if not self.session_id.strip():
            raise ValueError("session_id is required")
        if not self.subject_id.strip():
            raise ValueError("subject_id is required")
        if not self.user_message_id.strip():
            raise ValueError("user_message_id is required")
        if not self.user_text.strip():
            raise ValueError("user_text is required")
        if self.mode is ConversationMode.REPORT and self.report_scope is None:
            raise ValueError("report_scope is required in report mode")
        if self.mode is ConversationMode.CHAT and self.report_scope is not None:
            raise ValueError("report_scope is only valid in report mode")


@dataclass(frozen=True)
class TraceStep:
    sequence: int
    step_type: StepType
    status: StepStatus
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None
    summary: str
    error_code: ErrorCode | None = None


@dataclass(frozen=True)
class ToolResult:
    display_content: str
    internal_content: Any = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolExecution:
    tool_call_id: str
    tool_name: str
    critical: bool
    outcome: ToolOutcome
    attempts: int
    duration_ms: int
    result_preview: str | None
    arguments_preview: Mapping[str, Any] | None
    error_code: ErrorCode | None = None
    internal_content: Any = None


@dataclass(frozen=True)
class ModelDraft:
    content: str
    cited_chunk_ids: tuple[str, ...] = ()
    tool_executions: tuple[ToolExecution, ...] = ()
    model_name: str = "unknown"


@dataclass(frozen=True)
class AgentRunResult:
    run_id: str
    status: RunStatus
    public_content: str
    candidate_content: str | None
    citations: tuple[Any, ...]
    trace: tuple[TraceStep, ...]
    confidence: float
    confidence_threshold: float
    review_reasons: tuple[ReviewReason, ...] = ()
    degraded_dependencies: tuple[str, ...] = ()
    memory_warning: str | None = None
    error_code: ErrorCode | None = None
    model_name: str = "unknown"
    retrieval_strategy: str = "vector+bm25+rrf+rerank"


@dataclass(frozen=True)
class AgentModelRequest:
    run_id: str
    mode: ConversationMode
    user_text: str
    rendered_context: str
    short_term_messages: Sequence["ConversationMessage"]
    conversation_summary: str | None
    long_term_facts: Sequence["LongTermFact"]
    report_scope: ReportScope | None


@dataclass(frozen=True)
class ConversationMessage:
    message_id: str
    role: str
    content: str
    created_at: datetime


class MemoryType(StrEnum):
    USER_FACT = "user_fact"
    PREFERENCE = "preference"
    TASK_SUMMARY = "task_summary"


@dataclass(frozen=True)
class LongTermFact:
    memory_id: str
    memory_type: MemoryType
    content: str
    confidence: float
    source_message_id: str
    version: int = 1
    active: bool = True

    def __post_init__(self) -> None:
        if not self.content.strip():
            raise ValueError("memory content is required")
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("memory confidence must be between 0 and 1")
        if not self.source_message_id.strip():
            raise ValueError("memory source_message_id is required")
        if self.version < 1:
            raise ValueError("memory version must be positive")

    def corrected(self, content: str) -> "LongTermFact":
        return LongTermFact(
            memory_id=self.memory_id,
            memory_type=self.memory_type,
            content=content,
            confidence=1.0,
            source_message_id=self.source_message_id,
            version=self.version + 1,
            active=True,
        )

    def deactivated(self) -> "LongTermFact":
        return LongTermFact(
            memory_id=self.memory_id,
            memory_type=self.memory_type,
            content=self.content,
            confidence=self.confidence,
            source_message_id=self.source_message_id,
            version=self.version + 1,
            active=False,
        )


@dataclass(frozen=True)
class MemoryContext:
    window: tuple[ConversationMessage, ...] = ()
    summary: str | None = None
    facts: tuple[LongTermFact, ...] = ()
