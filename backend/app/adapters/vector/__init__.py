"""Vector-store and fixed-embedding adapters."""

from .dashscope import DashScopeEmbeddingAdapter
from .fake import FixedEmbedding, InMemoryVectorStore

__all__ = ["DashScopeEmbeddingAdapter", "FixedEmbedding", "InMemoryVectorStore"]
