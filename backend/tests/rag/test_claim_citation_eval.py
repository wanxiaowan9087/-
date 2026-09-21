from __future__ import annotations

import pytest
from evals.run_claim_citation_eval import (
    DEFAULT_CORPUS,
    DEFAULT_DATASET,
    evaluate,
)


@pytest.mark.asyncio
async def test_claim_citation_eval_distinguishes_retrieval_and_binding_failures() -> None:
    report = await evaluate(DEFAULT_CORPUS, DEFAULT_DATASET)

    retrieval = report["metrics"]["end_to_end"]["retrieval"]
    claim = report["metrics"]["end_to_end"]["claim"]

    assert retrieval["retrieval_candidate_recall"]["denominator"] == 18
    assert "wrong_section_rate" in claim
    assert "false_binding_rate" in claim
    assert "binding_conditional_precision" in claim
    assert "generic_source_mismatch_rate" in claim

    obsidian = next(
        case for case in report["case_results"] if case["case_id"] == "x9-obsidian-feature"
    )
    detail = obsidian["modes"]["end_to_end"]["claim"][0]
    assert detail["cross_model_links"] == 0
    assert detail["wrong_section_links"] == 0
    assert detail["supported"] is True
