from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
from typing import Any
from uuid import NAMESPACE_URL, uuid5

from backend.app.adapters.vector.chroma import ChromaVectorStore
from backend.app.adapters.vector.dashscope import DashScopeEmbeddingAdapter
from backend.app.core.config import Settings
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import DocumentRecord, DocumentType


class ManifestError(ValueError):
    """A source manifest cannot be turned into a traceable knowledge record."""


def parse_manifest(text: str) -> tuple[DocumentRecord, ...]:
    documents: list[DocumentRecord] = []
    for line_number, line in enumerate(text.splitlines(), 1):
        if not line.strip():
            continue
        try:
            raw: Any = json.loads(line)
        except json.JSONDecodeError as error:
            raise ManifestError(f"line {line_number}: invalid JSON") from error
        if not isinstance(raw, dict):
            raise ManifestError(f"line {line_number}: expected an object")
        try:
            source = _required_text(raw, "source")
            title = _required_text(raw, "title")
            document_id = _optional_text(raw, "document_id") or str(
                uuid5(NAMESPACE_URL, source)
            )
            document_type = DocumentType(raw.get("document_type", "text"))
            content = _optional_text(raw, "content") or ""
            pages = _string_tuple(raw.get("pages"), line_number, "pages")
            metadata = raw.get("metadata", {})
            if not isinstance(metadata, dict):
                raise ManifestError(f"line {line_number}: metadata must be an object")
            documents.append(
                DocumentRecord(
                    document_id=document_id,
                    title=title,
                    source=source,
                    document_type=document_type,
                    content=content,
                    version=_optional_text(raw, "version"),
                    pages=pages,
                    metadata=metadata,
                )
            )
        except (TypeError, ValueError) as error:
            if isinstance(error, ManifestError):
                raise
            raise ManifestError(f"line {line_number}: {error}") from error
    if not documents:
        raise ManifestError("manifest must contain at least one document")
    return tuple(documents)


def _required_text(raw: dict[str, Any], field: str) -> str:
    value = _optional_text(raw, field)
    if value is None:
        raise ManifestError(f"{field} is required")
    return value


def _optional_text(raw: dict[str, Any], field: str) -> str | None:
    value = raw.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ManifestError(f"{field} must be a non-empty string when supplied")
    return value


def _string_tuple(value: Any, line_number: int, field: str) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list) or not all(
        isinstance(item, str) and item.strip() for item in value
    ):
        raise ManifestError(f"line {line_number}: {field} must be a list of non-empty strings")
    return tuple(value)


async def ingest(manifest: Path, settings: Settings) -> int:
    try:
        import chromadb
        from langchain_community.embeddings import DashScopeEmbeddings
    except ImportError as error:
        raise RuntimeError("install the project [ai] extra before ingesting knowledge") from error

    documents = parse_manifest(manifest.read_text(encoding="utf-8"))
    client = chromadb.PersistentClient(path=settings.agent_vector_store_path)
    collection = client.get_or_create_collection(
        name=settings.agent_vector_collection_name,
        metadata={"hnsw:space": "cosine"},
    )
    vector_store = ChromaVectorStore(collection)
    indexer = KnowledgeIndexer(
        DocumentChunker(),
        DashScopeEmbeddingAdapter(DashScopeEmbeddings(model=settings.agent_embedding_model_name)),
        vector_store,
        BM25KeywordIndex(vector_store.load_all_chunks()),
    )
    total_chunks = 0
    for document in documents:
        report = await indexer.ingest(document)
        total_chunks += report.chunks_indexed
        print(
            f"indexed {report.document_id}@{report.document_version}: "
            f"{report.chunks_indexed} chunks"
        )
    return total_chunks


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Ingest a versioned JSONL knowledge manifest into Chroma."
    )
    parser.add_argument(
        "--manifest", required=True, type=Path, help="UTF-8 JSONL knowledge manifest"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="validate the manifest without importing AI dependencies",
    )
    args = parser.parse_args()
    documents = parse_manifest(args.manifest.read_text(encoding="utf-8"))
    if args.dry_run:
        print(f"valid manifest: {len(documents)} documents")
        return
    total = asyncio.run(ingest(args.manifest, Settings()))
    print(f"completed: {len(documents)} documents, {total} chunks")


if __name__ == "__main__":
    main()
