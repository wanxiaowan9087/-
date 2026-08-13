from __future__ import annotations

import json
from typing import Any

from backend.app.rag.query_rewrite import DeterministicQueryRewriter, QueryPlan, normalize_queries


class LangChainQueryRewriter:
    def __init__(self, model: Any, *, fallback: DeterministicQueryRewriter | None = None) -> None:
        self._model = model
        self._fallback = fallback or DeterministicQueryRewriter()

    async def rewrite(self, query: str) -> QueryPlan:
        prompt = (
            "将用户中文客服问题改写为最多3条知识库检索查询。保留型号、错误码、时间和部件名称；"
            "不要回答、不要补充事实。只返回 JSON 字符串数组。\n用户问题：" + query
        )
        try:
            response = await self._model.ainvoke(prompt)
            text = getattr(response, "content", response)
            raw = text if isinstance(text, str) else str(text)
            start, end = raw.find("["), raw.rfind("]")
            values = json.loads(raw[start : end + 1])
            if (
                start < 0
                or not isinstance(values, list)
                or not all(isinstance(item, str) for item in values)
            ):
                raise ValueError("invalid query rewrite response")
            return normalize_queries(query, values)
        except Exception:
            return await self._fallback.rewrite(query)
