from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from backend.app.adapters.vector.fake import FixedEmbedding, InMemoryVectorStore
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.knowledge_catalog import KnowledgeCatalog
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.schemas.resources import KnowledgeFile


@pytest.mark.asyncio
async def test_catalog_update_reindexes_and_delete_cleans_both_indexes(tmp_path: Path) -> None:
    vectors = InMemoryVectorStore()
    keywords = BM25KeywordIndex()
    indexer = KnowledgeIndexer(DocumentChunker(), FixedEmbedding(), vectors, keywords)
    catalog = KnowledgeCatalog(str(tmp_path), indexer)
    document_id = str(uuid4())
    payload = "型号 X9\n\n支持地毯清洁和自动回充。".encode()
    root = tmp_path / "knowledge"
    root.mkdir()
    filename = "x9-abc.md"
    (root / filename).write_bytes(payload)
    item = KnowledgeFile(
        id=document_id,
        filename=filename,
        title="X9 手册",
        source=f"file://uploads/knowledge/{filename}",
        size_bytes=len(payload),
        chunk_count=1,
        uploaded_at=datetime.now(UTC),
        original_filename="X9.md",
        sha256="a" * 64,
        ingest_status="indexed",
    )
    await catalog.register(item)
    from backend.app.rag.parsers import parse_knowledge_payload

    document = parse_knowledge_payload(filename, payload, document_id=document_id, title=item.title, source=item.source)
    await indexer.ingest(document)
    updated = await catalog.update_file(document_id, title="X9 使用手册")
    assert updated.title == "X9 使用手册"
    assert (await vectors.search(await FixedEmbedding().embed_query("地毯"), 5))
    await catalog.delete_file(document_id)
    assert not (root / filename).exists()
    assert not await vectors.search(await FixedEmbedding().embed_query("地毯"), 5)
    assert not await keywords.search("地毯", 5)
