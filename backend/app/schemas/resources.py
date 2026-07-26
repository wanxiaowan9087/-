from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field

from backend.app.schemas.common import ContractModel, Envelope, Page


class LiveStatus(ContractModel):
    status: Literal["alive"] = "alive"


class ReadyStatus(ContractModel):
    status: Literal["ready", "not_ready"]
    dependencies: dict[
        Literal["postgresql", "redis", "vector_store", "model"],
        Literal["available", "degraded", "unavailable", "not_checked"],
    ]


class CreateSessionRequest(ContractModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)


class Session(ContractModel):
    id: UUID
    title: str = Field(min_length=1, max_length=120)
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None


class Citation(ContractModel):
    citation_id: UUID
    document_id: UUID
    document_version: str = Field(min_length=1, max_length=128)
    chunk_id: str = Field(min_length=1, max_length=256)
    title: str = Field(min_length=1, max_length=300)
    source: str = Field(min_length=1, max_length=2048)
    page: int | None = Field(default=None, ge=1)
    score: float = Field(ge=0, le=1)
    excerpt: str | None = Field(default=None, max_length=1000)


class Message(ContractModel):
    id: UUID
    session_id: UUID
    role: Literal["user", "assistant"]
    status: Literal[
        "accepted", "generating", "completed", "needs_review", "cancelled", "failed", "rejected"
    ]
    content: str = Field(max_length=100_000)
    reply_to_message_id: UUID | None
    run_id: UUID | None
    citations: list[Citation]
    created_at: datetime
    updated_at: datetime


class NewChatRequest(ContractModel):
    mode: Literal["new"]
    session_id: UUID
    content: str = Field(min_length=1, max_length=20_000)


class RetryChatRequest(ContractModel):
    mode: Literal["retry"]
    session_id: UUID
    original_user_message_id: UUID


ChatRequest = Annotated[NewChatRequest | RetryChatRequest, Field(discriminator="mode")]


class CreateFeedbackRequest(ContractModel):
    rating: Literal["positive", "negative"]
    reason: (
        Literal["helpful", "accurate", "inaccurate", "unsupported", "unsafe", "irrelevant", "other"]
        | None
    ) = None
    comment: str | None = Field(default=None, max_length=1000)


class Feedback(ContractModel):
    id: UUID
    message_id: UUID
    rating: Literal["positive", "negative"]
    reason: str | None
    comment: str | None
    created_at: datetime


class CancelRunRequest(ContractModel):
    reason: str | None = Field(default=None, max_length=300)


class CancelRunResult(ContractModel):
    run_id: UUID
    status: Literal["cancellation_requested", "already_terminal"]
    requested_at: datetime


class TraceStep(ContractModel):
    sequence: int = Field(ge=1)
    step_type: Literal[
        "context", "retrieval", "reasoning", "tool", "policy", "generation", "review"
    ]
    status: Literal["started", "succeeded", "failed", "timeout", "cancelled", "waiting"]
    started_at: datetime
    ended_at: datetime | None
    duration_ms: int | None = Field(ge=0)
    summary: str = Field(max_length=1000)
    error_code: str | None


class RunTrace(ContractModel):
    run_id: UUID
    session_id: UUID
    user_message_id: UUID
    assistant_message_id: UUID | None
    status: Literal[
        "queued", "running", "needs_review", "completed", "cancelled", "failed", "rejected"
    ]
    model: str = Field(max_length=128)
    retrieval_strategy: str = Field(max_length=128)
    confidence_threshold: float = Field(ge=0, le=1)
    steps: list[TraceStep]
    citations: list[Citation]
    started_at: datetime
    ended_at: datetime | None


class Memory(ContractModel):
    id: UUID
    memory_type: Literal["user_fact", "preference", "task_summary"]
    content: str = Field(min_length=1, max_length=5000)
    status: Literal["active", "inactive"]
    confidence: float = Field(ge=0, le=1)
    source_message_id: UUID
    corrected_from_version: int | None = Field(default=None, ge=1)
    version: int = Field(ge=1)
    created_at: datetime
    updated_at: datetime


class CorrectMemoryRequest(ContractModel):
    action: Literal["correct"]
    content: str = Field(min_length=1, max_length=5000)
    expected_version: int = Field(ge=1)


class DeactivateMemoryRequest(ContractModel):
    action: Literal["deactivate"]
    expected_version: int = Field(ge=1)
    reason: str | None = Field(default=None, max_length=300)


UpdateMemoryRequest = Annotated[
    CorrectMemoryRequest | DeactivateMemoryRequest, Field(discriminator="action")
]


class DeleteMemoryResult(ContractModel):
    memory_id: UUID
    deleted_at: datetime
    backup_expiry_note: str = Field(max_length=300)


class ReviewTask(ContractModel):
    id: UUID
    run_id: UUID
    session_id: UUID
    user_message_id: UUID
    candidate_content: str = Field(max_length=100_000)
    reason_codes: list[str] = Field(min_length=1)
    confidence: float = Field(ge=0, le=1)
    status: Literal["pending", "approved", "rejected", "edited_and_published"]
    version: int = Field(ge=1)
    created_at: datetime
    decided_at: datetime | None


class ApproveReviewRequest(ContractModel):
    decision: Literal["approve"]
    expected_version: int = Field(ge=1)
    note: str | None = Field(default=None, max_length=1000)


class RejectReviewRequest(ContractModel):
    decision: Literal["reject"]
    expected_version: int = Field(ge=1)
    note: str = Field(min_length=1, max_length=1000)


class EditReviewRequest(ContractModel):
    decision: Literal["edit_and_publish"]
    expected_version: int = Field(ge=1)
    edited_content: str = Field(min_length=1, max_length=100_000)
    note: str | None = Field(default=None, max_length=1000)


ReviewDecisionRequest = Annotated[
    ApproveReviewRequest | RejectReviewRequest | EditReviewRequest,
    Field(discriminator="decision"),
]


class ReviewDecisionResult(ContractModel):
    review_id: UUID
    status: Literal["approved", "rejected", "edited_and_published"]
    published_message_id: UUID | None
    decided_at: datetime
    version: int = Field(ge=2)


LiveEnvelope = Envelope[LiveStatus]
ReadyEnvelope = Envelope[ReadyStatus]
SessionEnvelope = Envelope[Session]
SessionPageEnvelope = Envelope[Page[Session]]
MessagePageEnvelope = Envelope[Page[Message]]
FeedbackEnvelope = Envelope[Feedback]
CancelRunEnvelope = Envelope[CancelRunResult]
RunTraceEnvelope = Envelope[RunTrace]
MemoryEnvelope = Envelope[Memory]
MemoryPageEnvelope = Envelope[Page[Memory]]
DeleteMemoryEnvelope = Envelope[DeleteMemoryResult]
ReviewPageEnvelope = Envelope[Page[ReviewTask]]
ReviewDecisionEnvelope = Envelope[ReviewDecisionResult]
