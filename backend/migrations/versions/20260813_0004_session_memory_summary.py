"""persist session memory summaries

Revision ID: 20260813_0004
Revises: 20260813_0003
Create Date: 2026-08-13
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy import inspect

revision = "20260813_0004"
down_revision = "20260813_0003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("sessions")}
    with op.batch_alter_table("sessions") as batch:
        if "memory_summary" not in existing:
            batch.add_column(sa.Column("memory_summary", sa.Text(), nullable=True))
        if "summary_through_created_at" not in existing:
            batch.add_column(
                sa.Column("summary_through_created_at", sa.DateTime(timezone=True), nullable=True)
            )
        if "summary_through_message_id" not in existing:
            batch.add_column(sa.Column("summary_through_message_id", sa.Uuid(), nullable=True))


def downgrade() -> None:
    existing = {column["name"] for column in inspect(op.get_bind()).get_columns("sessions")}
    with op.batch_alter_table("sessions") as batch:
        if "summary_through_message_id" in existing:
            batch.drop_column("summary_through_message_id")
        if "summary_through_created_at" in existing:
            batch.drop_column("summary_through_created_at")
        if "memory_summary" in existing:
            batch.drop_column("memory_summary")
