from __future__ import annotations

from typing import Sequence
from uuid import NAMESPACE_URL, UUID, uuid5

from .models import (
    Chunk,
    Citation,
    CitationValidation,
    SearchHit,
)


class CitationService:
    def build(
        self,
        hits: Sequence[SearchHit],
        *,
        selected_chunk_ids: Sequence[str] = (),
        limit: int = 3,
    ) -> tuple[Citation, ...]:
        selected = set(selected_chunk_ids)
        eligible = (
            [hit for hit in hits if hit.chunk.chunk_id in selected]
            if selected
            else list(hits)
        )
        citations: list[Citation] = []
        for hit in eligible[:limit]:
            chunk = hit.chunk
            citation_id = str(
                uuid5(
                    NAMESPACE_URL,
                    (
                        f"{chunk.document_id}:{chunk.document_version}:"
                        f"{chunk.chunk_id}"
                    ),
                )
            )
            citations.append(
                Citation(
                    citation_id=citation_id,
                    document_id=chunk.document_id,
                    document_version=chunk.document_version,
                    chunk_id=chunk.chunk_id,
                    title=chunk.title,
                    source=chunk.source,
                    page=chunk.location.page,
                    score=round(max(0.0, min(1.0, hit.score)), 6),
                    excerpt=chunk.content[:1000],
                )
            )
        return tuple(citations)

    def validate(
        self,
        citations: Sequence[Citation],
        available_chunks: Sequence[Chunk],
    ) -> CitationValidation:
        chunks = {chunk.chunk_id: chunk for chunk in available_chunks}
        errors: list[str] = []
        seen: set[str] = set()
        for citation in citations:
            try:
                UUID(citation.citation_id)
                UUID(citation.document_id)
            except ValueError:
                errors.append(f"invalid_uuid:{citation.chunk_id}")
            if citation.citation_id in seen:
                errors.append(f"duplicate_citation:{citation.citation_id}")
            seen.add(citation.citation_id)
            chunk = chunks.get(citation.chunk_id)
            if chunk is None:
                errors.append(f"missing_chunk:{citation.chunk_id}")
                continue
            if citation.document_id != chunk.document_id:
                errors.append(f"document_mismatch:{citation.chunk_id}")
            if citation.document_version != chunk.document_version:
                errors.append(f"version_mismatch:{citation.chunk_id}")
            if citation.source != chunk.source or citation.title != chunk.title:
                errors.append(f"location_mismatch:{citation.chunk_id}")
            if citation.page != chunk.location.page:
                errors.append(f"page_mismatch:{citation.chunk_id}")
            if not 0.0 <= citation.score <= 1.0:
                errors.append(f"invalid_score:{citation.chunk_id}")
        return CitationValidation(valid=not errors, errors=tuple(errors))
