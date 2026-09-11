from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest
from evals.run_rag_eval import (
    DEFAULT_DATASET,
    DEFAULT_SUPPORT_ANNOTATIONS,
    SupportingSource,
    _load_support_annotations,
    _read_jsonl,
    _resolve_expected_chunks,
    _score_citation_support,
    evaluate,
)

from backend.app.rag.citations import CitationService


def test_support_annotations_cover_every_answerable_case() -> None:
    cases = _read_jsonl(DEFAULT_DATASET)
    annotations = _load_support_annotations(DEFAULT_SUPPORT_ANNOTATIONS, cases)

    assert {case["case_id"] for case in cases if case["should_answer"]} == set(annotations)
    assert all(
        source.document_version == "v1" for sources in annotations.values() for source in sources
    )


@pytest.mark.asyncio
async def test_evaluator_scores_claim_bound_full_source_identity() -> None:
    report = await evaluate()

    metric = report["metrics"]["citation_support_precision"]
    assert metric["value"] == 1.0
    assert metric["denominator"] >= 16
    assert all(
        not case["citation_support_failures"] for case in report["cases"] if case["answered"]
    )


@pytest.mark.asyncio
async def test_wrong_document_version_is_not_supported() -> None:
    report = await evaluate()
    first_answered = next(case for case in report["cases"] if case["answered"])
    assert first_answered["supported_claim_ids"]

    from evals.run_rag_eval import DEFAULT_CORPUS

    from backend.app.adapters.vector.fake import FixedEmbedding, InMemoryVectorStore
    from backend.app.rag.chunking import DocumentChunker
    from backend.app.rag.ingestion import KnowledgeIndexer
    from backend.app.rag.lexical import BM25KeywordIndex
    from backend.app.rag.models import DocumentRecord, DocumentType
    from backend.app.rag.retrieval import HybridRetriever, LexicalReranker

    embeddings = FixedEmbedding()
    vectors = InMemoryVectorStore()
    keywords = BM25KeywordIndex()
    indexer = KnowledgeIndexer(DocumentChunker(), embeddings, vectors, keywords)
    raw = _read_jsonl(DEFAULT_CORPUS)[0]
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
    retrieval = await HybridRetriever(
        embeddings,
        vectors,
        keywords,
        LexicalReranker(),
    ).retrieve(raw["content"])
    service = CitationService()
    citation = service.build(retrieval.hits, limit=1)[0]
    wrong_version = replace(citation, document_version="v999")
    expected = (
        SupportingSource(
            claim_id="claim",
            document_id=citation.document_id,
            document_version="v1",
            chunk_id=citation.chunk_id,
        ),
    )

    supported, _, failures = _score_citation_support(
        (wrong_version,),
        expected,
        service,
        [hit.chunk for hit in retrieval.hits],
    )

    assert supported == 0
    assert any(reason.startswith("version_mismatch:") for reason in failures)


def test_support_annotation_rejects_missing_answerable_case(tmp_path: Path) -> None:
    path = tmp_path / "support.json"
    path.write_text('{"schema_version": 1, "cases": {}}', encoding="utf-8")

    with pytest.raises(ValueError, match="should_answer"):
        _load_support_annotations(path, _read_jsonl(DEFAULT_DATASET))


def test_v2_dataset_has_frozen_distribution() -> None:
    root = Path(__file__).resolve().parents[3]
    cases = _read_jsonl(root / "evals" / "datasets" / "zenmop-rag-v2.jsonl")
    manifest = json.loads(
        (root / "evals" / "datasets" / "zenmop-rag-v2.manifest.json").read_text(encoding="utf-8")
    )

    assert len(cases) == 120
    assert len({case["case_id"] for case in cases}) == 120
    assert manifest["distribution"] == {
        "direct": 40,
        "multi_source": 20,
        "model_comparison": 15,
        "troubleshooting": 15,
        "rewrite_or_colloquial": 10,
        "unanswerable": 10,
        "parameter_boundary": 5,
        "prompt_injection": 5,
    }


@pytest.mark.asyncio
async def test_v2_section_selectors_survive_chunk_configuration_changes() -> None:
    root = Path(__file__).resolve().parents[3]
    corpus = _read_jsonl(root / "evals" / "corpus" / "zenmop-rag-v2.jsonl")
    case = next(
        item
        for item in _read_jsonl(root / "evals" / "datasets" / "zenmop-rag-v2.jsonl")
        if item["case_id"] == "direct-s8luna-feature"
    )
    from backend.app.rag.chunking import DocumentChunker
    from backend.app.rag.models import DocumentRecord, DocumentType

    for size, overlap in ((600, 80), (450, 60), (350, 50)):
        chunks = []
        for raw in corpus:
            chunks.extend(
                DocumentChunker(text_chunk_size=size, text_overlap=overlap).split(
                    DocumentRecord(
                        document_id=raw["document_id"],
                        version=raw["version"],
                        title=raw["title"],
                        source=raw["source"],
                        document_type=DocumentType(raw["document_type"]),
                        content=raw["content"],
                        metadata=raw["metadata"],
                    )
                )
            )
        resolved = _resolve_expected_chunks(case["expected_sources"], chunks)
        assert resolved
