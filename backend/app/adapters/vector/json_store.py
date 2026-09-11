from __future__ import annotations

import asyncio
import json
import math
from collections.abc import Sequence
from pathlib import Path

from backend.app.rag.models import Chunk, DocumentType, ScoredChunk, SourceLocation
from backend.app.rag.ports import EmbeddingVector


class JsonVectorStore:
    """Small durable vector adapter for a single-node application deployment.

    It keeps the VectorStorePort interface used by the hybrid retriever while
    avoiding a network-facing vector database process. The file is atomically
    replaced after document ingestion so a restart can rebuild retrieval and
    lexical state from the same durable source.
    """

    def __init__(self, path: str) -> None:
        self._path = Path(path)
        self._records: dict[str, tuple[Chunk, EmbeddingVector]] = {}
        self._lock = asyncio.Lock()
        self._reload()

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
        async with self._lock:
            self._reload()
            self._records = {
                chunk_id: record
                for chunk_id, record in self._records.items()
                if record[0].document_id != document_id
            }
            replacements = {
                chunk.chunk_id: (chunk, vector)
                for chunk, vector in zip(chunks, vectors, strict=True)
            }
            self._records.update(replacements)
            self._persist()

    async def search(
        self, query_vector: EmbeddingVector, limit: int
    ) -> Sequence[ScoredChunk]:
        if limit <= 0:
            return ()
        self._reload()
        ranked = sorted(
            (
                ScoredChunk(chunk=chunk, score=_cosine(query_vector, vector))
                for chunk, vector in self._records.values()
            ),
            key=lambda item: item.score,
            reverse=True,
        )
        return tuple(ranked[:limit])

    async def resolve(self, chunk_ids: Sequence[str]) -> Sequence[Chunk]:
        self._reload()
        return tuple(
            self._records[chunk_id][0]
            for chunk_id in chunk_ids
            if chunk_id in self._records
        )

    async def delete_document(self, document_id: str) -> None:
        async with self._lock:
            self._reload()
            self._records = {
                chunk_id: record
                for chunk_id, record in self._records.items()
                if record[0].document_id != document_id
            }
            self._persist()

    def load_all_chunks(self) -> tuple[Chunk, ...]:
        self._reload()
        return tuple(chunk for chunk, _ in self._records.values())

    def load_all_records(self) -> tuple[tuple[Chunk, EmbeddingVector], ...]:
        """Expose durable records for a one-way pgvector migration backup import."""
        self._reload()
        return tuple(self._records.values())

    def _reload(self) -> None:
        if not self._path.exists():
            return
        raw = json.loads(self._path.read_text(encoding="utf-8"))
        records = raw.get("records", {})
        if not isinstance(records, dict):
            raise ValueError("invalid vector store records")
        loaded: dict[str, tuple[Chunk, EmbeddingVector]] = {}
        for chunk_id, item in records.items():
            if not isinstance(chunk_id, str) or not isinstance(item, dict):
                raise ValueError("invalid vector store record")
            chunk = _deserialize_chunk(item["chunk"])
            vector = tuple(float(value) for value in item["vector"])
            loaded[chunk_id] = (chunk, vector)
        self._records = loaded

    def _persist(self) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format": "agent-json-vector-store-v1",
            "records": {
                chunk_id: {"chunk": _serialize_chunk(chunk), "vector": list(vector)}
                for chunk_id, (chunk, vector) in self._records.items()
            },
        }
        temporary = self._path.with_suffix(f"{self._path.suffix}.tmp")
        temporary.write_text(
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
        )
        temporary.replace(self._path)


def _cosine(left: EmbeddingVector, right: EmbeddingVector) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions differ")
    denominator = math.sqrt(sum(value * value for value in left)) * math.sqrt(
        sum(value * value for value in right)
    )
    if denominator == 0:
        return 0.0
    similarity = (
        sum(first * second for first, second in zip(left, right, strict=True)) / denominator
    )
    return max(0.0, min(1.0, (similarity + 1.0) / 2.0))


def _serialize_chunk(chunk: Chunk) -> dict[str, object]:
    return {
        "document_id": chunk.document_id,
        "document_version": chunk.document_version,
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
        "source": chunk.source,
        "content": chunk.content,
        "document_type": chunk.document_type.value,
        "location": {
            "section": chunk.location.section,
            "page": chunk.location.page,
            "row": chunk.location.row,
        },
        "metadata": chunk.metadata,
    }


def _deserialize_chunk(value: object) -> Chunk:
    if not isinstance(value, dict):
        raise ValueError("invalid stored chunk")
    location = value.get("location")
    if not isinstance(location, dict):
        raise ValueError("invalid stored chunk location")
    metadata = value.get("metadata", {})
    if not isinstance(metadata, dict):
        raise ValueError("invalid stored chunk metadata")
    return Chunk(
        document_id=str(value["document_id"]),
        document_version=str(value["document_version"]),
        chunk_id=str(value["chunk_id"]),
        title=str(value["title"]),
        source=str(value["source"]),
        content=str(value["content"]),
        document_type=DocumentType(str(value["document_type"])),
        location=SourceLocation(
            section=location.get("section") if isinstance(location.get("section"), str) else None,
            page=location.get("page") if isinstance(location.get("page"), int) else None,
            row=location.get("row") if isinstance(location.get("row"), int) else None,
        ),
        metadata=metadata,
    )
