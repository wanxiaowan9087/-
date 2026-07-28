from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, RootModel

from backend.app.schemas.common import ContractModel
from backend.app.schemas.resources import Citation


class MetaPayload(ContractModel):
    user_message_id: UUID
    assistant_message_id: UUID
    attempt: int = Field(ge=1)
    retry_of_user_message_id: UUID | None
    replayed: bool


class StatusPayload(ContractModel):
    phase: Literal[
        "accepted",
        "preparing_context",
        "retrieving",
        "reasoning",
        "calling_tool",
        "generating",
        "checking_policy",
        "cancelling",
        "awaiting_review",
        "completed",
    ]
    detail: str | None = Field(default=None, max_length=300)


class ToolStartPayload(ContractModel):
    tool_call_id: UUID
    tool_name: str = Field(min_length=1, max_length=128)
    critical: bool
    arguments_preview: dict[str, Any] | None = None


class ToolEndPayload(ContractModel):
    tool_call_id: UUID
    tool_name: str = Field(min_length=1, max_length=128)
    outcome: Literal["succeeded", "failed", "timeout", "cancelled"]
    duration_ms: int = Field(ge=0)
    result_preview: str | None = Field(max_length=1000)
    error_code: str | None


class DeltaPayload(ContractModel):
    index: int = Field(ge=0)
    content: str = Field(min_length=1, max_length=20_000)


class ReviewRequiredPayload(ContractModel):
    review_id: UUID
    reason_codes: list[
        Literal[
            "low_confidence",
            "high_risk",
            "prompt_injection",
            "critical_tool_failure",
            "conflicting_sources",
            "policy_rule",
        ]
    ] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    draft_withheld: Literal[True] = True


class TokenUsage(ContractModel):
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class DonePayload(ContractModel):
    outcome: Literal["completed", "needs_review", "cancelled"]
    assistant_message_id: UUID | None
    finish_reason: Literal["stop", "needs_review", "user_cancelled"]
    usage: TokenUsage | None


class StreamErrorPayload(ContractModel):
    code: str
    message: str = Field(min_length=1, max_length=512)
    retryable: bool
    retry_after_seconds: int | None = Field(default=None, ge=1)


class EventBase(ContractModel):
    event_type: str
    sequence: int = Field(ge=1)
    request_id: str
    session_id: UUID
    run_id: UUID
    timestamp: datetime
    payload: Any


class MetaEvent(EventBase):
    event_type: Literal["meta"]
    payload: MetaPayload


class StatusEvent(EventBase):
    event_type: Literal["status"]
    payload: StatusPayload


class ToolStartEvent(EventBase):
    event_type: Literal["tool_start"]
    payload: ToolStartPayload


class ToolEndEvent(EventBase):
    event_type: Literal["tool_end"]
    payload: ToolEndPayload


class DeltaEvent(EventBase):
    event_type: Literal["delta"]
    payload: DeltaPayload


class CitationEvent(EventBase):
    event_type: Literal["citation"]
    payload: Citation


class ReviewRequiredEvent(EventBase):
    event_type: Literal["review_required"]
    payload: ReviewRequiredPayload


class DoneEvent(EventBase):
    event_type: Literal["done"]
    payload: DonePayload


class ErrorEvent(EventBase):
    event_type: Literal["error"]
    payload: StreamErrorPayload


SseEvent = Annotated[
    MetaEvent
    | StatusEvent
    | ToolStartEvent
    | ToolEndEvent
    | DeltaEvent
    | CitationEvent
    | ReviewRequiredEvent
    | DoneEvent
    | ErrorEvent,
    Field(discriminator="event_type"),
]


class SseEventSchema(RootModel[SseEvent]):
    """OpenAPI-only wrapper for the discriminated SSE event union."""


TERMINAL_EVENT_TYPES = frozenset({"done", "error"})
