from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Any

from backend.app.rag.ports import EmbeddingVector


class DashScopeEmbeddingAdapter:
    """Moves the synchronous LangChain DashScope embedding client behind a port."""

    def __init__(self, embeddings: Any) -> None:
        self._embeddings = embeddings

    async def embed_documents(self, texts: Sequence[str]) -> Sequence[EmbeddingVector]:
        vectors = await asyncio.to_thread(self._embeddings.embed_documents, list(texts))
        return tuple(tuple(float(value) for value in vector) for vector in vectors)

    async def embed_query(self, text: str) -> EmbeddingVector:
        vector = await asyncio.to_thread(self._embeddings.embed_query, text)
        return tuple(float(value) for value in vector)
