"""Vector-store and fixed-embedding adapters."""

from .fake import FixedEmbedding, InMemoryVectorStore

__all__ = ["FixedEmbedding", "InMemoryVectorStore"]
