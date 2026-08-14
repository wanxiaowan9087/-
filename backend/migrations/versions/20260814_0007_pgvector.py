"""move knowledge vectors into PostgreSQL pgvector"""

from __future__ import annotations

from alembic import op

revision = "20260814_0007"
down_revision = "20260813_0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS vector")
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS knowledge_vectors (
            chunk_id VARCHAR(256) PRIMARY KEY,
            document_id UUID NOT NULL,
            document_version VARCHAR(128) NOT NULL,
            title TEXT NOT NULL,
            source TEXT NOT NULL,
            content TEXT NOT NULL,
            document_type VARCHAR(32) NOT NULL,
            location JSONB NOT NULL DEFAULT '{}'::jsonb,
            metadata JSONB NOT NULL DEFAULT '{}'::jsonb,
            embedding vector(1024) NOT NULL,
            created_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_document_id "
        "ON knowledge_vectors (document_id)"
    )
    op.execute(
        "CREATE INDEX IF NOT EXISTS ix_knowledge_vectors_embedding_hnsw "
        "ON knowledge_vectors USING hnsw (embedding vector_cosine_ops) "
        "WITH (m = 16, ef_construction = 64)"
    )


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS knowledge_vectors")
