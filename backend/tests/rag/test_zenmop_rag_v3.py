from __future__ import annotations

from collections import Counter

from scripts.build_zenmop_rag_v3 import build_payloads


def test_v3_covers_all_24_documents_with_200_unique_cases() -> None:
    corpus, cases, support, manifest = build_payloads()

    document_ids = {str(item["document_id"]) for item in corpus}
    case_ids = [str(item["case_id"]) for item in cases]
    covered_documents = {
        str(source["document_id"])
        for case in cases
        if case["should_answer"]
        for source in case["expected_sources"]
    }

    assert len(corpus) == 24
    assert len(document_ids) == 24
    assert len(cases) == 200
    assert len(set(case_ids)) == 200
    assert covered_documents == document_ids
    assert manifest["case_count"] == 200
    assert len(manifest["documents"]) == 24


def test_v3_support_annotations_match_every_answerable_case() -> None:
    _corpus, cases, support, _manifest = build_payloads()

    answerable = {str(case["case_id"]) for case in cases if case["should_answer"]}
    unanswerable = {str(case["case_id"]) for case in cases if not case["should_answer"]}
    annotated = set(support["cases"])

    assert annotated == answerable
    assert not (annotated & unanswerable)
    for case_id, claims in support["cases"].items():
        assert claims, case_id
        assert all(claim["supporting_sources"] for claim in claims)


def test_v3_distribution_is_derived_from_case_tags() -> None:
    _corpus, cases, _support, manifest = build_payloads()

    primary_tags = Counter(str(case["tags"][0]) for case in cases)

    assert manifest["distribution"] == dict(sorted(primary_tags.items()))
    assert sum(manifest["distribution"].values()) == 200
