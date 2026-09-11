from __future__ import annotations

from typing import Generic, Literal, TypeVar

from pydantic import BaseModel, ConfigDict, Field

T = TypeVar("T")
ErrorCode = Literal[
    "VALIDATION_ERROR",
    "BAD_REQUEST",
    "UNAUTHORIZED",
    "FORBIDDEN",
    "NOT_FOUND",
    "CONFLICT",
    "KNOWLEDGE_DUPLICATE",
    "KNOWLEDGE_NAME_CONFLICT",
    "KNOWLEDGE_SIMILAR",
    "KNOWLEDGE_CATALOG_FAILED",
    "IDEMPOTENCY_KEY_REUSED",
    "IDEMPOTENCY_IN_PROGRESS",
    "STREAM_REPLAY_EXPIRED",
    "RATE_LIMITED",
    "MODEL_TIMEOUT",
    "MODEL_UNAVAILABLE",
    "TOOL_TIMEOUT",
    "TOOL_FAILED",
    "RETRIEVAL_FAILED",
    "STORAGE_UNAVAILABLE",
    "REVIEW_REQUIRED",
    "CANCELLED",
    "INTERNAL_ERROR",
]


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid", from_attributes=True)


class Envelope(ContractModel, Generic[T]):
    code: Literal["OK"] = "OK"
    message: Literal["success"] = "success"
    data: T
    request_id: str = Field(min_length=8, max_length=128, pattern=r"^[A-Za-z0-9_-]+$")


class PageInfo(ContractModel):
    next_cursor: str | None
    has_more: bool


class Page(ContractModel, Generic[T]):
    items: list[T]
    page: PageInfo


class ValidationIssue(ContractModel):
    field: str = Field(max_length=256)
    reason: str = Field(max_length=512)


class ErrorData(ContractModel):
    retryable: bool = False
    retry_after_seconds: int | None = Field(default=None, ge=1)
    issues: list[ValidationIssue] | None = Field(default=None, max_length=50)


class ErrorEnvelope(ContractModel):
    code: ErrorCode
    message: str = Field(min_length=1, max_length=512)
    data: ErrorData | None
    request_id: str
