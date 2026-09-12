from __future__ import annotations

import argparse
import asyncio
import json
from dataclasses import asdict
from pathlib import Path
from typing import Any

from backend.app.adapters.vector.dashscope import DashScopeEmbeddingAdapter
from backend.app.adapters.vector.pgvector_store import PgVectorStore
from backend.app.agent.safety import (
    PromptInjectionDetector,
    classify_user_risk,
    required_fields_missing,
)
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.citations import CitationService
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import DocumentRecord, DocumentType
from backend.app.rag.query_rewrite import DeterministicQueryRewriter
from backend.app.rag.retrieval import HybridRetriever, LexicalReranker, MultiQueryRetriever
from backend.app.rag.security import scan_retrieved_content
from sqlalchemy.ext.asyncio import create_async_engine

from evals.run_rag_eval import (
    DEFAULT_CORPUS,
    DEFAULT_DATASET,
    DEFAULT_SUPPORT_ANNOTATIONS,
    CaseResult,
    _average_metric,
    _load_support_annotations,
    _metric,
    _ndcg,
    _read_jsonl,
    _resolve_expected_chunks,
    _score_citation_support,
)


async def evaluate(
    *,
    database_url: str,
    corpus_path: Path = DEFAULT_CORPUS,
    dataset_path: Path = DEFAULT_DATASET,
    support_annotations_path: Path = DEFAULT_SUPPORT_ANNOTATIONS,
    embedding_model: str = "text-embedding-v4",
    vector_dimensions: int = 1024,
    chunk_size: int = 600,
    chunk_overlap: int = 80,
) -> dict[str, Any]:
    try:
        from langchain_community.embeddings import DashScopeEmbeddings
    except ImportError as error:
        raise RuntimeError("DashScope evaluation dependencies are not installed") from error

    engine = create_async_engine(database_url, pool_pre_ping=True)
    embeddings = DashScopeEmbeddingAdapter(DashScopeEmbeddings(model=embedding_model))
    vector_store = PgVectorStore(engine, vector_dimensions)
    keyword_index = BM25KeywordIndex()
    indexer = KnowledgeIndexer(
        DocumentChunker(text_chunk_size=chunk_size, text_overlap=chunk_overlap),
        embeddings,
        vector_store,
        keyword_index,
    )
    try:
        for raw in _read_jsonl(corpus_path):
            await indexer.ingest(
                DocumentRecord(
                    document_id=raw["document_id"],
                    version=raw["version"],
                    title=raw["title"],
                    source=raw["source"],
                    document_type=DocumentType(raw["document_type"]),
                    content=raw["content"],
                    metadata=raw.get("metadata", {}),
                )
            )
        indexed_chunks = await vector_store.load_all_chunks()
        cases = _read_jsonl(dataset_path)
        annotations = _load_support_annotations(support_annotations_path, cases, indexed_chunks)
        hybrid = HybridRetriever(
            embeddings,
            vector_store,
            keyword_index,
            LexicalReranker(),
            candidate_limit=18,
            result_limit=10,
        )
        retriever = MultiQueryRetriever(
            hybrid,
            DeterministicQueryRewriter(),
            result_limit=10,
        )
        detector = PromptInjectionDetector()
        citations = CitationService()
        results: list[CaseResult] = []

        for case in cases:
            retrieval = await retriever.retrieve(case["question"])
            ranked = tuple(hit.chunk.chunk_id for hit in retrieval.hits)
            expected = _resolve_expected_chunks(case["expected_sources"], indexed_chunks)
            supporting_sources = annotations.get(case["case_id"], ())
            first_rank = next(
                (rank for rank, chunk_id in enumerate(ranked[:10], 1) if chunk_id in expected),
                None,
            )
            user_signals = detector.scan(case["question"], source="user")
            retrieved_signals = scan_retrieved_content(retrieval.hits, detector)
            high_risk, sensitive, _ = classify_user_risk(case["question"])
            must_withhold = bool(
                user_signals
                or retrieved_signals
                or retrieval.conflicting_sources
                or high_risk
                or (sensitive and required_fields_missing(case["question"]))
            )
            answered = bool(
                retrieval.has_evidence and retrieval.confidence >= 0.65 and not must_withhold
            )
            built_citations = (
                citations.build(retrieval.hits, limit=max(1, len(supporting_sources)))
                if answered
                else ()
            )
            validation = citations.validate(built_citations, [hit.chunk for hit in retrieval.hits])
            supported, claim_ids, support_failures = _score_citation_support(
                built_citations,
                supporting_sources,
                citations,
                [hit.chunk for hit in retrieval.hits],
            )
            injection_defended = None
            if "prompt_injection" in case["tags"]:
                injection_defended = bool(must_withhold and not answered)
            target_model = case.get("target_model")
            results.append(
                CaseResult(
                    case_id=case["case_id"],
                    ranked_chunk_ids=ranked,
                    confidence=retrieval.confidence,
                    answered=answered,
                    expected_hit_at_1=bool(expected & set(ranked[:1])),
                    expected_hit_at_3=bool(expected & set(ranked[:3])),
                    expected_hit_at_5=bool(expected & set(ranked[:5])),
                    expected_hit_at_10=bool(expected & set(ranked[:10])),
                    reciprocal_rank=1.0 / first_rank if first_rank is not None else 0.0,
                    ndcg=_ndcg(ranked[:10], expected),
                    citation_integrity=validation.valid,
                    supported_citations=supported,
                    total_citations=len(built_citations),
                    supported_claim_ids=claim_ids,
                    citation_support_failures=support_failures,
                    injection_defended=injection_defended,
                    top_1_model_matched=(
                        bool(
                            retrieval.hits
                            and retrieval.hits[0].chunk.metadata.get("model") == target_model
                        )
                        if target_model
                        else None
                    ),
                )
            )

        answerable = [
            item for item, case in zip(results, cases, strict=True) if case["should_answer"]
        ]
        unanswerable = [
            item for item, case in zip(results, cases, strict=True) if not case["should_answer"]
        ]
        injection = [item for item in results if item.injection_defended is not None]
        model = [item for item in results if item.top_1_model_matched is not None]
        citation_total = sum(item.total_citations for item in answerable)
        citation_supported = sum(item.supported_citations for item in answerable)
        metrics = {
            "recall@1": _metric(
                sum(item.expected_hit_at_1 for item in answerable), len(answerable)
            ),
            "recall@3": _metric(
                sum(item.expected_hit_at_3 for item in answerable), len(answerable)
            ),
            "retrieval_hit_rate@5": _metric(
                sum(item.expected_hit_at_5 for item in answerable), len(answerable)
            ),
            "recall@10": _metric(
                sum(item.expected_hit_at_10 for item in answerable), len(answerable)
            ),
            "mrr@10": _average_metric([item.reciprocal_rank for item in answerable]),
            "ndcg@10": _average_metric([item.ndcg for item in answerable]),
            "citation_integrity": _metric(
                sum(item.total_citations for item in answerable if item.citation_integrity),
                citation_total,
            ),
            "citation_support_precision": _metric(citation_supported, citation_total),
            "citation_faithfulness": _metric(citation_supported, citation_total),
            "unanswerable_refusal_recall": _metric(
                sum(not item.answered for item in unanswerable), len(unanswerable)
            ),
            "answerable_false_refusal_rate": _metric(
                sum(not item.answered for item in answerable), len(answerable)
            ),
            "prompt_injection_defense_rate": _metric(
                sum(bool(item.injection_defended) for item in injection), len(injection)
            ),
            "model_top_1_accuracy": _metric(
                sum(bool(item.top_1_model_matched) for item in model), len(model)
            ),
        }
        return {
            "dataset": dataset_path.name,
            "corpus": corpus_path.name,
            "dataset_cases": len(cases),
            "embedding": f"dashscope/{embedding_model}/{vector_dimensions}",
            "vector_store": "postgresql+pgvector",
            "retrieval": "deterministic-query-normalization+vector+bm25+rrf+lexical-rerank",
            "confidence_threshold": 0.65,
            "chunking": {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
            "query_normalization": True,
            "support_annotations": support_annotations_path.name,
            "metrics": {name: asdict(metric) for name, metric in metrics.items()},
            "cases": [asdict(item) for item in results],
        }
    finally:
        await engine.dispose()


def main() -> int:
    parser = argparse.ArgumentParser(description="Run a real DashScope + pgvector RAG evaluation.")
    parser.add_argument("--database-url", required=True)
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--support-annotations", type=Path, default=DEFAULT_SUPPORT_ANNOTATIONS)
    parser.add_argument("--embedding-model", default="text-embedding-v4")
    parser.add_argument("--vector-dimensions", type=int, default=1024)
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    parser.add_argument("--output", type=Path, required=True)
    arguments = parser.parse_args()
    report = asyncio.run(
        evaluate(
            database_url=arguments.database_url,
            corpus_path=arguments.corpus,
            dataset_path=arguments.dataset,
            support_annotations_path=arguments.support_annotations,
            embedding_model=arguments.embedding_model,
            vector_dimensions=arguments.vector_dimensions,
            chunk_size=arguments.chunk_size,
            chunk_overlap=arguments.chunk_overlap,
        )
    )
    arguments.output.parent.mkdir(parents=True, exist_ok=True)
    arguments.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {key: value for key, value in report.items() if key != "cases"},
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
