from __future__ import annotations

import argparse
import asyncio
import json
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from time import perf_counter
from typing import Any

from backend.app.adapters.vector.fake import FixedEmbedding, InMemoryVectorStore
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.evidence import select_citation_hits, select_claim_evidence
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.models import (
    Chunk,
    DocumentRecord,
    DocumentType,
    SearchHit,
)
from backend.app.rag.query_rewrite import DeterministicQueryRewriter
from backend.app.rag.retrieval import HybridRetriever, LexicalReranker, MultiQueryRetriever
from backend.app.rag.retrieval_planning import select_context_hits

ROOT = Path(__file__).resolve().parent
DEFAULT_CORPUS = ROOT / "corpus" / "zenmop-rag-v3.jsonl"
DEFAULT_DATASET = ROOT / "datasets" / "zenmop-claim-citation-v1.json"


@dataclass(frozen=True)
class Metric:
    value: float
    numerator: int
    denominator: int


@dataclass(frozen=True)
class EvaluationCounts:
    correct_links: int = 0
    total_links: int = 0
    supported_claims: int = 0
    expected_claims: int = 0
    unsupported_rejections: int = 0
    unsupported_claims: int = 0
    cross_model_links: int = 0
    wrong_section_links: int = 0
    generic_source_links: int = 0
    false_binding_links: int = 0
    model_scoped_links: int = 0
    conditional_correct_links: int = 0
    conditional_total_links: int = 0
    conditional_supported_claims: int = 0
    conditional_expected_claims: int = 0


@dataclass(frozen=True)
class RetrievalCounts:
    retrieved_claims: int = 0
    expected_claims: int = 0


def _metric(numerator: int, denominator: int) -> Metric:
    return Metric(
        value=round(numerator / denominator, 6) if denominator else 1.0,
        numerator=numerator,
        denominator=denominator,
    )


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def _section_matches(candidate: object, expected: str) -> bool:
    value = str(candidate or "").strip()
    return value == expected or value.endswith(f" > {expected}")


def _resolve_sources(sources: list[dict[str, str]], chunks: tuple[Chunk, ...]) -> set[str]:
    resolved: set[str] = set()
    for source in sources:
        for chunk in chunks:
            if chunk.document_id != source["document_id"]:
                continue
            section = source.get("section", "").strip()
            if section and not (
                _section_matches(chunk.location.section, section)
                or _section_matches(chunk.metadata.get("heading"), section)
            ):
                continue
            resolved.add(chunk.chunk_id)
    return resolved


def _synthetic_hit(chunk: Chunk, index: int) -> SearchHit:
    # Deliberately give early confounders a higher score. The selector must use
    # the claim/model boundary instead of trusting rank alone.
    score = max(0.55, 0.96 - index * 0.01)
    return SearchHit(chunk, score, score, score, score)


def _chunk_model(chunk: Chunk) -> str | None:
    model = str(chunk.metadata.get("model", "")).strip().upper()
    if not model or model in {"通用", "通用型号", "UNKNOWN", "ALL"}:
        return None
    return model


def _link_diagnostics(
    selected_ids: set[str],
    expected_ids: set[str],
    chunks_by_id: dict[str, Chunk],
) -> dict[str, int]:
    expected_models = {
        model
        for chunk_id in expected_ids
        if (chunk := chunks_by_id.get(chunk_id)) is not None
        if (model := _chunk_model(chunk)) is not None
    }
    wrong_ids = selected_ids - expected_ids
    cross_model = 0
    wrong_section = 0
    generic_source = 0
    for chunk_id in wrong_ids:
        chunk = chunks_by_id.get(chunk_id)
        model = _chunk_model(chunk) if chunk is not None else None
        if model is None:
            generic_source += 1
        elif expected_models and model not in expected_models:
            cross_model += 1
        elif model in expected_models:
            wrong_section += 1
    return {
        "cross_model_links": cross_model,
        "wrong_section_links": wrong_section,
        "generic_source_links": generic_source,
        "false_binding_links": len(wrong_ids),
        "model_scoped_links": len(selected_ids) if expected_models else 0,
    }


def _binding_pool(chunks: tuple[Chunk, ...]) -> tuple[SearchHit, ...]:
    model_chunks = [
        chunk
        for chunk in chunks
        if str(chunk.metadata.get("model", "")).strip().casefold()
        not in {"", "通用", "通用型号", "unknown", "all"}
        and str(chunk.location.section or "").endswith("1. 型号定位")
    ]
    return tuple(_synthetic_hit(chunk, index) for index, chunk in enumerate(model_chunks))


def _score_new(
    question: str,
    claims: list[dict[str, Any]],
    hits: tuple[SearchHit, ...],
    chunks: tuple[Chunk, ...],
) -> tuple[EvaluationCounts, float, list[dict[str, Any]]]:
    answer = "\n".join(claim["text"] for claim in claims)
    started = perf_counter()
    selected = select_claim_evidence(question, answer, hits, limit=max(1, len(claims)))
    duration_ms = (perf_counter() - started) * 1000
    counts = EvaluationCounts()
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    available_ids = {hit.chunk.chunk_id for hit in hits}
    details: list[dict[str, Any]] = []
    for claim, binding in zip(claims, selected, strict=True):
        expected = _resolve_sources(claim["supporting_sources"], chunks)
        selected_ids = {hit.chunk.chunk_id for hit in binding.hits}
        correct = selected_ids & expected
        diagnostics = _link_diagnostics(selected_ids, expected, chunks_by_id)
        conditional = bool(expected & available_ids)
        if expected:
            counts = EvaluationCounts(
                correct_links=counts.correct_links + len(correct),
                total_links=counts.total_links + len(selected_ids),
                supported_claims=counts.supported_claims + bool(correct),
                expected_claims=counts.expected_claims + 1,
                unsupported_rejections=counts.unsupported_rejections,
                unsupported_claims=counts.unsupported_claims,
                cross_model_links=(
                    counts.cross_model_links + diagnostics["cross_model_links"]
                ),
                wrong_section_links=(
                    counts.wrong_section_links + diagnostics["wrong_section_links"]
                ),
                generic_source_links=(
                    counts.generic_source_links + diagnostics["generic_source_links"]
                ),
                false_binding_links=(
                    counts.false_binding_links + diagnostics["false_binding_links"]
                ),
                model_scoped_links=(
                    counts.model_scoped_links + diagnostics["model_scoped_links"]
                ),
                conditional_correct_links=(
                    counts.conditional_correct_links + (len(correct) if conditional else 0)
                ),
                conditional_total_links=(
                    counts.conditional_total_links
                    + (len(selected_ids) if conditional else 0)
                ),
                conditional_supported_claims=(
                    counts.conditional_supported_claims
                    + (bool(correct) if conditional else 0)
                ),
                conditional_expected_claims=(
                    counts.conditional_expected_claims + conditional
                ),
            )
        else:
            counts = EvaluationCounts(
                correct_links=counts.correct_links,
                total_links=counts.total_links + len(selected_ids),
                supported_claims=counts.supported_claims,
                expected_claims=counts.expected_claims,
                unsupported_rejections=counts.unsupported_rejections + (not selected_ids),
                unsupported_claims=counts.unsupported_claims + 1,
                cross_model_links=counts.cross_model_links,
                wrong_section_links=counts.wrong_section_links,
                generic_source_links=counts.generic_source_links,
                false_binding_links=counts.false_binding_links,
                model_scoped_links=counts.model_scoped_links,
                conditional_correct_links=counts.conditional_correct_links,
                conditional_total_links=counts.conditional_total_links,
                conditional_supported_claims=counts.conditional_supported_claims,
                conditional_expected_claims=counts.conditional_expected_claims,
            )
        details.append(
            {
                "claim": claim["text"],
                "expected_chunk_ids": sorted(expected),
                "selected_chunk_ids": sorted(selected_ids),
                "supported": bool(correct) if expected else not selected_ids,
                **diagnostics,
            }
        )
    return counts, duration_ms, details


def _score_legacy(
    question: str,
    claims: list[dict[str, Any]],
    hits: tuple[SearchHit, ...],
    chunks: tuple[Chunk, ...],
) -> tuple[EvaluationCounts, float, list[dict[str, Any]]]:
    answer = "\n".join(claim["text"] for claim in claims)
    started = perf_counter()
    selected = select_citation_hits(question, answer, hits, limit=max(1, len(claims)))
    duration_ms = (perf_counter() - started) * 1000
    selected_ids = {hit.chunk.chunk_id for hit in selected}
    expected_by_claim = [
        _resolve_sources(claim["supporting_sources"], chunks) for claim in claims
    ]
    expected_union = set().union(*expected_by_claim) if expected_by_claim else set()
    expected_claims = sum(bool(expected) for expected in expected_by_claim)
    unsupported_claims = len(expected_by_claim) - expected_claims
    supported_claims = sum(bool(expected & selected_ids) for expected in expected_by_claim)
    chunks_by_id = {chunk.chunk_id: chunk for chunk in chunks}
    diagnostics = _link_diagnostics(selected_ids, expected_union, chunks_by_id)
    available_ids = {hit.chunk.chunk_id for hit in hits}
    conditional = bool(expected_union & available_ids)
    counts = EvaluationCounts(
        correct_links=len(selected_ids & expected_union),
        total_links=len(selected_ids),
        supported_claims=supported_claims,
        expected_claims=expected_claims,
        unsupported_rejections=(unsupported_claims if not selected_ids else 0),
        unsupported_claims=unsupported_claims,
        cross_model_links=diagnostics["cross_model_links"] if expected_union else 0,
        wrong_section_links=diagnostics["wrong_section_links"] if expected_union else 0,
        generic_source_links=diagnostics["generic_source_links"] if expected_union else 0,
        false_binding_links=diagnostics["false_binding_links"] if expected_union else 0,
        model_scoped_links=diagnostics["model_scoped_links"] if expected_union else 0,
        conditional_correct_links=(
            len(selected_ids & expected_union) if conditional else 0
        ),
        conditional_total_links=len(selected_ids) if conditional else 0,
        conditional_supported_claims=supported_claims if conditional else 0,
        conditional_expected_claims=expected_claims if conditional else 0,
    )
    return counts, duration_ms, [
        {
            "answer_level_selected_chunk_ids": sorted(selected_ids),
            "answer_level_expected_chunk_ids": sorted(expected_union),
            **diagnostics,
        }
    ]


def _merge(left: EvaluationCounts, right: EvaluationCounts) -> EvaluationCounts:
    return EvaluationCounts(
        **{
            field: getattr(left, field) + getattr(right, field)
            for field in EvaluationCounts.__dataclass_fields__
        }
    )


def _summary(counts: EvaluationCounts, latencies: list[float]) -> dict[str, Any]:
    ordered = sorted(latencies)
    p95_index = max(0, min(len(ordered) - 1, int(len(ordered) * 0.95) - 1))
    return {
        "claim_citation_precision": asdict(_metric(counts.correct_links, counts.total_links)),
        "claim_citation_recall": asdict(_metric(counts.supported_claims, counts.expected_claims)),
        "unsupported_claim_rejection_rate": asdict(
            _metric(counts.unsupported_rejections, counts.unsupported_claims)
        ),
        "cross_model_contamination_rate": asdict(
            _metric(counts.cross_model_links, counts.model_scoped_links)
        ),
        "wrong_section_rate": asdict(
            _metric(counts.wrong_section_links, counts.model_scoped_links)
        ),
        "generic_source_mismatch_rate": asdict(
            _metric(counts.generic_source_links, counts.model_scoped_links)
        ),
        "false_binding_rate": asdict(
            _metric(counts.false_binding_links, counts.model_scoped_links)
        ),
        "binding_conditional_precision": asdict(
            _metric(counts.conditional_correct_links, counts.conditional_total_links)
        ),
        "binding_conditional_recall": asdict(
            _metric(
                counts.conditional_supported_claims,
                counts.conditional_expected_claims,
            )
        ),
        "selector_latency_ms": {
            "p50": round(statistics.median(ordered), 4) if ordered else 0.0,
            "p95": round(ordered[p95_index], 4) if ordered else 0.0,
        },
    }


async def evaluate(corpus_path: Path, dataset_path: Path) -> dict[str, Any]:
    embeddings = FixedEmbedding()
    vector_store = InMemoryVectorStore()
    keyword_index = BM25KeywordIndex()
    indexer = KnowledgeIndexer(DocumentChunker(), embeddings, vector_store, keyword_index)
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
    chunks = vector_store.snapshot()
    retriever = MultiQueryRetriever(
        HybridRetriever(
            embeddings,
            vector_store,
            keyword_index,
            LexicalReranker(),
            candidate_limit=20,
            result_limit=10,
        ),
        DeterministicQueryRewriter(),
        result_limit=10,
    )
    dataset = json.loads(dataset_path.read_text(encoding="utf-8"))
    if dataset.get("schema_version") != 1:
        raise ValueError("unsupported claim citation dataset schema")
    binding_hits = _binding_pool(chunks)
    totals = {
        "binding": {"legacy": EvaluationCounts(), "claim": EvaluationCounts()},
        "end_to_end": {"legacy": EvaluationCounts(), "claim": EvaluationCounts()},
    }
    latencies = {
        "binding": {"legacy": [], "claim": []},
        "end_to_end": {"legacy": [], "claim": []},
    }
    cases: list[dict[str, Any]] = []
    retrieval_counts = RetrievalCounts()
    for case in dataset["cases"]:
        retrieval = await retriever.retrieve(case["question"])
        context_hits = select_context_hits(case["question"], retrieval.hits)
        context_ids = {hit.chunk.chunk_id for hit in context_hits}
        retrieval_details: list[dict[str, Any]] = []
        for claim in case["claims"]:
            expected = _resolve_sources(claim["supporting_sources"], chunks)
            if not expected:
                continue
            retrieved = bool(expected & context_ids)
            retrieval_counts = RetrievalCounts(
                retrieved_claims=retrieval_counts.retrieved_claims + retrieved,
                expected_claims=retrieval_counts.expected_claims + 1,
            )
            retrieval_details.append(
                {
                    "claim": claim["text"],
                    "expected_chunk_ids": sorted(expected),
                    "retrieved": retrieved,
                }
            )
        case_result: dict[str, Any] = {
            "case_id": case["case_id"],
            "retrieval": {
                "context_chunk_ids": sorted(context_ids),
                "context_hits": [
                    {
                        "chunk_id": hit.chunk.chunk_id,
                        "title": hit.chunk.title,
                        "model": _chunk_model(hit.chunk),
                        "section": (
                            hit.chunk.location.section
                            or str(hit.chunk.metadata.get("heading", ""))
                        ),
                        "score": round(hit.score, 6),
                    }
                    for hit in context_hits
                ],
                "claims": retrieval_details,
            },
            "modes": {},
        }
        for mode, hits in (("binding", binding_hits), ("end_to_end", context_hits)):
            case_result["modes"][mode] = {}
            for selector, scorer in (("legacy", _score_legacy), ("claim", _score_new)):
                count, latency, details = scorer(
                    case["question"], case["claims"], hits, chunks
                )
                totals[mode][selector] = _merge(totals[mode][selector], count)
                latencies[mode][selector].append(latency)
                case_result["modes"][mode][selector] = details
        cases.append(case_result)
    metrics = {
        mode: {
            selector: _summary(totals[mode][selector], latencies[mode][selector])
            for selector in ("legacy", "claim")
        }
        for mode in ("binding", "end_to_end")
    }
    metrics["end_to_end"]["retrieval"] = {
        "retrieval_candidate_recall": asdict(
            _metric(
                retrieval_counts.retrieved_claims,
                retrieval_counts.expected_claims,
            )
        )
    }
    return {
        "dataset": dataset_path.name,
        "corpus": corpus_path.name,
        "cases": len(dataset["cases"]),
        "external_model_calls": 0,
        "metrics": metrics,
        "case_results": cases,
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--corpus", type=Path, default=DEFAULT_CORPUS)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = asyncio.run(evaluate(args.corpus, args.dataset))
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(payload + "\n", encoding="utf-8")
    print(payload)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
