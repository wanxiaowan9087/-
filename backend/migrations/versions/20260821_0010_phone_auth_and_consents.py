"""persist protected phone identity, legal consent and security audits"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from uuid import uuid4

from alembic import op
import sqlalchemy as sa

from backend.app.application.legal import CURRENT_LEGAL_DOCUMENTS
from backend.app.application.phone_crypto import PhoneProtector

revision = "20260821_0010"
down_revision = "20260820_0009"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {item["name"] for item in inspector.get_columns("users")}
    for name, type_ in (
        ("phone_ciphertext", sa.String(512)),
        ("phone_lookup_digest", sa.String(64)),
        ("phone_key_version", sa.String(16)),
        ("phone_verified_at", sa.DateTime(timezone=True)),
        ("tokens_revoked_after", sa.DateTime(timezone=True)),
    ):
        if name not in columns:
            op.add_column("users", sa.Column(name, type_, nullable=True))
    op.create_index("ix_users_phone_lookup_digest", "users", ["phone_lookup_digest"], unique=True)
    op.create_table(
        "agreement_versions",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False), sa.Column("content", sa.Text(), nullable=False),
        sa.Column("content_digest", sa.String(64), nullable=False), sa.Column("effective_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.UniqueConstraint("document_type", "version", name="uq_agreement_document_version"),
    )
    op.create_table(
        "user_agreement_consents",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="CASCADE"), nullable=False),
        sa.Column("agreement_version_id", sa.Uuid(), sa.ForeignKey("agreement_versions.id"), nullable=False), sa.Column("document_type", sa.String(32), nullable=False),
        sa.Column("version", sa.String(32), nullable=False), sa.Column("content_digest", sa.String(64), nullable=False), sa.Column("consented_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("request_id", sa.String(128)), sa.Column("ip_digest", sa.String(64)), sa.Column("user_agent_digest", sa.String(64)),
        sa.UniqueConstraint("user_id", "agreement_version_id", name="uq_user_agreement_consent"),
    )
    op.create_table(
        "sms_verification_audits",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("purpose", sa.String(32), nullable=False), sa.Column("phone_redacted", sa.String(32), nullable=False), sa.Column("phone_digest", sa.String(64), nullable=False),
        sa.Column("provider_request_id", sa.String(128)), sa.Column("status", sa.String(32), nullable=False), sa.Column("provider_error_code", sa.String(64)),
        sa.Column("request_id", sa.String(128)), sa.Column("ip_digest", sa.String(64)), sa.Column("user_agent_digest", sa.String(64)), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_table(
        "security_audit_logs",
        sa.Column("id", sa.Uuid(), primary_key=True), sa.Column("actor_user_id", sa.Uuid(), sa.ForeignKey("users.id", ondelete="SET NULL")),
        sa.Column("event_type", sa.String(64), nullable=False), sa.Column("result", sa.String(32), nullable=False), sa.Column("request_id", sa.String(128)),
        sa.Column("ip_digest", sa.String(64)), sa.Column("user_agent_digest", sa.String(64)), sa.Column("metadata", sa.JSON(), nullable=False), sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    _seed_legal(bind)
    _migrate_admin(bind)


def _seed_legal(bind) -> None:
    now = datetime.now(UTC)
    for document in CURRENT_LEGAL_DOCUMENTS:
        bind.execute(sa.text("""INSERT INTO agreement_versions (id, document_type, version, content, content_digest, effective_at, status, created_at)
            VALUES (:id, :kind, :version, :content, :digest, :effective, 'active', :created)
            ON CONFLICT (document_type, version) DO NOTHING"""), {
            "id": str(uuid4()), "kind": document.document_type, "version": document.version, "content": document.content,
            "digest": document.content_digest, "effective": now, "created": now,
        })


def _migrate_admin(bind) -> None:
    row = bind.execute(sa.text("SELECT id FROM users WHERE username = 'xiaow' LIMIT 1")).first()
    encryption = os.getenv("APP_PHONE_ENCRYPTION_KEY") or os.getenv("PHONE_ENCRYPTION_KEY")
    lookup = os.getenv("APP_PHONE_LOOKUP_HMAC_KEY") or os.getenv("PHONE_LOOKUP_HMAC_KEY")
    if row and (not encryption or not lookup):
        raise RuntimeError("phone encryption and lookup keys are required to migrate xiaow")
    if not row:
        return
    protector = PhoneProtector(encryption, lookup)
    digest = protector.lookup_digest("15884119087")
    bind.execute(sa.text("""UPDATE users SET phone_ciphertext=:cipher, phone_lookup_digest=:digest,
            phone_key_version=:version, phone_verified_at=COALESCE(phone_verified_at, :verified)
            WHERE id=:id AND (phone_lookup_digest IS NULL OR phone_lookup_digest=:digest)"""), {
            "cipher": protector.encrypt("15884119087"), "digest": digest, "version": protector.key_version,
            "verified": datetime.now(UTC), "id": row[0],
        })


def downgrade() -> None:
    for table in ("security_audit_logs", "sms_verification_audits", "user_agreement_consents", "agreement_versions"):
        op.drop_table(table)
    op.drop_index("ix_users_phone_lookup_digest", table_name="users")
    for name in ("tokens_revoked_after", "phone_verified_at", "phone_key_version", "phone_lookup_digest", "phone_ciphertext"):
        op.drop_column("users", name)
