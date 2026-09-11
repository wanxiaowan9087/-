from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass

from .chunking import DocumentChunker
from .models import Chunk, DocumentRecord
from .ports import EmbeddingPort, KeywordSearchPort, VectorStorePort


@dataclass(frozen=True)
class IngestionReport:
    document_id: str
    document_version: str
    chunks_indexed: int


class IndexingError(RuntimeError):
    def __init__(self, stage: str) -> None:
        super().__init__(f"knowledge indexing failed at {stage}")
        self.stage = stage


class KnowledgeIndexer:
    """Idempotently replaces one document version in both retrieval indexes."""

    def __init__(
        self,
        chunker: DocumentChunker,
        embeddings: EmbeddingPort,
        vector_store: VectorStorePort,
        keyword_index: KeywordSearchPort,
    ) -> None:
        self._chunker = chunker
        self._embeddings = embeddings
        self._vector_store = vector_store
        self._keyword_index = keyword_index

    async def ingest(self, document: DocumentRecord) -> IngestionReport:
        chunks = self._chunker.split(document)
        version = chunks[0].document_version
        try:
            vectors = tuple(
                await self._embeddings.embed_documents([chunk.content for chunk in chunks])
            )
        except Exception as error:
            raise IndexingError("embedding") from error
        if len(vectors) != len(chunks):
            raise IndexingError("embedding_count")
        try:
            await self._vector_store.replace_document(
                document.document_id, version, chunks, vectors
            )
        except Exception as error:
            raise IndexingError("vector_store") from error
        try:
            await self._keyword_index.replace_document(document.document_id, version, chunks)
        except Exception as error:
            raise IndexingError("keyword_index") from error
        return IngestionReport(
            document_id=document.document_id,
            document_version=version,
            chunks_indexed=len(chunks),
        )

    async def delete_document(self, document_id: str) -> None:
        await self._vector_store.delete_document(document_id)
        await self._keyword_index.delete_document(document_id)

    async def indexed_documents(self) -> tuple[tuple[DocumentRecord, int, str], ...]:
        """Return durable documents currently present in the vector index.

        The upload directory is only a source-of-truth for uploaded files; this
        view also preserves built-in or previously indexed documents whose
        source file is no longer mounted in the API container.
        """
        load_all_chunks = getattr(self._vector_store, "load_all_chunks", None)
        if load_all_chunks is None:
            return ()
        chunks = await load_all_chunks()
        grouped: dict[tuple[str, str], list[Chunk]] = defaultdict(list)
        for chunk in chunks:
            grouped[(chunk.document_id, chunk.document_version)].append(chunk)
        documents: list[tuple[DocumentRecord, int, str]] = []
        for (document_id, version), items in grouped.items():
            first = items[0]
            documents.append(
                (
                    DocumentRecord(
                        document_id=document_id,
                        title=first.title,
                        source=first.source,
                        document_type=first.document_type,
                        content="\n\n".join(item.content for item in items),
                        version=version,
                    ),
                    len(items),
                    version,
                )
            )
        return tuple(documents)
