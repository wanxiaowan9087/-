from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from backend.app.rag.models import Chunk, DocumentType, ScoredChunk, SourceLocation
from backend.app.rag.ports import EmbeddingVector


class PgVectorStore:
    """Async pgvector adapter for durable production knowledge retrieval."""

    def __init__(self, engine: AsyncEngine, dimensions: int) -> None:
        if dimensions <= 0:
            raise ValueError("pgvector dimensions must be positive")
        self._engine = engine
        self._dimensions = dimensions

    async def replace_document(
        self,
        document_id: str,
        document_version: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[EmbeddingVector],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and vector counts differ")
        if any(
            chunk.document_id != document_id or chunk.document_version != document_version
            for chunk in chunks
        ):
            raise ValueError("chunks must belong to the replacement document version")
        payloads = [
            _payload(chunk, self._vector_literal(vector))
            for chunk, vector in zip(chunks, vectors, strict=True)
        ]
        async with self._engine.begin() as connection:
            await connection.execute(
                text(
                    "DELETE FROM knowledge_vectors "
                    "WHERE document_id = CAST(:document_id AS uuid)"
                ),
                {"document_id": document_id},
            )
            for payload in payloads:
                await connection.execute(_UPSERT_CHUNK, payload)

    async def search(
        self, query_vector: EmbeddingVector, limit: int
    ) -> Sequence[ScoredChunk]:
        if limit <= 0:
            return ()
        vector = self._vector_literal(query_vector)
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(
                    _SEARCH_CHUNKS,
                    {"query_vector": vector, "limit": limit},
                )
            ).mappings().all()
        return tuple(
            ScoredChunk(
                chunk=_chunk_from_row(row),
                score=max(0.0, min(1.0, float(row["score"]))),
            )
            for row in rows
        )

    async def resolve(self, chunk_ids: Sequence[str]) -> Sequence[Chunk]:
        if not chunk_ids:
            return ()
        async with self._engine.connect() as connection:
            rows = (
                await connection.execute(_RESOLVE_CHUNKS, {"chunk_ids": list(chunk_ids)})
            ).mappings().all()
        resolved = {str(row["chunk_id"]): _chunk_from_row(row) for row in rows}
        return tuple(resolved[chunk_id] for chunk_id in chunk_ids if chunk_id in resolved)

    async def load_all_chunks(self) -> tuple[Chunk, ...]:
        async with self._engine.connect() as connection:
            rows = (await connection.execute(_LOAD_ALL_CHUNKS)).mappings().all()
        return tuple(_chunk_from_row(row) for row in rows)

    async def health(self) -> str:
        async with self._engine.connect() as connection:
            await connection.execute(text("SELECT 1 FROM knowledge_vectors LIMIT 1"))
        return "available"

    def _vector_literal(self, vector: EmbeddingVector) -> str:
        if len(vector) != self._dimensions:
            raise ValueError(
                f"embedding dimensions differ: expected {self._dimensions}, got {len(vector)}"
            )
        values = [float(value) for value in vector]
        if not all(math.isfinite(value) for value in values):
            raise ValueError("embedding values must be finite")
        return "[" + ",".join(repr(value) for value in values) + "]"


_SELECT_COLUMNS = """
    chunk_id, document_id, document_version, title, source, content,
    document_type, location, metadata
"""

_UPSERT_CHUNK = text(
    """
    INSERT INTO knowledge_vectors (
        chunk_id, document_id, document_version, title, source, content,
        document_type, location, metadata, embedding
    ) VALUES (
        :chunk_id, CAST(:document_id AS uuid), :document_version, :title,
        :source, :content, :document_type,
        CAST(:location AS jsonb), CAST(:metadata AS jsonb), CAST(:embedding AS vector)
    )
    ON CONFLICT (chunk_id) DO UPDATE SET
        document_id = EXCLUDED.document_id,
        document_version = EXCLUDED.document_version,
        title = EXCLUDED.title,
        source = EXCLUDED.source,
        content = EXCLUDED.content,
        document_type = EXCLUDED.document_type,
        location = EXCLUDED.location,
        metadata = EXCLUDED.metadata,
        embedding = EXCLUDED.embedding,
        updated_at = CURRENT_TIMESTAMP
    """
)

_SEARCH_CHUNKS = text(
    f"""
    SELECT {_SELECT_COLUMNS},
        LEAST(1.0, GREATEST(
            0.0, (2.0 - (embedding <=> CAST(:query_vector AS vector))) / 2.0
        )) AS score
    FROM knowledge_vectors
    ORDER BY embedding <=> CAST(:query_vector AS vector), chunk_id
    LIMIT :limit
    """
)

_RESOLVE_CHUNKS = text(f"""
    SELECT {_SELECT_COLUMNS}
    FROM knowledge_vectors
    WHERE chunk_id = ANY(CAST(:chunk_ids AS text[]))
""")

_LOAD_ALL_CHUNKS = text(f"SELECT {_SELECT_COLUMNS} FROM knowledge_vectors ORDER BY chunk_id")


def _payload(chunk: Chunk, embedding: str) -> dict[str, str]:
    return {
        "chunk_id": chunk.chunk_id,
        "document_id": chunk.document_id,
        "document_version": chunk.document_version,
        "title": chunk.title,
        "source": chunk.source,
        "content": chunk.content,
        "document_type": chunk.document_type.value,
        "location": json.dumps(
            {
                "section": chunk.location.section,
                "page": chunk.location.page,
                "row": chunk.location.row,
            },
            ensure_ascii=False,
        ),
        "metadata": json.dumps(dict(chunk.metadata), ensure_ascii=False),
        "embedding": embedding,
    }


def _chunk_from_row(row: Mapping[str, Any]) -> Chunk:
    location = _json_object(row["location"])
    return Chunk(
        document_id=str(row["document_id"]),
        document_version=str(row["document_version"]),
        chunk_id=str(row["chunk_id"]),
        title=str(row["title"]),
        source=str(row["source"]),
        content=str(row["content"]),
        document_type=DocumentType(str(row["document_type"])),
        location=SourceLocation(
            section=location.get("section"),
            page=location.get("page"),
            row=location.get("row"),
        ),
        metadata=_json_object(row["metadata"]),
    )


def _json_object(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        return dict(value)
    if isinstance(value, str):
        decoded = json.loads(value)
        if isinstance(decoded, dict):
            return decoded
    raise ValueError("pgvector metadata must be a JSON object")
