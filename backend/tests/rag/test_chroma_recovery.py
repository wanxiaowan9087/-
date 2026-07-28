from __future__ import annotations

import unittest

from backend.app.adapters.vector.chroma import ChromaVectorStore, _metadata
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import Chunk, DocumentType, SourceLocation


class _PersistedCollection:
    def __init__(self, chunks: tuple[Chunk, ...]) -> None:
        self._chunks = chunks

    def get(self, *, include: list[str]) -> dict[str, list[object]]:
        assert include == ["documents", "metadatas"]
        return {
            "documents": [chunk.content for chunk in self._chunks],
            "metadatas": [_metadata(chunk) for chunk in self._chunks],
        }


class ChromaRecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_rebuilds_lexical_index_from_persisted_chroma_chunks(self) -> None:
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
        vector_store = ChromaVectorStore(_PersistedCollection((chunk,)))

        recovered = vector_store.load_all_chunks()
        results = await BM25KeywordIndex(recovered).search("charging contacts", 3)

        self.assertEqual(recovered, (chunk,))
        self.assertEqual([result.chunk.chunk_id for result in results], [chunk.chunk_id])
