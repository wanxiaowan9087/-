from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from backend.app.adapters.vector.json_store import JsonVectorStore
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import Chunk, DocumentType, SourceLocation


class JsonVectorRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rebuilds_lexical_index_from_durable_chunks(self) -> None:
        chunk = Chunk(
            document_id="b908216a-ae09-4a97-93d5-7e39bb910f93",
            document_version="v3",
            chunk_id="b908216a-ae09-4a97-93d5-7e39bb910f93:v3:0001",
            title="Maintenance guide",
            source="kb://maintenance",
            content="Clean the charging contacts before retrying the dock.",
            document_type=DocumentType.FAQ,
            location=SourceLocation(section="3.2"),
        )
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "vectors.json")
            store = JsonVectorStore(path)
            await store.replace_document(
                chunk.document_id, chunk.document_version, (chunk,), ((1.0, 0.0),)
            )
            recovered = JsonVectorStore(path).load_all_chunks()

        results = await BM25KeywordIndex(recovered).search("charging contacts", 3)
        self.assertEqual(recovered, (chunk,))
        self.assertEqual([result.chunk.chunk_id for result in results], [chunk.chunk_id])

    async def test_scores_durable_vectors_with_cosine_similarity(self) -> None:
        chunk = Chunk(
            document_id="a9e52d60-6d68-4a75-9f1f-b8d1c8cc0f1b",
            document_version="v1",
            chunk_id="a9e52d60-6d68-4a75-9f1f-b8d1c8cc0f1b:v1:0001",
            title="Guide",
            source="kb://guide",
            content="content",
            document_type=DocumentType.TEXT,
            location=SourceLocation(),
        )
        with tempfile.TemporaryDirectory() as directory:
            store = JsonVectorStore(str(Path(directory) / "vectors.json"))
            await store.replace_document(
                chunk.document_id, "v1", (chunk,), ((1.0, 0.0),)
            )
            results = await store.search((1.0, 0.0), 1)

        self.assertEqual(results[0].chunk, chunk)
        self.assertEqual(results[0].score, 1.0)
