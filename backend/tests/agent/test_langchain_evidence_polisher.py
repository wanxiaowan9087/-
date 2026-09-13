from __future__ import annotations

import pytest

from backend.app.adapters.llm.langchain_react import LangChainEvidencePolisher
from backend.app.rag.models import Chunk, DocumentType, RetrievalResult, SearchHit


class _Response:
    def __init__(self, content: object) -> None:
        self.content = content


class _Model:
    def __init__(self, content: object) -> None:
        self.content = content
        self.prompts: list[str] = []

    async def ainvoke(self, prompt: str) -> _Response:
        self.prompts.append(prompt)
        return _Response(self.content)


def _retrieval() -> RetrievalResult:
    chunk = Chunk(
        document_id="00000000-0000-0000-0000-000000000222",
        document_version="v1",
        chunk_id="chunk-1",
        title="S8 皓月",
        source="file://data/catalog/robots.md",
        content="S8 皓月支持自动集尘，适合大户型。",
        document_type=DocumentType.MARKDOWN,
    )
    return RetrievalResult(
        hits=(SearchHit(chunk, 0.9, 0.8, 0.9),), confidence=0.9
    )


@pytest.mark.asyncio
async def test_polisher_asks_for_grounded_natural_chinese_summary() -> None:
    model = _Model("1. 支持自动集尘。\n2. 适合大户型。")
    result = await LangChainEvidencePolisher(model=model)("推荐适合大户型的型号", _retrieval())

    assert "自动集尘" in result
    assert len(model.prompts) == 1
    prompt = model.prompts[0]
    assert "只保留资料中能够直接支持的事实" in prompt
    assert "推荐适合大户型的型号" in prompt
    assert "S8 皓月" in prompt
    assert "不要输出文档 ID" in prompt


@pytest.mark.asyncio
async def test_polisher_rejects_empty_provider_response() -> None:
    model = _Model("   ")
    with pytest.raises(ValueError, match="empty content"):
        await LangChainEvidencePolisher(model=model)("问题", _retrieval())
