"""document the administrator role extension

Revision ID: 20260813_0003
Revises: 20260728_0002
Create Date: 2026-08-13
"""

from __future__ import annotations

revision = "20260813_0003"
down_revision = "20260728_0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Role is a String column. Existing user/reviewer values remain valid and
    # the bootstrap service creates the first admin after migrations complete.
    return None


def downgrade() -> None:
    return None
