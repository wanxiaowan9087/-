from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.app.core.errors import AppError
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.models import DocumentRecord, DocumentType
from backend.app.schemas.resources import KnowledgeFile


class KnowledgeCatalog:
    """Admin-only catalog over durable uploaded documents and their indexer."""

    def __init__(self, uploads_dir: str, indexer: KnowledgeIndexer | None = None) -> None:
        self._root = Path(uploads_dir) / "knowledge"
        self._indexer = indexer

    async def list_files(self) -> list[KnowledgeFile]:
        if not self._root.exists():
            return []
        files: list[KnowledgeFile] = []
        for path in sorted(
            self._root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True
        ):
            if not path.is_file() or path.suffix.lower() not in {".txt", ".md", ".markdown"}:
                continue
            stat = path.stat()
            content = path.read_text(encoding="utf-8", errors="ignore")
            document_type = (
                DocumentType.MARKDOWN
                if path.suffix.lower() in {".md", ".markdown"}
                else DocumentType.TEXT
            )
            document_id = str(uuid5(NAMESPACE_URL, f"knowledge-upload:{path.name}"))
            chunk_count = len(
                DocumentChunker().split(
                    DocumentRecord(
                        document_id=document_id,
                        title=path.stem.rsplit("-", 1)[0] or path.stem,
                        source=f"file://uploads/knowledge/{path.name}",
                        document_type=document_type,
                        content=content,
                    )
                )
            )
            files.append(KnowledgeFile(
                id=document_id,
                filename=path.name,
                title=path.stem.rsplit("-", 1)[0] or path.stem,
                source=f"file://uploads/knowledge/{path.name}",
                size_bytes=stat.st_size,
                chunk_count=chunk_count,
                uploaded_at=datetime.fromtimestamp(stat.st_mtime, UTC),
            ))
        return files

    async def reindex_all(self) -> int:
        if self._indexer is None:
            raise AppError(
                "KNOWLEDGE_INDEX_UNAVAILABLE",
                "knowledge index is not configured",
                503,
                {"retryable": True},
            )
        count = 0
        for item in await self.list_files():
            path = self._root / item.filename
            content = path.read_text(encoding="utf-8")
            document = DocumentRecord(
                document_id=item.id,
                title=item.title,
                source=item.source,
                document_type=(
                    DocumentType.MARKDOWN
                    if path.suffix.lower() in {".md", ".markdown"}
                    else DocumentType.TEXT
                ),
                content=content,
            )
            report = await self._indexer.ingest(document)
            count += report.chunks_indexed
        return count
