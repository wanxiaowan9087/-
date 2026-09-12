from __future__ import annotations

import argparse
import asyncio
import json
import math
import re
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from backend.app.adapters.vector.fake import FixedEmbedding, InMemoryVectorStore
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

ROOT = Path(__file__).resolve().parent
DEFAULT_CORPUS = ROOT / "corpus" / "customer-service-v1.jsonl"
DEFAULT_DATASET = ROOT / "datasets" / "customer-service-rag-v1.jsonl"
DEFAULT_SUPPORT_ANNOTATIONS = ROOT / "datasets" / "customer-service-rag-v1.support.json"

THRESHOLDS = {
    "retrieval_hit_rate@5": ("min", 0.80),
    "mrr@10": ("min", 0.65),
    "ndcg@10": ("min", 0.70),
    "citation_integrity": ("min", 1.00),
    "citation_support_precision": ("min", 0.90),
    "unanswerable_refusal_recall": ("min", 0.90),
    "answerable_false_refusal_rate": ("max", 0.10),
    "prompt_injection_defense_rate": ("min", 1.00),
}


@dataclass(frozen=True)
class Metric:
    value: float
    numerator: int
    denominator: int


@dataclass(frozen=True)
class CaseResult:
    case_id: str
    ranked_chunk_ids: tuple[str, ...]
    confidence: float
    answered: bool
    expected_hit_at_1: bool
    expected_hit_at_3: bool
    expected_hit_at_5: bool
    expected_hit_at_10: bool
    reciprocal_rank: float
    ndcg: float
    citation_integrity: bool
    supported_citations: int
    total_citations: int
    supported_claim_ids: tuple[str, ...]
    citation_support_failures: tuple[str, ...]
    injection_defended: bool | None
    top_1_model_matched: bool | None


@dataclass(frozen=True)
class SupportingSource:
    claim_id: str
    document_id: str
    document_version: str
    chunk_id: str

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.document_id, self.document_version, self.chunk_id


async def evaluate(
    corpus_path: Path = DEFAULT_CORPUS,
    dataset_path: Path = DEFAULT_DATASET,
    support_annotations_path: Path = DEFAULT_SUPPORT_ANNOTATIONS,
    implementation_sha: str | None = None,
    *,
    chunk_size: int = 600,
    chunk_overlap: int = 80,
    query_normalization: bool = False,
) -> dict[str, Any]:
    embeddings = FixedEmbedding()
    vector_store = InMemoryVectorStore()
    keyword_index = BM25KeywordIndex()
    indexer = KnowledgeIndexer(
        DocumentChunker(text_chunk_size=chunk_size, text_overlap=chunk_overlap),
        embeddings,
        vector_store,
        keyword_index,
    )
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
    hybrid_retriever = HybridRetriever(
        embeddings,
        vector_store,
        keyword_index,
        LexicalReranker(),
        candidate_limit=18,
        result_limit=10,
    )
    retriever = (
        MultiQueryRetriever(hybrid_retriever, DeterministicQueryRewriter(), result_limit=10)
        if query_normalization
        else hybrid_retriever
    )
    detector = PromptInjectionDetector()
    citations = CitationService()
    cases = _read_jsonl(dataset_path)
    indexed_chunks = vector_store.snapshot()
    support_annotations = _load_support_annotations(support_annotations_path, cases, indexed_chunks)
    case_results: list[CaseResult] = []
    answerable_count = sum(bool(case["should_answer"]) for case in cases)
    unanswerable_count = len(cases) - answerable_count
    prompt_injection_count = sum("prompt_injection" in case["tags"] for case in cases)

    for case in cases:
        result = await retriever.retrieve(case["question"])
        ranked = tuple(hit.chunk.chunk_id for hit in result.hits)
        expected = _resolve_expected_chunks(case["expected_sources"], indexed_chunks)
        supporting_sources = support_annotations.get(case["case_id"], ())
        first_rank = next(
            (index for index, chunk_id in enumerate(ranked[:10], 1) if chunk_id in expected),
            None,
        )
        user_signals = detector.scan(case["question"], source="user")
        retrieved_signals = scan_retrieved_content(result.hits, detector)
        high_risk, sensitive, _ = classify_user_risk(case["question"])
        missing = required_fields_missing(case["question"])
        must_withhold = bool(
            user_signals
            or retrieved_signals
            or result.conflicting_sources
            or high_risk
            or (sensitive and missing)
        )
        answered = bool(result.has_evidence and result.confidence >= 0.65 and not must_withhold)
        built_citations = (
            citations.build(
                result.hits,
                limit=max(1, len(supporting_sources)),
            )
            if answered
            else ()
        )
        validation = citations.validate(built_citations, [hit.chunk for hit in result.hits])
        supported, supported_claim_ids, support_failures = _score_citation_support(
            built_citations,
            supporting_sources,
            citations,
            [hit.chunk for hit in result.hits],
        )
        forbidden = set(case["forbidden_sources"])
        injection_defended: bool | None = None
        if "prompt_injection" in case["tags"]:
            injection_defended = bool(
                must_withhold
                and not answered
                and not any(citation.chunk_id in forbidden for citation in built_citations)
            )
        target_model = case.get("target_model")
        top_1_model_matched = None
        if target_model:
            top_1_model_matched = bool(
                result.hits and result.hits[0].chunk.metadata.get("model") == target_model
            )
        case_results.append(
            CaseResult(
                case_id=case["case_id"],
                ranked_chunk_ids=ranked,
                confidence=result.confidence,
                answered=answered,
                expected_hit_at_1=bool(expected & set(ranked[:1])),
                expected_hit_at_3=bool(expected & set(ranked[:3])),
                expected_hit_at_5=bool(expected & set(ranked[:5])),
                expected_hit_at_10=bool(expected & set(ranked[:10])),
                reciprocal_rank=(1.0 / first_rank if first_rank is not None else 0.0),
                ndcg=_ndcg(ranked[:10], expected),
                citation_integrity=validation.valid,
                supported_citations=supported,
                total_citations=len(built_citations),
                supported_claim_ids=supported_claim_ids,
                citation_support_failures=support_failures,
                injection_defended=injection_defended,
                top_1_model_matched=top_1_model_matched,
            )
        )

    answerable_results = [
        result for result, case in zip(case_results, cases, strict=False) if case["should_answer"]
    ]
    unanswerable_results = [
        result
        for result, case in zip(case_results, cases, strict=False)
        if not case["should_answer"]
    ]
    citation_total = sum(result.total_citations for result in answerable_results)
    citation_supported = sum(result.supported_citations for result in answerable_results)
    integrity_total = sum(result.total_citations for result in answerable_results)
    integrity_valid = sum(
        result.total_citations for result in answerable_results if result.citation_integrity
    )
    injection_results = [result for result in case_results if result.injection_defended is not None]
    model_results = [result for result in case_results if result.top_1_model_matched is not None]
    metrics = {
        "recall@1": _metric(
            sum(result.expected_hit_at_1 for result in answerable_results), answerable_count
        ),
        "recall@3": _metric(
            sum(result.expected_hit_at_3 for result in answerable_results), answerable_count
        ),
        "retrieval_hit_rate@5": _metric(
            sum(result.expected_hit_at_5 for result in answerable_results),
            answerable_count,
        ),
        "recall@10": _metric(
            sum(result.expected_hit_at_10 for result in answerable_results), answerable_count
        ),
        "mrr@10": _average_metric([result.reciprocal_rank for result in answerable_results]),
        "ndcg@10": _average_metric([result.ndcg for result in answerable_results]),
        "citation_integrity": _metric(integrity_valid, integrity_total),
        "citation_support_precision": _metric(citation_supported, citation_total),
        # This validates that returned citations bind to human-curated claims.
        # It is evidence fidelity, not an LLM semantic-faithfulness judgement.
        "citation_faithfulness": _metric(citation_supported, citation_total),
        "unanswerable_refusal_recall": _metric(
            sum(not result.answered for result in unanswerable_results),
            unanswerable_count,
        ),
        "answerable_false_refusal_rate": _metric(
            sum(not result.answered for result in answerable_results),
            answerable_count,
        ),
        "prompt_injection_defense_rate": _metric(
            sum(bool(result.injection_defended) for result in injection_results),
            prompt_injection_count,
        ),
        "model_top_1_accuracy": _metric(
            sum(bool(result.top_1_model_matched) for result in model_results), len(model_results)
        ),
    }
    metric_dict = {key: asdict(value) for key, value in metrics.items()}
    gates = {
        key: (
            metric_dict[key]["value"] >= threshold
            if direction == "min"
            else metric_dict[key]["value"] <= threshold
        )
        for key, (direction, threshold) in THRESHOLDS.items()
    }
    return {
        "dataset": dataset_path.name,
        "corpus": corpus_path.name,
        "dataset_cases": len(cases),
        "embedding": "fixed-hash-v1/256",
        "retrieval": "vector+bm25+rrf+lexical-rerank",
        "confidence_threshold": 0.65,
        "chunking": {"chunk_size": chunk_size, "chunk_overlap": chunk_overlap},
        "query_normalization": query_normalization,
        "support_annotations": support_annotations_path.name,
        "implementation_sha": implementation_sha,
        "metrics": metric_dict,
        "gates": gates,
        "passed": all(gates.values()),
        "cases": [asdict(result) for result in case_results],
    }


def _load_support_annotations(
    path: Path,
    cases: Sequence[dict[str, Any]],
    chunks: Sequence[Any] | None = None,
) -> dict[str, tuple[SupportingSource, ...]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    if raw.get("schema_version") != 1:
        raise ValueError("unsupported citation support annotation schema")
    annotations: dict[str, tuple[SupportingSource, ...]] = {}
    for case_id, claims in raw.get("cases", {}).items():
        sources: list[SupportingSource] = []
        for claim in claims:
            claim_id = str(claim["claim_id"])
            if not claim_id.strip():
                raise ValueError(f"blank claim_id for {case_id}")
            for source in claim["supporting_sources"]:
                if chunks is None:
                    if "chunk_id" not in source:
                        raise ValueError("section selectors require indexed chunks")
                    sources.append(
                        SupportingSource(
                            claim_id=claim_id,
                            document_id=str(source["document_id"]),
                            document_version=str(source["document_version"]),
                            chunk_id=str(source["chunk_id"]),
                        )
                    )
                else:
                    for chunk_id in _resolve_expected_chunks((source,), chunks):
                        matched = next(item for item in chunks if item.chunk_id == chunk_id)
                        sources.append(
                            SupportingSource(
                                claim_id=claim_id,
                                document_id=matched.document_id,
                                document_version=matched.document_version,
                                chunk_id=matched.chunk_id,
                            )
                        )
        annotations[case_id] = tuple(sources)

    case_ids = {str(case["case_id"]) for case in cases}
    unknown = set(annotations) - case_ids
    if unknown:
        raise ValueError(f"support annotations contain unknown cases: {sorted(unknown)}")
    for case in cases:
        case_sources = annotations.get(str(case["case_id"]), ())
        if bool(case["should_answer"]) != bool(case_sources):
            raise ValueError(
                f"support annotations disagree with should_answer for {case['case_id']}"
            )
        if chunks is None:
            expected_identities = {
                (str(source["document_id"]), str(source["chunk_id"]))
                for source in case["expected_sources"]
            }
        else:
            expected_chunk_ids = _resolve_expected_chunks(case["expected_sources"], chunks)
            expected_identities = {
                (chunk.document_id, chunk.chunk_id)
                for chunk in chunks
                if chunk.chunk_id in expected_chunk_ids
            }
        for source in case_sources:
            if (source.document_id, source.chunk_id) not in expected_identities:
                raise ValueError(
                    f"support source is not retrieval ground truth for {case['case_id']}"
                )
    return annotations


def _resolve_expected_chunks(sources: Sequence[dict[str, Any]], chunks: Sequence[Any]) -> set[str]:
    """Resolve fixed chunk IDs or stable document/section selectors.

    v1 labels retain their exact chunk IDs. v2 labels select a document and
    Markdown heading so chunking experiments are compared against equivalent
    evidence rather than stale ordinal IDs.
    """
    resolved: set[str] = set()
    for source in sources:
        chunk_id = source.get("chunk_id")
        if chunk_id:
            resolved.add(str(chunk_id))
            continue
        document_id = str(source["document_id"])
        section = str(source.get("section", "")).strip()
        candidates = [chunk for chunk in chunks if chunk.document_id == document_id]
        if section:
            candidates = [
                chunk
                for chunk in candidates
                if chunk.location.section == section or chunk.metadata.get("heading") == section
            ]
        if not candidates:
            raise ValueError(
                f"expected source selector matched no chunks: {document_id} / {section or '*'}"
            )
        resolved.update(chunk.chunk_id for chunk in candidates)
    return resolved


def _score_citation_support(
    built_citations: Sequence[Any],
    supporting_sources: Sequence[SupportingSource],
    citation_service: CitationService,
    available_chunks: Sequence[Any],
) -> tuple[int, tuple[str, ...], tuple[str, ...]]:
    support_by_identity: dict[tuple[str, str, str], set[str]] = {}
    for source in supporting_sources:
        support_by_identity.setdefault(source.identity, set()).add(source.claim_id)

    supported = 0
    claim_ids: set[str] = set()
    failures: list[str] = []
    for citation in built_citations:
        validation = citation_service.validate((citation,), available_chunks)
        identity = (
            citation.document_id,
            citation.document_version,
            citation.chunk_id,
        )
        if not validation.valid:
            failures.extend(validation.errors)
            continue
        claims = support_by_identity.get(identity)
        if not claims:
            failures.append(
                "unsupported_source:"
                f"{citation.document_id}:{citation.document_version}:{citation.chunk_id}"
            )
            continue
        supported += 1
        claim_ids.update(claims)
    return supported, tuple(sorted(claim_ids)), tuple(failures)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8") as stream:
        return [
            json.loads(line)
            for line in stream
            if line.strip() and not line.lstrip().startswith("#")
        ]


def _metric(numerator: int, denominator: int) -> Metric:
    return Metric(
        value=round(numerator / denominator, 6) if denominator else 1.0,
        numerator=numerator,
        denominator=denominator,
    )


def _average_metric(values: Sequence[float]) -> Metric:
    scaled = round(sum(values) * 1_000_000)
    denominator = len(values) * 1_000_000
    return Metric(
        value=round(sum(values) / len(values), 6) if values else 1.0,
        numerator=scaled,
        denominator=denominator,
    )


def _ndcg(ranked: Sequence[str], relevant: set[str]) -> float:
    if not relevant:
        return 1.0
    dcg = sum(
        1.0 / math.log2(rank + 1) for rank, chunk_id in enumerate(ranked, 1) if chunk_id in relevant
    )
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, min(len(relevant), len(ranked)) + 1))
    return dcg / ideal if ideal else 0.0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument(
        "--support-annotations",
        type=Path,
        default=DEFAULT_SUPPORT_ANNOTATIONS,
    )
    parser.add_argument("--implementation-sha")
    parser.add_argument("--chunk-size", type=int, default=600)
    parser.add_argument("--chunk-overlap", type=int, default=80)
    parser.add_argument(
        "--query-normalization",
        action="store_true",
        help="Apply zero-token deterministic colloquial query normalization before retrieval.",
    )
    parser.add_argument("--write-candidate", type=Path)
    parser.add_argument("--assert-gates", action="store_true")
    parser.add_argument("--show-cases", action="store_true")
    arguments = parser.parse_args()
    if arguments.implementation_sha is not None and not re.fullmatch(
        r"[0-9a-f]{40}", arguments.implementation_sha
    ):
        parser.error("--implementation-sha must be a 40-character lowercase Git SHA")
    if arguments.write_candidate is not None and arguments.implementation_sha is None:
        parser.error("--write-candidate requires --implementation-sha")
    report = asyncio.run(
        evaluate(
            arguments.corpus,
            arguments.dataset,
            arguments.support_annotations,
            arguments.implementation_sha,
            chunk_size=arguments.chunk_size,
            chunk_overlap=arguments.chunk_overlap,
            query_normalization=arguments.query_normalization,
        )
    )
    if arguments.write_candidate is not None:
        candidate = {
            "baseline_status": "proposed_for_qa_review",
            **{key: value for key, value in report.items() if key != "cases"},
        }
        with arguments.write_candidate.open("x", encoding="utf-8") as stream:
            json.dump(
                candidate,
                stream,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
            stream.write("\n")
    if not arguments.show_cases:
        report = {key: value for key, value in report.items() if key != "cases"}
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    return 1 if arguments.assert_gates and not report["passed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
