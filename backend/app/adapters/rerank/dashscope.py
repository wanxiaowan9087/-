from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import httpx

from backend.app.rag.models import SearchHit

DASHSCOPE_RERANK_ENDPOINT = (
    "https://dashscope.aliyuncs.com/api/v1/services/"
    "rerank/text-rerank/text-rerank"
)


class DashScopeRerankError(RuntimeError):
    """Provider returned an unusable response without exposing response data."""


class DashScopeReranker:
    """Rank fused retrieval candidates with DashScope's text reranker."""

    def __init__(
        self,
        client: httpx.AsyncClient,
        *,
        api_key: str,
        model: str,
        endpoint: str = DASHSCOPE_RERANK_ENDPOINT,
    ) -> None:
        self._client = client
        self._api_key = api_key
        self._model = model
        self._endpoint = endpoint

    async def rerank(
        self,
        query: str,
        hits: Sequence[SearchHit],
        limit: int,
    ) -> Sequence[SearchHit]:
        if not hits or limit < 1:
            return ()
        top_n = min(limit, len(hits))
        response = await self._client.post(
            self._endpoint,
            headers={"Authorization": f"Bearer {self._api_key}"},
            json={
                "model": self._model,
                "input": {
                    "query": query,
                    "documents": [hit.chunk.content for hit in hits],
                },
                "parameters": {
                    "top_n": top_n,
                    "return_documents": True,
                },
            },
        )
        response.raise_for_status()
        try:
            payload: Any = response.json()
            output = payload["output"]
            results = output["results"]
            if not isinstance(results, list):
                raise TypeError
            ranked: list[SearchHit] = []
            seen_indexes: set[int] = set()
            for item in results[:top_n]:
                if not isinstance(item, Mapping):
                    raise TypeError
                index = item["index"]
                score = item["relevance_score"]
                if (
                    not isinstance(index, int)
                    or isinstance(index, bool)
                    or not 0 <= index < len(hits)
                    or index in seen_indexes
                ):
                    raise ValueError
                normalized_score = float(score)
                if not 0.0 <= normalized_score <= 1.0:
                    raise ValueError
                ranked.append(replace(hits[index], rerank_score=normalized_score))
                seen_indexes.add(index)
            if not ranked:
                raise ValueError
        except (KeyError, TypeError, ValueError) as error:
            raise DashScopeRerankError("DashScope rerank response is invalid") from error
        return tuple(ranked)
