"""record completed one-time JSON vector imports"""

from __future__ import annotations

from alembic import op

revision = "20260814_0008"
down_revision = "20260814_0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS vector_migration_audits (
            migration_key VARCHAR(128) PRIMARY KEY,
            vector_count INTEGER NOT NULL,
            completed_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS vector_migration_audits")
