"""Explicit LangGraph routing for memory-first, grounded answers.

The graph is intentionally small: it owns the decision about *where* an
answer may come from, while ``AgentRuntime`` continues to own policy,
generation, tracing and persistence.  Keeping those responsibilities apart
prevents a second agent implementation from drifting from the application
service.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from typing import Any, Literal, TypedDict, cast

from ..rag.models import RetrievalResult
from .contracts import MemoryContext

MemoryAnswer = Callable[[str, MemoryContext], str | None]
Retriever = Callable[[str], Awaitable[RetrievalResult]]
EvidencePolisher = Callable[[str, RetrievalResult], Awaitable[str]]
RouteName = Literal["memory", "knowledge", "insufficient"]
logger = logging.getLogger(__name__)


_CONTEXTUAL_SEASON_RE = re.compile(r"(春季|夏季|秋季|冬季)")
_DOMAIN_MARKERS = (
    "机器人",
    "扫地",
    "扫拖",
    "保养",
    "维护",
    "清洁",
    "滤网",
    "电池",
    "拖布",
    "型号",
    "产品",
)


def contextualize_retrieval_query(
    user_text: str, memory_context: MemoryContext
) -> str:
    """Carry the subject of an elliptical follow-up into knowledge retrieval.

    The complete short-term window is still passed to the model.  This small
    adapter only helps RAG when a user sends a clearly elliptical follow-up
    such as ``秋季呢`` after asking about robot maintenance in summer.  It
    deliberately uses the latest earlier user turn and removes seasonal words
    from that turn so the new season remains the active retrieval constraint.
    """
    current = re.sub(r"\s+", " ", user_text).strip()
    if not current:
        return current

    compact = re.sub(r"\s+", "", current)
    has_season = bool(_CONTEXTUAL_SEASON_RE.search(compact))
    is_short_followup = len(compact) <= 16 and (
        compact.endswith(("呢", "吗", "？", "?")) or compact.startswith(("那", "再", "还有"))
    )
    lacks_subject = not any(marker in compact for marker in _DOMAIN_MARKERS)
    if not ((has_season and lacks_subject) or is_short_followup):
        return current

    previous_user = next(
        (
            message
            for message in reversed(memory_context.window)
            if message.role == "user"
            and message.content.strip()
            and re.sub(r"\s+", "", message.content) != compact
        ),
        None,
    )
    if previous_user is None:
        return current

    subject = _CONTEXTUAL_SEASON_RE.sub("", previous_user.content)
    subject = re.sub(r"\s+", " ", subject).strip(" ，,。！？!?；;")
    if not subject:
        return current
    return f"{subject} {current}"[:240]


class RouteState(TypedDict, total=False):
    user_text: str
    memory_context: MemoryContext
    memory_answer: str | None
    retrieval: RetrievalResult | None
    polished_context: str | None
    route: RouteName


class MemoryKnowledgeRoute:
    """Compile and invoke the memory -> knowledge -> evidence graph."""

    def __init__(
        self,
        *,
        answer_memory: MemoryAnswer,
        retrieve: Retriever,
        polish: EvidencePolisher | None = None,
    ) -> None:
        self._answer_memory = answer_memory
        self._retrieve = retrieve
        self._polish = polish

    async def _try_polish(
        self, user_text: str, retrieval: RetrievalResult
    ) -> str | None:
        if self._polish is None:
            return None
        try:
            polished = (await self._polish(user_text, retrieval)).strip()
        except Exception:
            logger.warning(
                "evidence polish degraded; retaining raw retrieval",
                exc_info=True,
                extra={"component": "evidence-polisher"},
            )
            return None
        return polished or None

    async def invoke(self, user_text: str, memory_context: MemoryContext) -> RouteState:
        try:
            from langgraph.graph import END, START, StateGraph
        except ImportError:
            # The optional AI extra is not needed by lightweight unit tests.
            # Production images install it; this fallback preserves the same
            # semantics when only the core dependencies are present.
            answer = self._answer_memory(user_text, memory_context)
            if answer is not None:
                return {
                    "user_text": user_text,
                    "memory_context": memory_context,
                    "memory_answer": answer,
                    "retrieval": None,
                    "polished_context": None,
                    "route": "memory",
                }
            retrieval = await self._retrieve(
                contextualize_retrieval_query(user_text, memory_context)
            )
            polished = (
                await self._try_polish(user_text, retrieval)
                if retrieval.has_evidence
                else None
            )
            return {
                "user_text": user_text,
                "memory_context": memory_context,
                "memory_answer": None,
                "retrieval": retrieval,
                "polished_context": polished,
                "route": "knowledge" if retrieval.has_evidence else "insufficient",
            }

        async def memory_context_node(_: RouteState) -> RouteState:
            return {"memory_context": memory_context}

        def memory_intent_node(state: RouteState) -> RouteState:
            answer = self._answer_memory(user_text, state["memory_context"])
            return {
                "memory_answer": answer,
                "route": "memory" if answer is not None else "knowledge",
            }

        async def knowledge_node(state: RouteState) -> RouteState:
            query = contextualize_retrieval_query(user_text, state["memory_context"])
            return {"retrieval": await self._retrieve(query)}

        async def polish_node(state: RouteState) -> RouteState:
            retrieval = state.get("retrieval")
            if retrieval is None or not retrieval.has_evidence:
                return {"polished_context": None}
            return {"polished_context": await self._try_polish(user_text, retrieval)}

        def evidence_gate_node(state: RouteState) -> RouteState:
            retrieval = state.get("retrieval")
            return {
                "route": "knowledge"
                if retrieval and retrieval.has_evidence
                else "insufficient"
            }

        def route_after_memory(state: RouteState) -> str:
            return "memory" if state.get("route") == "memory" else "knowledge"

        def route_after_evidence(state: RouteState) -> str:
            return "knowledge" if state.get("route") == "knowledge" else "insufficient"

        # LangGraph's current generic stubs cannot express a TypedDict that
        # contains our domain dataclasses on every supported release.  Keep
        # the integration boundary typed as RouteState while leaving the
        # third-party builder itself dynamic.
        graph: Any = StateGraph(RouteState)
        graph.add_node("memory_context", memory_context_node)
        graph.add_node("memory_intent", memory_intent_node)
        graph.add_node("knowledge_retrieval", knowledge_node)
        graph.add_node("evidence_gate", evidence_gate_node)
        graph.add_node("evidence_polish", polish_node)
        graph.add_edge(START, "memory_context")
        graph.add_edge("memory_context", "memory_intent")
        graph.add_conditional_edges(
            "memory_intent",
            route_after_memory,
            {"memory": END, "knowledge": "knowledge_retrieval"},
        )
        graph.add_edge("knowledge_retrieval", "evidence_gate")
        graph.add_conditional_edges(
            "evidence_gate",
            route_after_evidence,
            {"knowledge": "evidence_polish", "insufficient": END},
        )
        graph.add_edge("evidence_polish", END)
        result = await graph.compile().ainvoke(
            {"user_text": user_text, "memory_context": memory_context}
        )
        return cast(RouteState, result)


async def route_memory_then_knowledge(
    user_text: str,
    memory_context: MemoryContext,
    *,
    answer_memory: MemoryAnswer,
    retrieve: Retriever,
    polish: EvidencePolisher | None = None,
) -> RouteState:
    """Convenience entry point used by ``AgentRuntime`` and tests."""

    return await MemoryKnowledgeRoute(
        answer_memory=answer_memory, retrieve=retrieve, polish=polish
    ).invoke(user_text, memory_context)
