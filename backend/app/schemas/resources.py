from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import Field, field_validator

from backend.app.schemas.common import ContractModel, Envelope, Page


class LiveStatus(ContractModel):
    status: Literal["alive"] = "alive"


class ReadyStatus(ContractModel):
    status: Literal["ready", "not_ready"]
    dependencies: dict[
        Literal["postgresql", "redis", "vector_store", "model"],
        Literal["available", "degraded", "unavailable", "not_checked"],
    ]


class AuthUser(ContractModel):
    id: UUID
    username: str = Field(min_length=3, max_length=64)
    nickname: str = Field(min_length=1, max_length=40)
    avatar_url: str = Field(min_length=8, max_length=2048)
    role: Literal["user", "reviewer", "admin"]
    created_at: datetime


class RegisterRequest(ContractModel):
    phone: str = Field(min_length=11, max_length=16)
    password: str = Field(min_length=6, max_length=20)
    nickname: str = Field(min_length=1, max_length=40)
    verification_code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    user_agreement_version: str = Field(min_length=1, max_length=32)
    privacy_policy_version: str = Field(min_length=1, max_length=32)
    agree_user_agreement: Literal[True]
    agree_privacy_policy: Literal[True]
    avatar_url: str | None = Field(default=None, min_length=8, max_length=2048)

    @field_validator("password")
    @classmethod
    def validate_password(cls, value: str) -> str:
        if (
            not any(char.isalpha() for char in value)
            or not any(char.isdigit() for char in value)
            or not any(not char.isalnum() for char in value)
        ):
            raise ValueError("password must contain a letter, number, and special character")
        return value


class LoginRequest(ContractModel):
    phone: str = Field(min_length=11, max_length=16)
    password: str = Field(min_length=6, max_length=20)


class SmsCodeRequest(ContractModel):
    phone: str = Field(min_length=11, max_length=16)
    purpose: Literal["register", "password_reset"]


class SmsCodeResult(ContractModel):
    accepted: bool = True
    retry_after_seconds: int = Field(ge=0)


class PasswordResetRequest(ContractModel):
    phone: str = Field(min_length=11, max_length=16)
    verification_code: str = Field(min_length=6, max_length=6, pattern=r"^[0-9]{6}$")
    new_password: str = Field(min_length=6, max_length=20)

    @field_validator("new_password")
    @classmethod
    def validate_reset_password(cls, value: str) -> str:
        if not any(char.isalpha() for char in value) or not any(char.isdigit() for char in value) or not any(not char.isalnum() for char in value):
            raise ValueError("password must contain a letter, number, and special character")
        return value


class LegalDocument(ContractModel):
    document_type: Literal["user_agreement", "privacy_policy"]
    version: str
    title: str
    content: str
    content_sha256: str
    effective_at: datetime


class PasswordResetResult(ContractModel):
    reset: bool = True


class UpdateProfileRequest(ContractModel):
    nickname: str | None = Field(default=None, min_length=1, max_length=40)
    avatar_url: str | None = Field(default=None, min_length=8, max_length=2048)


class ChangePasswordRequest(ContractModel):
    current_password: str = Field(min_length=6, max_length=20)
    new_password: str = Field(min_length=6, max_length=20)

    @field_validator("new_password")
    @classmethod
    def validate_new_password(cls, value: str) -> str:
        if (
            not any(char.isalpha() for char in value)
            or not any(char.isdigit() for char in value)
            or not any(not char.isalnum() for char in value)
        ):
            raise ValueError("password must contain a letter, number, and special character")
        return value


class AuthSession(ContractModel):
    access_token: str = Field(min_length=20, max_length=256)
    token_type: Literal["Bearer"] = "Bearer"
    expires_at: datetime
    user: AuthUser


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
    product_recommendations: list[dict[str, Any]] = Field(default_factory=list)
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


class KnowledgeFile(ContractModel):
    id: str = Field(min_length=1, max_length=128)
    filename: str = Field(min_length=1, max_length=180)
    title: str = Field(min_length=1, max_length=120)
    source: str = Field(min_length=1, max_length=2048)
    size_bytes: int = Field(ge=1)
    chunk_count: int = Field(ge=0)
    uploaded_at: datetime


class KnowledgeReindexResult(ContractModel):
    chunks_indexed: int = Field(ge=0)


class ExternalIdentityMappingRequest(ContractModel):
    platform_user_id: UUID
    external_user_id: str = Field(min_length=1, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class ExternalIdentityMapping(ContractModel):
    platform_user_id: UUID
    external_user_id: str
    active: bool
    updated_at: datetime


class UsageSummaryTopic(ContractModel):
    name: str = Field(min_length=1, max_length=80)
    count: int = Field(ge=1)


class UsageSummaryMemory(ContractModel):
    content: str = Field(min_length=1, max_length=5000)
    confidence: float = Field(ge=0, le=1)
    source_message_id: UUID


class UsageSummaryProduct(ContractModel):
    product_id: str | None = Field(default=None, max_length=128)
    model_code: str = Field(min_length=1, max_length=128)
    recommendation_count: int = Field(ge=1)


class UsageSummaryDeviceUsage(ContractModel):
    status: Literal["available", "unavailable"]
    reason: str | None = Field(default=None, max_length=200)


class UsageSummaryConversationOverview(ContractModel):
    session_count: int = Field(ge=0)
    message_count: int = Field(ge=0)
    active_days: int = Field(ge=0)
    top_topics: list[UsageSummaryTopic] = Field(default_factory=list, max_length=10)


class UsageSummaryProductActivity(ContractModel):
    recommended_models: list[UsageSummaryProduct] = Field(default_factory=list, max_length=20)
    detail_view_count: int = Field(ge=0)
    three_d_view_count: int = Field(ge=0)


class UsageSummary(ContractModel):
    status: Literal["ready", "updating", "empty"]
    version: int = Field(ge=0)
    conversation_overview: UsageSummaryConversationOverview
    facts: list[UsageSummaryMemory] = Field(default_factory=list, max_length=50)
    preferences: list[UsageSummaryMemory] = Field(default_factory=list, max_length=50)
    product_activity: UsageSummaryProductActivity
    device_usage: UsageSummaryDeviceUsage
    generated_at: datetime | None
    data_through_at: datetime | None


class UsageEventRequest(ContractModel):
    event_type: Literal["product_detail_viewed", "product_3d_viewed"]
    product_id: str = Field(min_length=1, max_length=128)
    model_code: str | None = Field(default=None, max_length=128)


class UsageEvent(ContractModel):
    id: UUID
    event_type: Literal["product_detail_viewed", "product_3d_viewed", "product_recommended"]
    product_id: str | None
    model_code: str | None
    occurred_at: datetime


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
KnowledgeFileEnvelope = Envelope[KnowledgeFile]
DeleteMemoryEnvelope = Envelope[DeleteMemoryResult]
ReviewPageEnvelope = Envelope[Page[ReviewTask]]
ReviewDecisionEnvelope = Envelope[ReviewDecisionResult]
UsageSummaryEnvelope = Envelope[UsageSummary]
UsageEventEnvelope = Envelope[UsageEvent]
