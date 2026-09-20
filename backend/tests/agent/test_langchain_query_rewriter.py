from __future__ import annotations

import asyncio

from backend.app.adapters.llm.query_rewriter import LangChainQueryRewriter


class Response:
    def __init__(self, content: str) -> None:
        self.content = content


class RewriteModel:
    def __init__(self, content: str, *, delay: float = 0.0) -> None:
        self.content = content
        self.delay = delay

    async def ainvoke(self, _prompt: str) -> Response:
        if self.delay:
            await asyncio.sleep(self.delay)
        return Response(self.content)


async def test_llm_query_rewriter_keeps_original_and_bounded_expansions() -> None:
    rewriter = LangChainQueryRewriter(
        RewriteModel('["回充失败排查", "充电座定位故障", "重复项", "额外查询"]'),
        timeout_seconds=0.2,
    )

    plan = await rewriter.rewrite("机子老是找不到家咋办")

    assert plan.strategy == "llm-query-rewrite"
    assert plan.queries == (
        "机子老是找不到家咋办",
        "回充失败排查",
        "充电座定位故障",
        "重复项",
    )


async def test_llm_query_rewriter_timeout_falls_back_to_zero_token_normalization() -> None:
    rewriter = LangChainQueryRewriter(
        RewriteModel('["不应返回"]', delay=0.05),
        timeout_seconds=0.001,
    )

    plan = await rewriter.rewrite("机子咋办")

    assert plan.strategy == "deterministic-dual-query-normalization"
    assert plan.queries[0] == "机子咋办"
    assert "机器人" in plan.queries[1]
