from __future__ import annotations

import hashlib
import math
from typing import Sequence

from ...rag.lexical import tokenize
from ...rag.models import Chunk, ScoredChunk
from ...rag.ports import EmbeddingVector


class FixedEmbedding:
    """Stable hashing embedding. It never performs network I/O."""

    def __init__(self, dimensions: int = 256) -> None:
        if dimensions < 32:
            raise ValueError("fixed embedding dimensions must be at least 32")
        self.dimensions = dimensions

    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[EmbeddingVector]:
        return tuple(self._embed(text) for text in texts)

    async def embed_query(self, text: str) -> EmbeddingVector:
        return self._embed(text)

    def _embed(self, text: str) -> EmbeddingVector:
        vector = [0.0] * self.dimensions
        terms = tokenize(text)
        for term in terms:
            digest = hashlib.sha256(term.encode("utf-8")).digest()
            index = int.from_bytes(digest[:4], "big") % self.dimensions
            sign = 1.0 if digest[4] % 2 == 0 else -1.0
            vector[index] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return tuple(vector)
        return tuple(value / norm for value in vector)


class InMemoryVectorStore:
    """VectorStorePort adapter for deterministic tests and local demos."""

    def __init__(self) -> None:
        self._entries: dict[str, tuple[Chunk, EmbeddingVector]] = {}

    async def replace_document(
        self,
        document_id: str,
        document_version: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[EmbeddingVector],
    ) -> None:
        if len(chunks) != len(vectors):
            raise ValueError("chunk and vector counts differ")
        stale = [
            chunk_id
            for chunk_id, (chunk, _) in self._entries.items()
            if chunk.document_id == document_id
        ]
        for chunk_id in stale:
            del self._entries[chunk_id]
        for chunk, vector in zip(chunks, vectors):
            if chunk.document_version != document_version:
                raise ValueError("mixed document versions")
            self._entries[chunk.chunk_id] = (chunk, tuple(vector))

    async def search(
        self, query_vector: EmbeddingVector, limit: int
    ) -> Sequence[ScoredChunk]:
        ranked = [
            ScoredChunk(
                chunk=chunk,
                score=max(0.0, min(1.0, _cosine(query_vector, vector))),
            )
            for chunk, vector in self._entries.values()
        ]
        ranked = [item for item in ranked if item.score > 0.0]
        ranked.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(ranked[: max(0, limit)])

    async def resolve(self, chunk_ids: Sequence[str]) -> Sequence[Chunk]:
        return tuple(
            self._entries[chunk_id][0]
            for chunk_id in chunk_ids
            if chunk_id in self._entries
        )

    def snapshot(self) -> tuple[Chunk, ...]:
        return tuple(entry[0] for entry in self._entries.values())


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    if len(left) != len(right):
        raise ValueError("embedding dimensions differ")
    left_norm = math.sqrt(sum(value * value for value in left))
    right_norm = math.sqrt(sum(value * value for value in right))
    if not left_norm or not right_norm:
        return 0.0
    return sum(a * b for a, b in zip(left, right)) / (
        left_norm * right_norm
    )
