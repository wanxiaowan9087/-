from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.app.core.errors import AppError
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import tokenize
from backend.app.rag.parsers import SUPPORTED_SUFFIXES, parse_knowledge_payload
from backend.app.schemas.resources import KnowledgeFile


class KnowledgeCatalog:
    """Admin-only catalog over durable uploaded documents and their indexer."""

    def __init__(self, uploads_dir: str, indexer: KnowledgeIndexer | None = None) -> None:
        self._root = Path(uploads_dir) / "knowledge"
        self._indexer = indexer
        self._manifest_path = self._root / "knowledge_manifest.json"

    def _read_manifest(self) -> dict[str, dict[str, object]]:
        if not self._manifest_path.exists():
            return {}
        try:
            payload = json.loads(self._manifest_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_manifest(self, manifest: dict[str, dict[str, object]]) -> None:
        self._root.mkdir(parents=True, exist_ok=True)
        handle, temporary = tempfile.mkstemp(prefix="knowledge-manifest-", suffix=".tmp", dir=self._root)
        try:
            with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as stream:
                json.dump(manifest, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
            os.replace(temporary, self._manifest_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def normalize_filename(filename: str) -> str:
        return Path(filename).name.strip().casefold()

    async def find_existing(self, filename: str, sha256: str) -> tuple[str, KnowledgeFile] | None:
        normalized = self.normalize_filename(filename)
        same_name: KnowledgeFile | None = None
        for item in await self.list_files():
            if item.sha256 == sha256:
                return "duplicate", item
            original = self.normalize_filename(item.original_filename or item.filename)
            if original == normalized:
                same_name = item
        return ("conflict", same_name) if same_name is not None else None

    async def find_similar(self, text: str, *, threshold: float = 0.82) -> tuple[KnowledgeFile, float] | None:
        candidate_tokens = set(tokenize(text))
        if len(candidate_tokens) < 8:
            return None
        best: tuple[KnowledgeFile, float] | None = None
        for item in await self.list_files():
            path = self._root / item.filename
            if path.exists():
                try:
                    candidate_document = parse_knowledge_payload(
                        item.filename,
                        path.read_bytes(),
                        document_id=item.id,
                        title=item.title,
                        source=item.source,
                    )
                    candidate_text = candidate_document.content
                except (ValueError, RuntimeError):
                    continue
            else:
                continue
            tokens = set(tokenize(candidate_text))
            score = len(candidate_tokens & tokens) / max(1, len(candidate_tokens | tokens))
            if score >= threshold and (best is None or score > best[1]):
                best = (item, score)
        if self._indexer is not None:
            try:
                indexed = await self._indexer.indexed_documents()
            except Exception:
                indexed = ()
            known_ids = {item.id for item in await self.list_files()}
            for document, chunk_count, version in indexed:
                if document.document_id in known_ids:
                    continue
                tokens = set(tokenize(document.content))
                score = len(candidate_tokens & tokens) / max(1, len(candidate_tokens | tokens))
                if score >= threshold:
                    item = KnowledgeFile(
                        id=document.document_id,
                        filename=Path(document.source).name or f"{document.document_id}.md",
                        title=document.title,
                        source=document.source,
                        size_bytes=max(1, len(document.content.encode("utf-8"))),
                        chunk_count=chunk_count,
                        uploaded_at=datetime.now(UTC),
                        original_filename=Path(document.source).name or document.title,
                        sha256=None,
                        ingest_status="indexed",
                        document_version=version,
                        last_indexed_at=datetime.now(UTC),
                    )
                    if best is None or score > best[1]:
                        best = (item, score)
        return best

    async def register(self, item: KnowledgeFile) -> None:
        manifest = self._read_manifest()
        manifest[item.filename] = {
            "id": item.id,
            "original_filename": item.original_filename or item.filename,
            "title": item.title,
            "source": item.source,
            "size_bytes": item.size_bytes,
            "sha256": item.sha256,
            "ingest_status": item.ingest_status,
            "document_version": item.document_version,
            "chunk_count": item.chunk_count,
            "uploaded_at": item.uploaded_at.isoformat(),
            "last_indexed_at": item.last_indexed_at.isoformat() if item.last_indexed_at else None,
        }
        self._write_manifest(manifest)

    async def get_file(self, document_id: str) -> KnowledgeFile | None:
        return next((item for item in await self.list_files() if item.id == document_id), None)

    async def update_file(
        self,
        document_id: str,
        *,
        title: str | None = None,
        original_filename: str | None = None,
    ) -> KnowledgeFile:
        item = await self.get_file(document_id)
        if item is None:
            raise AppError("KNOWLEDGE_FILE_NOT_FOUND", "知识文档不存在", 404)
        updated = item.model_copy(update={
            "title": title.strip() if title is not None else item.title,
            "original_filename": original_filename.strip() if original_filename is not None else item.original_filename,
        })
        if not updated.title:
            raise AppError("INVALID_KNOWLEDGE_METADATA", "文档标题不能为空", 400)
        if self._indexer is not None and title is not None:
            path = self._root / item.filename
            if not path.exists():
                raise AppError("KNOWLEDGE_SOURCE_MISSING", "文档源文件不存在，无法重新索引", 409)
            try:
                document = parse_knowledge_payload(item.filename, path.read_bytes(), document_id=item.id, title=updated.title, source=item.source)
                report = await self._indexer.ingest(document)
            except (ValueError, RuntimeError) as error:
                raise AppError("KNOWLEDGE_INDEXING_FAILED", str(error), 503, {"retryable": True}) from error
            updated = updated.model_copy(update={
                "chunk_count": report.chunks_indexed,
                "document_version": report.document_version,
                "ingest_status": "indexed",
                "last_indexed_at": datetime.now(UTC),
            })
        await self.register(updated)
        return updated

    async def delete_file(self, document_id: str) -> KnowledgeFile:
        item = await self.get_file(document_id)
        if item is None:
            raise AppError("KNOWLEDGE_FILE_NOT_FOUND", "知识文档不存在", 404)
        if self._indexer is not None:
            await self._indexer.delete_document(document_id)
        (self._root / item.filename).unlink(missing_ok=True)
        self.remove(item.filename)
        return item

    def remove(self, filename: str) -> None:
        manifest = self._read_manifest()
        if filename in manifest:
            del manifest[filename]
            self._write_manifest(manifest)

    async def list_files(self) -> list[KnowledgeFile]:
        if not self._root.exists():
            return []
        files: list[KnowledgeFile] = []
        manifest = self._read_manifest()
        for path in sorted(
            self._root.iterdir(), key=lambda item: item.stat().st_mtime, reverse=True
        ):
            if not path.is_file() or path.suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            stat = path.stat()
            payload = path.read_bytes()
            digest = hashlib.sha256(payload).hexdigest()
            metadata = manifest.get(path.name, {})
            document_id = str(metadata.get("id") or uuid5(NAMESPACE_URL, f"knowledge-upload:{path.name}"))
            title = str(metadata.get("title") or path.stem.rsplit("-", 1)[0] or path.stem)
            try:
                document = parse_knowledge_payload(path.name, payload, document_id=document_id, title=title, source=f"file://uploads/knowledge/{path.name}")
                chunk_count = len(DocumentChunker().split(document))
            except (ValueError, RuntimeError):
                chunk_count = int(metadata.get("chunk_count") or 0)
            uploaded_at = metadata.get("uploaded_at")
            try:
                uploaded = datetime.fromisoformat(str(uploaded_at)) if uploaded_at else datetime.fromtimestamp(stat.st_mtime, UTC)
            except ValueError:
                uploaded = datetime.fromtimestamp(stat.st_mtime, UTC)
            last_indexed_at = metadata.get("last_indexed_at")
            try:
                indexed = datetime.fromisoformat(str(last_indexed_at)) if last_indexed_at else None
            except ValueError:
                indexed = None
            raw_status = str(metadata.get("ingest_status") or "indexed")
            ingest_status = raw_status if raw_status in {"indexed", "local", "error"} else "error"
            files.append(KnowledgeFile(
                id=document_id,
                filename=path.name,
                title=title,
                source=f"file://uploads/knowledge/{path.name}",
                size_bytes=stat.st_size,
                chunk_count=int(metadata.get("chunk_count") or chunk_count),
                uploaded_at=uploaded,
                original_filename=str(metadata.get("original_filename") or path.stem.rsplit("-", 1)[0] + path.suffix),
                sha256=str(metadata.get("sha256") or digest),
                ingest_status=ingest_status,
                document_version=str(metadata.get("document_version")) if metadata.get("document_version") else None,
                last_indexed_at=indexed,
            ))
        known_ids = {item.id for item in files}
        known_filenames = {item.filename for item in files}
        for filename, metadata in manifest.items():
            if filename in known_filenames:
                continue
            document_id = str(metadata.get("id") or uuid5(NAMESPACE_URL, f"knowledge-upload:{filename}"))
            uploaded_at = metadata.get("uploaded_at")
            try:
                uploaded = datetime.fromisoformat(str(uploaded_at)) if uploaded_at else datetime.now(UTC)
            except ValueError:
                uploaded = datetime.now(UTC)
            last_indexed_at = metadata.get("last_indexed_at")
            try:
                indexed = datetime.fromisoformat(str(last_indexed_at)) if last_indexed_at else None
            except ValueError:
                indexed = None
            raw_status = str(metadata.get("ingest_status") or "indexed")
            ingest_status = raw_status if raw_status in {"indexed", "local", "error"} else "error"
            files.append(KnowledgeFile(
                id=document_id,
                filename=filename,
                title=str(metadata.get("title") or Path(filename).stem),
                source=str(metadata.get("source") or f"file://uploads/knowledge/{filename}"),
                size_bytes=max(1, int(metadata.get("size_bytes") or 1)),
                chunk_count=max(0, int(metadata.get("chunk_count") or 0)),
                uploaded_at=uploaded,
                original_filename=str(metadata.get("original_filename") or filename),
                sha256=str(metadata.get("sha256")) if metadata.get("sha256") else None,
                ingest_status=ingest_status,
                document_version=str(metadata.get("document_version")) if metadata.get("document_version") else None,
                last_indexed_at=indexed,
            ))
            known_ids.add(document_id)
        if self._indexer is not None:
            try:
                indexed_documents = await self._indexer.indexed_documents()
            except Exception:
                indexed_documents = ()
            for document, chunk_count, version in indexed_documents:
                if document.document_id in known_ids:
                    continue
                source_name = Path(document.source).name or f"{document.document_id}.md"
                files.append(KnowledgeFile(
                    id=document.document_id,
                    filename=source_name,
                    title=document.title,
                    source=document.source,
                    size_bytes=max(1, len(document.content.encode("utf-8"))),
                    chunk_count=chunk_count,
                    uploaded_at=datetime.now(UTC),
                    original_filename=source_name,
                    sha256=None,
                    ingest_status="indexed",
                    document_version=version,
                    last_indexed_at=datetime.now(UTC),
                ))
        return sorted(files, key=lambda item: item.uploaded_at, reverse=True)

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
            document = parse_knowledge_payload(
                item.filename,
                path.read_bytes(),
                document_id=item.id,
                title=item.title,
                source=item.source,
            )
            report = await self._indexer.ingest(document)
            count += report.chunks_indexed
            await self.register(item.model_copy(update={
                "chunk_count": report.chunks_indexed,
                "document_version": report.document_version,
                "last_indexed_at": datetime.now(UTC),
                "ingest_status": "indexed",
            }))
        return count
