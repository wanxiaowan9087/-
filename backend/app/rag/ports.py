from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from .models import Chunk, ScoredChunk, SearchHit

EmbeddingVector = tuple[float, ...]


class EmbeddingPort(Protocol):
    async def embed_documents(
        self, texts: Sequence[str]
    ) -> Sequence[EmbeddingVector]: ...

    async def embed_query(self, text: str) -> EmbeddingVector: ...


class VectorStorePort(Protocol):
    """Chroma and in-memory adapters satisfy this seam."""

    async def replace_document(
        self,
        document_id: str,
        document_version: str,
        chunks: Sequence[Chunk],
        vectors: Sequence[EmbeddingVector],
    ) -> None: ...

    async def search(
        self, query_vector: EmbeddingVector, limit: int
    ) -> Sequence[ScoredChunk]: ...

    async def resolve(self, chunk_ids: Sequence[str]) -> Sequence[Chunk]: ...


class KeywordSearchPort(Protocol):
    async def replace_document(
        self,
        document_id: str,
        document_version: str,
        chunks: Sequence[Chunk],
    ) -> None: ...

    async def search(self, query: str, limit: int) -> Sequence[ScoredChunk]: ...


class RerankerPort(Protocol):
    async def rerank(
        self, query: str, hits: Sequence[SearchHit], limit: int
    ) -> Sequence[SearchHit]: ...


class RetrieverPort(Protocol):
    async def retrieve(self, query: str) -> RetrievalResult: ...


from .models import RetrievalResult  # noqa: E402
