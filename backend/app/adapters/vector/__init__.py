"""Vector-store and fixed-embedding adapters."""

from .dashscope import DashScopeEmbeddingAdapter
from .fake import FixedEmbedding, InMemoryVectorStore
from .pgvector_store import PgVectorStore

__all__ = ["DashScopeEmbeddingAdapter", "FixedEmbedding", "InMemoryVectorStore", "PgVectorStore"]
