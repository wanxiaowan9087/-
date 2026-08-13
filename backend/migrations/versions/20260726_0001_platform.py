"""create platform persistence schema

Revision ID: 20260726_0001
Revises:
Create Date: 2026-07-26
"""

from __future__ import annotations

from alembic import op
from backend.app.adapters.sql.models import Base

revision = "20260726_0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    Base.metadata.create_all(bind=op.get_bind(), checkfirst=False)


def downgrade() -> None:
    Base.metadata.drop_all(bind=op.get_bind(), checkfirst=False)
