"""add registered users and bearer access tokens

Revision ID: 20260728_0002
Revises: 20260726_0001
Create Date: 2026-07-28
"""

from __future__ import annotations

from alembic import op
from backend.app.adapters.sql.models import AccessTokenModel, UserModel

revision = "20260728_0002"
down_revision = "20260726_0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    UserModel.__table__.create(bind=op.get_bind(), checkfirst=True)
    AccessTokenModel.__table__.create(bind=op.get_bind(), checkfirst=True)


def downgrade() -> None:
    AccessTokenModel.__table__.drop(bind=op.get_bind(), checkfirst=True)
    UserModel.__table__.drop(bind=op.get_bind(), checkfirst=True)
