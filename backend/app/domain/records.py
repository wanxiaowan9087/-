from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any
from uuid import UUID


@dataclass(slots=True)
class SessionRecord:
    id: UUID
    owner_id: str
    title: str
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None = None
    memory_summary: str | None = None
    summary_through_created_at: datetime | None = None
    summary_through_message_id: UUID | None = None


@dataclass(slots=True)
class MessageRecord:
    id: UUID
    session_id: UUID
    owner_id: str
    role: str
    status: str
    content: str
    reply_to_message_id: UUID | None
    run_id: UUID | None
    citations: list[dict[str, Any]] = field(default_factory=list)
    created_at: datetime | None = None
    updated_at: datetime | None = None
    product_recommendations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class RunRecord:
    id: UUID
    session_id: UUID
    owner_id: str
    user_message_id: UUID
    assistant_message_id: UUID
    status: str
    attempt: int
    retry_of_user_message_id: UUID | None
    model: str = "unconfigured"
    retrieval_strategy: str = "unconfigured"
    confidence_threshold: float = 0.65
    cancellation_requested_at: datetime | None = None
    started_at: datetime | None = None
    ended_at: datetime | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    citations: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class IdempotencyRecord:
    subject_id: str
    operation_id: str
    normalized_path: str
    key_digest: str
    request_digest: str
    state: str
    status_code: int | None
    response_body: dict[str, Any] | None
    run_id: UUID | None
    created_at: datetime
    expires_at: datetime


@dataclass(slots=True)
class StreamEventRecord:
    run_id: UUID
    sequence: int
    event: dict[str, Any]
    created_at: datetime


@dataclass(slots=True)
class MemoryRecord:
    id: UUID
    owner_id: str
    memory_type: str
    content: str
    status: str
    confidence: float
    source_message_id: UUID
    corrected_from_version: int | None
    version: int
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None


@dataclass(slots=True)
class ReviewRecord:
    id: UUID
    run_id: UUID
    session_id: UUID
    owner_id: str
    user_message_id: UUID
    candidate_content: str
    reason_codes: list[str]
    confidence: float
    status: str
    version: int
    created_at: datetime
    decided_at: datetime | None = None


@dataclass(slots=True)
class FeedbackRecord:
    id: UUID
    message_id: UUID
    owner_id: str
    rating: str
    reason: str | None
    comment: str | None
    created_at: datetime


@dataclass(slots=True)
class ExternalIdentityMappingRecord:
    platform_user_id: UUID
    external_user_id: str
    created_by: UUID
    created_at: datetime
    updated_at: datetime
    active: bool = True


@dataclass(slots=True)
class ReviewAuditRecord:
    id: UUID
    review_id: UUID
    reviewer_id: str
    request_id: str
    old_status: str
    new_status: str
    content_digest: str | None
    success: bool
    created_at: datetime
