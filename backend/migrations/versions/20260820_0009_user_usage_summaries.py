"""add per-user usage summary persistence and durable update jobs"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect


revision = "20260820_0009"
down_revision = "20260814_0008"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    tables = set(inspect(bind).get_table_names())
    if "user_usage_events" not in tables:
        op.create_table(
            "user_usage_events",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("owner_id", sa.String(length=255), nullable=False),
            sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="SET NULL")),
            sa.Column("message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="SET NULL")),
            sa.Column("event_type", sa.String(length=64), nullable=False),
            sa.Column("product_id", sa.String(length=128)),
            sa.Column("model_code", sa.String(length=128)),
            sa.Column("metadata", sa.JSON(), nullable=False),
            sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        )
        op.create_index("ix_usage_events_owner_order", "user_usage_events", ["owner_id", "occurred_at", "id"])
        op.create_index("ix_usage_events_owner_model_order", "user_usage_events", ["owner_id", "model_code", "occurred_at"])

    if "summary_update_jobs" not in tables:
        op.create_table(
            "summary_update_jobs",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("owner_id", sa.String(length=255), nullable=False),
            sa.Column("session_id", sa.Uuid(), sa.ForeignKey("sessions.id", ondelete="CASCADE"), nullable=False),
            sa.Column("trigger_message_id", sa.Uuid(), sa.ForeignKey("messages.id", ondelete="CASCADE"), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="queued"),
            sa.Column("attempts", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("max_attempts", sa.Integer(), nullable=False, server_default="3"),
            sa.Column("next_attempt_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("error_code", sa.String(length=64)),
            sa.Column("error_summary", sa.String(length=500)),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("completed_at", sa.DateTime(timezone=True)),
            sa.UniqueConstraint("trigger_message_id", name="uq_summary_job_trigger_message"),
        )
        op.create_index("ix_summary_jobs_claim", "summary_update_jobs", ["status", "next_attempt_at", "created_at"])

    if "user_summary_snapshots" not in tables:
        op.create_table(
            "user_summary_snapshots",
            sa.Column("id", sa.Uuid(), primary_key=True),
            sa.Column("owner_id", sa.String(length=255), nullable=False),
            sa.Column("version", sa.Integer(), nullable=False),
            sa.Column("status", sa.String(length=16), nullable=False, server_default="active"),
            sa.Column("summary", sa.JSON(), nullable=False),
            sa.Column("display_summary", sa.Text(), nullable=False),
            sa.Column("data_through_at", sa.DateTime(timezone=True)),
            sa.Column("generated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("generator_version", sa.String(length=64), nullable=False),
            sa.UniqueConstraint("owner_id", "version", name="uq_user_summary_snapshot_version"),
        )
        op.create_index("ix_user_summary_snapshot_owner_order", "user_summary_snapshots", ["owner_id", "generated_at", "id"])
        op.create_index(
            "uq_user_summary_snapshot_active",
            "user_summary_snapshots",
            ["owner_id"],
            unique=True,
            postgresql_where=sa.text("status = 'active'"),
        )


def downgrade() -> None:
    op.drop_index("uq_user_summary_snapshot_active", table_name="user_summary_snapshots")
    op.drop_index("ix_user_summary_snapshot_owner_order", table_name="user_summary_snapshots")
    op.drop_table("user_summary_snapshots")
    op.drop_index("ix_summary_jobs_claim", table_name="summary_update_jobs")
    op.drop_table("summary_update_jobs")
    op.drop_index("ix_usage_events_owner_model_order", table_name="user_usage_events")
    op.drop_index("ix_usage_events_owner_order", table_name="user_usage_events")
    op.drop_table("user_usage_events")
