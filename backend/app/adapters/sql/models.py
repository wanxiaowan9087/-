from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    username: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    nickname: Mapped[str] = mapped_column(String(40))
    avatar_url: Mapped[str] = mapped_column(String(2048))
    password_hash: Mapped[str] = mapped_column(String(512))
    role: Mapped[str] = mapped_column(String(16), default="user")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class ExternalIdentityMappingModel(Base):
    __tablename__ = "external_identity_mappings"
    platform_user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    external_user_id: Mapped[str] = mapped_column(String(128), index=True)
    created_by: Mapped[UUID] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    active: Mapped[bool] = mapped_column(Boolean, default=True, index=True)


class AccessTokenModel(Base):
    __tablename__ = "access_tokens"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    user_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_digest: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class SessionModel(Base):
    __tablename__ = "sessions"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    title: Mapped[str] = mapped_column(String(120))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), index=True)
    memory_summary: Mapped[str | None] = mapped_column(Text)
    summary_through_created_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    summary_through_message_id: Mapped[UUID | None] = mapped_column()
    __table_args__ = (Index("ix_sessions_owner_order", "owner_id", "last_message_at", "id"),)


class MessageModel(Base):
    __tablename__ = "messages"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    role: Mapped[str] = mapped_column(String(16))
    status: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(Text)
    reply_to_message_id: Mapped[UUID | None] = mapped_column(ForeignKey("messages.id"))
    run_id: Mapped[UUID | None] = mapped_column(index=True)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    product_recommendations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_messages_session_order", "session_id", "created_at", "id"),)


class RunModel(Base):
    __tablename__ = "runs"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    session_id: Mapped[UUID] = mapped_column(
        ForeignKey("sessions.id", ondelete="CASCADE"), index=True
    )
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    user_message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"))
    assistant_message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"), unique=True)
    status: Mapped[str] = mapped_column(String(32), index=True)
    attempt: Mapped[int] = mapped_column(Integer)
    retry_of_user_message_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("messages.id"), index=True
    )
    model: Mapped[str] = mapped_column(String(128), default="unconfigured")
    retrieval_strategy: Mapped[str] = mapped_column(String(128), default="unconfigured")
    confidence_threshold: Mapped[float] = mapped_column(Float, default=0.65)
    cancellation_requested_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    steps: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    citations: Mapped[list[dict[str, Any]]] = mapped_column(JSON, default=list)
    __table_args__ = (
        Index(
            "ix_runs_active_retry",
            "owner_id",
            "retry_of_user_message_id",
            "status",
        ),
    )


class StreamEventModel(Base):
    __tablename__ = "stream_events"
    run_id: Mapped[UUID] = mapped_column(
        ForeignKey("runs.id", ondelete="CASCADE"), primary_key=True
    )
    sequence: Mapped[int] = mapped_column(Integer, primary_key=True)
    event: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


class FeedbackModel(Base):
    __tablename__ = "feedback"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    rating: Mapped[str] = mapped_column(String(16))
    reason: Mapped[str | None] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(String(1000))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("owner_id", "message_id", name="uq_feedback_owner_message"),)


class MemoryModel(Base):
    __tablename__ = "memories"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    memory_type: Mapped[str] = mapped_column(String(32), index=True)
    content: Mapped[str] = mapped_column(String(5000))
    status: Mapped[str] = mapped_column(String(16), index=True)
    confidence: Mapped[float] = mapped_column(Float)
    source_message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"))
    corrected_from_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (Index("ix_memories_owner_order", "owner_id", "updated_at", "id"),)


class ReviewModel(Base):
    __tablename__ = "reviews"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    run_id: Mapped[UUID] = mapped_column(ForeignKey("runs.id"), unique=True)
    session_id: Mapped[UUID] = mapped_column(ForeignKey("sessions.id"), index=True)
    owner_id: Mapped[str] = mapped_column(String(255), index=True)
    user_message_id: Mapped[UUID] = mapped_column(ForeignKey("messages.id"))
    candidate_content: Mapped[str] = mapped_column(Text)
    reason_codes: Mapped[list[str]] = mapped_column(JSON)
    confidence: Mapped[float] = mapped_column(Float)
    status: Mapped[str] = mapped_column(String(32), index=True)
    version: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ReviewAuditModel(Base):
    __tablename__ = "review_audits"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    review_id: Mapped[UUID] = mapped_column(ForeignKey("reviews.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(String(255))
    request_id: Mapped[str] = mapped_column(String(128), index=True)
    old_status: Mapped[str] = mapped_column(String(32))
    new_status: Mapped[str] = mapped_column(String(32))
    content_digest: Mapped[str | None] = mapped_column(String(64))
    success: Mapped[bool] = mapped_column(Boolean)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class IdempotencyModel(Base):
    __tablename__ = "idempotency_records"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    subject_id: Mapped[str] = mapped_column(String(255))
    operation_id: Mapped[str] = mapped_column(String(128))
    normalized_path: Mapped[str] = mapped_column(String(512))
    key_digest: Mapped[str] = mapped_column(String(64))
    request_digest: Mapped[str] = mapped_column(String(64))
    state: Mapped[str] = mapped_column(String(16))
    status_code: Mapped[int | None] = mapped_column(Integer)
    response_body: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    run_id: Mapped[UUID | None] = mapped_column(ForeignKey("runs.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    __table_args__ = (
        UniqueConstraint(
            "subject_id",
            "operation_id",
            "normalized_path",
            "key_digest",
            name="uq_idempotency_scope_key",
        ),
    )
