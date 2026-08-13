"""map platform identities to authorized external report identities"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy import inspect

revision = "20260813_0006"
down_revision = "20260813_0005"
branch_labels = None
depends_on = None

def upgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "external_identity_mappings" not in inspector.get_table_names():
        op.create_table(
            "external_identity_mappings",
            sa.Column("platform_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), primary_key=True),
            sa.Column("external_user_id", sa.String(length=128), nullable=False),
            sa.Column("created_by", sa.Uuid(), sa.ForeignKey("users.id"), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("active", sa.Boolean(), nullable=False, server_default=sa.true()),
        )
    existing_indexes = {index["name"] for index in inspect(bind).get_indexes("external_identity_mappings")}
    if "ix_external_identity_mappings_external_user_id" not in existing_indexes:
        op.create_index("ix_external_identity_mappings_external_user_id", "external_identity_mappings", ["external_user_id"])
    if "ix_external_identity_mappings_active" not in existing_indexes:
        op.create_index("ix_external_identity_mappings_active", "external_identity_mappings", ["active"])

def downgrade() -> None:
    bind = op.get_bind()
    inspector = inspect(bind)
    if "external_identity_mappings" not in inspector.get_table_names():
        return
    existing_indexes = {index["name"] for index in inspector.get_indexes("external_identity_mappings")}
    if "ix_external_identity_mappings_active" in existing_indexes:
        op.drop_index("ix_external_identity_mappings_active", table_name="external_identity_mappings")
    if "ix_external_identity_mappings_external_user_id" in existing_indexes:
        op.drop_index("ix_external_identity_mappings_external_user_id", table_name="external_identity_mappings")
    op.drop_table("external_identity_mappings")
