from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Sequence

from .models import Chunk, ScoredChunk

_WORD = re.compile(r"[a-z0-9]+|[\u4e00-\u9fff]+", re.IGNORECASE)


def tokenize(text: str) -> tuple[str, ...]:
    tokens: list[str] = []
    for match in _WORD.finditer(text.lower()):
        value = match.group(0)
        if value and "\u4e00" <= value[0] <= "\u9fff":
            if len(value) == 1:
                tokens.append(value)
            else:
                tokens.extend(value[index : index + 2] for index in range(len(value) - 1))
        else:
            tokens.append(value)
    return tuple(tokens)


class BM25KeywordIndex:
    """Deterministic in-process BM25 adapter for tests and small deployments."""

    def __init__(
        self,
        chunks: Sequence[Chunk] = (),
        *,
        k1: float = 1.5,
        b: float = 0.75,
    ) -> None:
        self._k1 = k1
        self._b = b
        self._chunks: dict[str, Chunk] = {
            chunk.chunk_id: chunk for chunk in chunks
        }

    async def replace_document(
        self,
        document_id: str,
        document_version: str,
        chunks: Sequence[Chunk],
    ) -> None:
        stale = [
            chunk_id
            for chunk_id, chunk in self._chunks.items()
            if chunk.document_id == document_id
        ]
        for chunk_id in stale:
            del self._chunks[chunk_id]
        self._chunks.update({chunk.chunk_id: chunk for chunk in chunks})

    async def search(self, query: str, limit: int) -> Sequence[ScoredChunk]:
        query_terms = tuple(dict.fromkeys(tokenize(query)))
        if not query_terms or not self._chunks or limit <= 0:
            return ()
        documents = tuple(self._chunks.values())
        tokenized = [tokenize(chunk.content) for chunk in documents]
        average_length = sum(map(len, tokenized)) / max(1, len(tokenized))
        document_frequency = {
            term: sum(term in terms for terms in tokenized)
            for term in query_terms
        }
        ranked: list[ScoredChunk] = []
        for chunk, terms in zip(documents, tokenized, strict=False):
            counts = Counter(terms)
            raw_score = 0.0
            matched = 0
            for term in query_terms:
                frequency = counts[term]
                if not frequency:
                    continue
                matched += 1
                frequency_docs = document_frequency[term]
                inverse_frequency = math.log(
                    1
                    + (len(documents) - frequency_docs + 0.5)
                    / (frequency_docs + 0.5)
                )
                denominator = frequency + self._k1 * (
                    1
                    - self._b
                    + self._b * len(terms) / max(1.0, average_length)
                )
                raw_score += (
                    inverse_frequency
                    * frequency
                    * (self._k1 + 1)
                    / denominator
                )
            if not matched:
                continue
            coverage = matched / len(query_terms)
            saturation = raw_score / (raw_score + 4.0)
            score = min(1.0, 0.72 * coverage + 0.28 * saturation)
            ranked.append(ScoredChunk(chunk=chunk, score=score))
        ranked.sort(key=lambda item: (-item.score, item.chunk.chunk_id))
        return tuple(ranked[:limit])
