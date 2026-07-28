from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from uuid import UUID


class DocumentType(StrEnum):
    FAQ = "faq"
    MARKDOWN = "markdown"
    TEXT = "text"
    PDF = "pdf"
    TABLE = "table"


@dataclass(frozen=True)
class DocumentRecord:
    document_id: str
    title: str
    source: str
    document_type: DocumentType
    content: str = ""
    version: str | None = None
    pages: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        UUID(self.document_id)
        if not self.title.strip() or not self.source.strip():
            raise ValueError("document title and source are required")
        if self.document_type is DocumentType.PDF:
            if not self.pages and not self.content.strip():
                raise ValueError("PDF documents require content or pages")
        elif not self.content.strip():
            raise ValueError("document content is required")
        if self.version is not None and not self.version.strip():
            raise ValueError("document version must not be blank")


@dataclass(frozen=True)
class SourceLocation:
    section: str | None = None
    page: int | None = None
    row: int | None = None

    def __post_init__(self) -> None:
        if self.page is not None and self.page < 1:
            raise ValueError("page must be positive")
        if self.row is not None and self.row < 1:
            raise ValueError("row must be positive")

    def label(self) -> str:
        parts: list[str] = []
        if self.section:
            parts.append(self.section)
        if self.page is not None:
            parts.append(f"page:{self.page}")
        if self.row is not None:
            parts.append(f"row:{self.row}")
        return "|".join(parts) or "document"


@dataclass(frozen=True)
class Chunk:
    document_id: str
    document_version: str
    chunk_id: str
    title: str
    source: str
    content: str
    document_type: DocumentType
    location: SourceLocation = SourceLocation()
    metadata: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        UUID(self.document_id)
        if not self.document_version.strip():
            raise ValueError("chunk document_version is required")
        if not self.chunk_id.strip() or len(self.chunk_id) > 256:
            raise ValueError("chunk_id must contain 1-256 characters")
        if not self.content.strip():
            raise ValueError("chunk content is required")


@dataclass(frozen=True)
class ScoredChunk:
    chunk: Chunk
    score: float

    def __post_init__(self) -> None:
        if not 0.0 <= self.score <= 1.0:
            raise ValueError("search score must be normalized to [0, 1]")


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    vector_score: float | None
    keyword_score: float | None
    fused_score: float
    rerank_score: float | None = None

    @property
    def score(self) -> float:
        return (
            self.rerank_score
            if self.rerank_score is not None
            else self.fused_score
        )

    @property
    def modalities(self) -> int:
        return int(self.vector_score is not None) + int(
            self.keyword_score is not None
        )


@dataclass(frozen=True)
class RetrievalResult:
    hits: tuple[SearchHit, ...]
    confidence: float
    strategy: str = "vector+bm25+rrf+rerank"
    degraded_dependencies: tuple[str, ...] = ()
    conflicting_sources: bool = False

    @property
    def has_evidence(self) -> bool:
        return bool(self.hits) and self.confidence > 0.0


@dataclass(frozen=True)
class Citation:
    citation_id: str
    document_id: str
    document_version: str
    chunk_id: str
    title: str
    source: str
    score: float
    page: int | None = None
    excerpt: str | None = None


@dataclass(frozen=True)
class CitationValidation:
    valid: bool
    errors: tuple[str, ...] = ()
