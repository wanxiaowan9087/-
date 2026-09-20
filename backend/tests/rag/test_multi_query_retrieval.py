from __future__ import annotations

from uuid import uuid4

import httpx
import pytest

from backend.app.adapters.rerank.dashscope import DashScopeReranker
from backend.app.adapters.vector.fake import FixedEmbedding, InMemoryVectorStore
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import (
    Chunk,
    DocumentRecord,
    DocumentType,
    RetrievalResult,
    SearchHit,
)
from backend.app.rag.query_rewrite import (
    DeterministicQueryRewriter,
    QueryPlan,
)
from backend.app.rag.retrieval import (
    HybridRetriever,
    LexicalReranker,
    LowConfidenceRetryRetriever,
    MultiQueryRetriever,
)


class FixedQueryRewriter:
    async def rewrite(self, query: str) -> QueryPlan:
        return QueryPlan(query, (query, "brush maintenance interval"), "llm-query-rewrite")


class ResultRetriever:
    def __init__(self, result: RetrievalResult | Exception) -> None:
        self.result = result
        self.queries: list[str] = []

    async def retrieve(self, query: str) -> RetrievalResult:
        self.queries.append(query)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _result_hit(content: str, *, score: float = 0.9) -> SearchHit:
    return SearchHit(
        chunk=Chunk(
            document_id=str(uuid4()),
            document_version="v1",
            chunk_id=str(uuid4()),
            title="知识资料",
            source="kb://test",
            content=content,
            document_type=DocumentType.TEXT,
        ),
        vector_score=score,
        keyword_score=score,
        fused_score=score,
        rerank_score=score,
    )


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
async def test_multi_query_calls_external_reranker_once_after_branch_fusion() -> None:
    embedding = FixedEmbedding()
    vectors = InMemoryVectorStore()
    keywords = BM25KeywordIndex()
    document = DocumentRecord(
        document_id=str(uuid4()),
        title="边刷维护",
        source="kb://maintenance",
        document_type=DocumentType.TEXT,
        content="边刷建议每三个月维护一次。",
    )
    await KnowledgeIndexer(DocumentChunker(), embedding, vectors, keywords).ingest(document)
    requests: list[httpx.Request] = []

    async def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"output": {"results": [{"index": 0, "relevance_score": 0.91}]}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    final_reranker = DashScopeReranker(client, api_key="test-key", model="test-model")
    retriever = MultiQueryRetriever(
        HybridRetriever(
            embedding,
            vectors,
            keywords,
            LexicalReranker(),
            candidate_limit=20,
            result_limit=20,
        ),
        FixedQueryRewriter(),
        final_reranker=final_reranker,
        candidate_limit=20,
        result_limit=8,
    )

    result = await retriever.retrieve("边刷多久维护")

    assert len(requests) == 1
    assert result.hits[0].rerank_score == 0.91
    await client.aclose()


@pytest.mark.asyncio
async def test_low_confidence_retrieval_uses_query_rewrite_retry_only_when_needed() -> None:
    weak = RetrievalResult(hits=(), confidence=0.2, strategy="deterministic")
    improved = RetrievalResult(hits=(), confidence=0.72, strategy="llm-query-rewrite")
    primary = ResultRetriever(weak)
    retry = ResultRetriever(improved)
    retriever = LowConfidenceRetryRetriever(primary, retry, confidence_threshold=0.5)

    result = await retriever.retrieve("机子老是找不到家咋办")

    assert result is improved
    assert primary.queries == ["机子老是找不到家咋办"]
    assert retry.queries == ["机子老是找不到家咋办"]


@pytest.mark.asyncio
async def test_high_confidence_retrieval_skips_query_rewrite_model_path() -> None:
    strong = RetrievalResult(
        hits=(_result_hit("边刷建议每三个月检查并按磨损情况更换。"),),
        confidence=0.82,
        strategy="deterministic",
    )
    primary = ResultRetriever(strong)
    retry = ResultRetriever(AssertionError("retry must not run"))
    retriever = LowConfidenceRetryRetriever(primary, retry, confidence_threshold=0.5)

    result = await retriever.retrieve("边刷多久换")

    assert result is strong
    assert retry.queries == []


@pytest.mark.asyncio
async def test_high_numeric_confidence_with_unrelated_evidence_uses_rewrite_retry() -> None:
    misleading = RetrievalResult(
        hits=(_result_hit("会员积分将在每月最后一天结算。"),),
        confidence=0.93,
        strategy="deterministic",
    )
    improved = RetrievalResult(
        hits=(_result_hit("边刷缠毛时应断电后拆下边刷清理。"),),
        confidence=0.88,
        strategy="llm-query-rewrite",
    )
    retry = ResultRetriever(improved)
    retriever = LowConfidenceRetryRetriever(
        ResultRetriever(misleading), retry, confidence_threshold=0.5
    )

    result = await retriever.retrieve("边刷缠毛怎么处理？")

    assert result is improved
    assert retry.queries == ["边刷缠毛怎么处理？"]


@pytest.mark.asyncio
async def test_query_rewrite_retry_failure_keeps_original_retrieval() -> None:
    weak = RetrievalResult(hits=(), confidence=0.2, strategy="deterministic")
    retriever = LowConfidenceRetryRetriever(
        ResultRetriever(weak),
        ResultRetriever(TimeoutError("rewrite timeout")),
        confidence_threshold=0.5,
    )

    result = await retriever.retrieve("口语化问题")

    assert result.confidence == weak.confidence
    assert result.degraded_dependencies == ("query_rewrite",)


@pytest.mark.asyncio
async def test_deterministic_query_rewrite_preserves_original_query() -> None:
    plan = await DeterministicQueryRewriter().rewrite("  filter   cleaning  ")

    assert plan.original == "filter cleaning"
    assert plan.queries == ("filter cleaning",)
    assert plan.strategy == "original-query-fallback"


@pytest.mark.asyncio
async def test_deterministic_query_rewrite_keeps_original_and_normalized_queries() -> (
    None
):
    plan = await DeterministicQueryRewriter().rewrite("皓月拖布怎么老是湿哒哒的？")

    assert plan.original == "皓月拖布怎么老是湿哒哒的？"
    assert plan.queries == (
        "皓月拖布怎么老是湿哒哒的？",
        "皓月拖布怎么持续潮湿的？",
    )
    assert plan.strategy == "deterministic-dual-query-normalization"


@pytest.mark.asyncio
async def test_deterministic_query_rewrite_expands_color_only_robot_followup() -> None:
    plan = await DeterministicQueryRewriter().rewrite("我喜欢白色的")

    assert plan.original == "我喜欢白色的"
    assert plan.queries == (
        "我喜欢白色的",
        "我喜欢白色的 扫地机器人 型号 推荐",
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("question", "expected_terms"),
    [
        ("我换了个小家，就三十来平，买哪个合适", ("小户型", "30", "推荐", "型号")),
        ("房子不大大概30㎡，想省心点，你帮我挑一款", ("小户型", "30", "推荐", "型号")),
        ("预算两千左右，小户型木地板，平时有猫毛，咋选", ("预算", "小户型", "木地板", "宠物家庭")),
        ("我喜欢灰色但家人喜欢绿色，按适合户型的来买还是按颜色", ("灰色", "绿色", "扫地机器人")),
        ("说得有点乱哈，面积30平、木地板、预算不高，给个建议", ("小户型", "30", "木地板")),
    ],
)
async def test_deterministic_rewrite_extracts_constraints_from_natural_language(
    question: str, expected_terms: tuple[str, ...]
) -> None:
    plan = await DeterministicQueryRewriter().rewrite(question)

    assert plan.strategy == "deterministic-dual-query-normalization"
    canonical = plan.queries[1]
    for term in expected_terms:
        assert term in canonical
