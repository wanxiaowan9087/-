from __future__ import annotations

from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import Chunk, DocumentRecord, DocumentType, RetrievalResult, SearchHit
from backend.app.rag.retrieval import LexicalReranker


class LocalTextCorpusRetriever:
    """Keyword retriever over bundled text knowledge files.

    This is a production-friendly fallback for small deployments: the app can
    still answer from cited local material when a vector store has not been
    ingested yet.
    """

    def __init__(self, root: str, *, result_limit: int = 5) -> None:
        self._root = Path(root)
        self._result_limit = result_limit
        self._keyword_index = BM25KeywordIndex(_load_chunks(self._root))
        self._reranker = LexicalReranker()

    async def retrieve(self, query: str) -> RetrievalResult:
        keyword_hits = await self._keyword_index.search(query, self._result_limit * 2)
        hits = tuple(
            SearchHit(
                chunk=item.chunk,
                vector_score=None,
                keyword_score=item.score,
                fused_score=item.score,
            )
            for item in keyword_hits
        )
        reranked = tuple(await self._reranker.rerank(query, hits, self._result_limit))
        return RetrievalResult(
            hits=reranked,
            confidence=_confidence(reranked),
            strategy="bm25+local-text-corpus",
            degraded_dependencies=("vector_store",),
        )


def _load_chunks(root: Path) -> tuple[Chunk, ...]:
    if not root.exists():
        return ()
    chunker = DocumentChunker()
    chunks: list[Chunk] = []
    for path in sorted(root.glob("*.txt")):
        content = path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            continue
        document = DocumentRecord(
            document_id=str(uuid5(NAMESPACE_URL, path.name)),
            title=path.stem.strip(".") or path.name,
            source=f"file://data/{path.name}",
            document_type=DocumentType.TEXT,
            content=content,
        )
        chunks.extend(chunker.split(document))
    return tuple(chunks)


def _confidence(hits: tuple[SearchHit, ...]) -> float:
    if not hits:
        return 0.0
    top_score = hits[0].score
    support_bonus = 0.06 if len(hits) > 1 and hits[1].score >= 0.45 else 0.0
    return round(min(1.0, top_score + support_bonus), 6)
