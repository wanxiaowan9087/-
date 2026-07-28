from __future__ import annotations

import json
from typing import Any, Sequence

from ...rag.models import (
    Chunk,
    DocumentType,
    ScoredChunk,
    SourceLocation,
)
from ...rag.ports import EmbeddingVector


class ChromaVectorStore:
    """Chroma adapter kept behind VectorStorePort.

    The collection is injected so lifecycle/configuration remains owned by the
    backend platform. It must provide Chroma's `get`, `delete`, `upsert`, and
    `query` methods.
    """

    def __init__(self, collection: Any) -> None:
        self._collection = collection

    async def replace_document(
        self,
        document_id: str,
        document_version: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[EmbeddingVector],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and vector counts differ")
        existing = self._collection.get(where={"document_id": document_id})
        existing_ids = existing.get("ids", []) if existing else []
        if existing_ids:
            self._collection.delete(ids=existing_ids)
        if not chunks:
            return
        self._collection.upsert(
            ids=[chunk.chunk_id for chunk in chunks],
            documents=[chunk.content for chunk in chunks],
            embeddings=[list(vector) for vector in vectors],
            metadatas=[_metadata(chunk) for chunk in chunks],
        )

    async def search(
        self, query_vector: EmbeddingVector, limit: int
    ) -> Sequence[ScoredChunk]:
        result = self._collection.query(
            query_embeddings=[list(query_vector)],
            n_results=limit,
            include=["documents", "metadatas", "distances"],
        )
        documents = _first(result.get("documents"))
        metadatas = _first(result.get("metadatas"))
        distances = _first(result.get("distances"))
        hits: list[ScoredChunk] = []
        for document, metadata, distance in zip(
            documents, metadatas, distances
        ):
            hits.append(
                ScoredChunk(
                    chunk=_chunk(document, metadata),
                    score=max(0.0, min(1.0, 1.0 - float(distance))),
                )
            )
        return tuple(hits)

    async def resolve(self, chunk_ids: Sequence[str]) -> Sequence[Chunk]:
        if not chunk_ids:
            return ()
        result = self._collection.get(
            ids=list(chunk_ids), include=["documents", "metadatas"]
        )
        documents = result.get("documents", [])
        metadatas = result.get("metadatas", [])
        return tuple(
            _chunk(document, metadata)
            for document, metadata in zip(documents, metadatas)
        )

    def load_all_chunks(self) -> tuple[Chunk, ...]:
        """Rebuild an in-process lexical index from durable Chroma records.

        Chroma owns vector persistence, while BM25 deliberately stays a small,
        deterministic in-process implementation. Loading its source chunks at
        bootstrap prevents a process restart from silently degrading hybrid
        retrieval to vector-only retrieval.
        """

        result = self._collection.get(include=["documents", "metadatas"])
        documents = result.get("documents", []) if result else []
        metadatas = result.get("metadatas", []) if result else []
        return tuple(
            _chunk(document, metadata)
            for document, metadata in zip(documents, metadatas)
        )


def _metadata(chunk: Chunk) -> dict[str, Any]:
    safe_custom = {
        str(key): value
        for key, value in chunk.metadata.items()
        if isinstance(value, (str, int, float, bool)) or value is None
    }
    return {
        "document_id": chunk.document_id,
        "document_version": chunk.document_version,
        "chunk_id": chunk.chunk_id,
        "title": chunk.title,
        "source": chunk.source,
        "document_type": chunk.document_type.value,
        "section": chunk.location.section or "",
        "page": chunk.location.page or 0,
        "row": chunk.location.row or 0,
        "custom_json": json.dumps(safe_custom, ensure_ascii=False),
    }


def _chunk(document: str, metadata: dict[str, Any]) -> Chunk:
    custom = json.loads(metadata.get("custom_json", "{}"))
    return Chunk(
        document_id=metadata["document_id"],
        document_version=metadata["document_version"],
        chunk_id=metadata["chunk_id"],
        title=metadata["title"],
        source=metadata["source"],
        content=document,
        document_type=DocumentType(metadata["document_type"]),
        location=SourceLocation(
            section=metadata.get("section") or None,
            page=int(metadata.get("page") or 0) or None,
            row=int(metadata.get("row") or 0) or None,
        ),
        metadata=custom,
    )


def _first(value: Any) -> list:
    if isinstance(value, list) and value and isinstance(value[0], list):
        return value[0]
    return value or []
