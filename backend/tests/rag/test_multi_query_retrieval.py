from __future__ import annotations

from uuid import uuid4

import pytest

from backend.app.adapters.vector.fake import FixedEmbedding, InMemoryVectorStore
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import DocumentRecord, DocumentType
from backend.app.rag.query_rewrite import (
    DeterministicQueryRewriter,
    QueryPlan,
)
from backend.app.rag.retrieval import HybridRetriever, LexicalReranker, MultiQueryRetriever


class FixedQueryRewriter:
    async def rewrite(self, query: str) -> QueryPlan:
        return QueryPlan(query, (query, "brush maintenance interval"), "llm-query-rewrite")


@pytest.mark.asyncio
async def test_multi_query_retrieval_fuses_rewrite_branches_and_reranks_original_query() -> None:
    embedding = FixedEmbedding()
    vectors = InMemoryVectorStore()
    keywords = BM25KeywordIndex()
    document = DocumentRecord(
        document_id=str(uuid4()),
        title="Maintenance guide",
        source="kb://maintenance",
        document_type=DocumentType.TEXT,
        content="The side brush maintenance interval is every three months.",
    )
    await KnowledgeIndexer(DocumentChunker(), embedding, vectors, keywords).ingest(document)
    retriever = MultiQueryRetriever(
        HybridRetriever(embedding, vectors, keywords, LexicalReranker()),
        FixedQueryRewriter(),
    )

    result = await retriever.retrieve("When should I maintain the side brush?")

    assert result.hits
    assert result.hits[0].chunk.document_id == document.document_id
    assert result.strategy == "llm-query-rewrite+multi-query+vector+bm25+rrf+rerank"
    assert result.confidence > 0


@pytest.mark.asyncio
async def test_deterministic_query_rewrite_preserves_original_query() -> None:
    plan = await DeterministicQueryRewriter().rewrite("  filter   cleaning  ")

    assert plan.original == "filter cleaning"
    assert plan.queries == ("filter cleaning",)
    assert plan.strategy == "original-query-fallback"
