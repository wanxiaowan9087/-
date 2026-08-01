from __future__ import annotations

import pytest

from backend.app.rag.local_corpus import LocalTextCorpusRetriever


@pytest.mark.asyncio
async def test_local_text_corpus_retrieves_bundled_knowledge(tmp_path) -> None:
    (tmp_path / "maintenance.txt").write_text(
        "尘盒清理：每次清扫后检查并清空尘盒，滤网应定期晾干。",
        encoding="utf-8",
    )
    retriever = LocalTextCorpusRetriever(str(tmp_path))

    result = await retriever.retrieve("尘盒多久清理")

    assert result.has_evidence
    assert result.strategy == "bm25+local-text-corpus"
    assert result.hits[0].chunk.title == "maintenance"
    assert "尘盒" in result.hits[0].chunk.content
