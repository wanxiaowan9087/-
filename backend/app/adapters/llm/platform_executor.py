from __future__ import annotations

import asyncio
import re
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Protocol, cast
from uuid import UUID

from backend.app.adapters.mcp.robot_catalog import RobotCatalogMcpError, recommend_robots
from backend.app.agent.contracts import (
    AgentRequest,
    AgentRunResult,
    CatalogProduct,
    ConversationMode,
    ReportScope,
    RunStatus,
)
from backend.app.agent.runtime import is_catalog_inventory_intent
from backend.app.agent.tooling import CancellationToken
from backend.app.application.ports import RunExecution

_ANSWER_REVEAL_DELAY_SECONDS = 1.0


def _jsonable(value: Any) -> Any:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if is_dataclass(value):
        return {key: _jsonable(item) for key, item in asdict(cast(Any, value)).items()}
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, tuple | list):
        return [_jsonable(item) for item in value]
    return value


class AgentRuntimePort(Protocol):
    async def execute(
        self,
        request: AgentRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult: ...


class RuntimeRunExecutor:
    """Adapts the AI runtime result to the platform's durable event seam."""

    def __init__(
        self, runtime: AgentRuntimePort, *, report_workflow: Any | None = None
    ) -> None:
        self._runtime = runtime
        self._report_workflow = report_workflow
        self.knowledge_indexer: Any | None = None
        self._tokens: dict[UUID, CancellationToken] = {}
        self._outcomes: dict[UUID, Any] = {}
        self._lock = asyncio.Lock()

    async def stream(self, execution: RunExecution) -> AsyncIterator[tuple[str, dict[str, Any]]]:
        token = CancellationToken()
        async with self._lock:
            self._tokens[execution.run_id] = token
        report = None
        recommendations = ()
        catalog_products: tuple[CatalogProduct, ...] = ()
        try:
            if _is_robot_recommendation_intent(execution.input_content):
                # Fetch the complete small catalog first. The final answer is
                # the source of truth for which cards are safe to display.
                recommendations = await recommend_robots(execution.input_content, limit=6)
                # MCP is the authoritative read-only catalog.  Always pass its
                # structured records into the runtime, not only for inventory
                # count questions, so recommendation answers are grounded by
                # the same records used to render product cards.
                catalog_products = tuple(
                    CatalogProduct(
                        product_id=item.product_id,
                        model=item.model,
                        name=item.name,
                        price=item.price,
                        highlights=item.highlights,
                        recommended_for=item.recommended_for,
                        colors=item.colors,
                    )
                    for item in recommendations
                )
        except RobotCatalogMcpError:
            recommendations = ()
        try:
            if self._report_workflow is not None and self._report_workflow.is_report_intent(
                execution.input_content
            ):
                report = await self._report_workflow.prepare(
                    subject_id=execution.subject_id,
                    user_text=execution.input_content,
                    cancellation=token,
                )
                start_at, end_at = _month_bounds(report.month)
        except Exception:
            yield (
                "error",
                {
                    "code": "TOOL_FAILED",
                    "message": "monthly report data is unavailable for this account",
                    "retryable": False,
                    "retry_after_seconds": None,
                },
            )
            return
        request = AgentRequest(
            request_id=execution.request_id,
            session_id=str(execution.session_id),
            subject_id=execution.subject_id,
            user_message_id=str(execution.user_message_id),
            user_text=execution.input_content,
            run_id=str(execution.run_id),
            mode=(
                ConversationMode.REPORT
                if report is not None
                else ConversationMode.CHAT
            ),
            report_scope=(
                ReportScope(execution.subject_id, start_at, end_at)
                if report is not None
                else None
            ),
            report_context=report.context if report is not None else None,
            report_tool_executions=report.tool_executions if report is not None else (),
            catalog_products=catalog_products,
        )
        try:
            for phase in (
                "accepted",
                "preparing_context",
                "retrieving",
                "reasoning",
                "generating",
                "checking_policy",
            ):
                yield "status", {"phase": phase, "detail": None}
            streamed_parts: list[str] = []
            if bool(getattr(self._runtime, "supports_token_streaming", False)):
                token_queue: asyncio.Queue[str] = asyncio.Queue()
                result_task = asyncio.create_task(
                    cast(Any, self._runtime).execute(
                        request,
                        cancellation=token,
                        on_token=token_queue.put,
                    )
                )
                pending = ""
                delta_index = 0
                while not result_task.done() or not token_queue.empty():
                    timed_out = False
                    try:
                        pending += await asyncio.wait_for(token_queue.get(), timeout=0.04)
                    except TimeoutError:
                        timed_out = True
                    while len(pending) >= 12:
                        content, pending = pending[:12], pending[12:]
                        if not streamed_parts:
                            await asyncio.sleep(_ANSWER_REVEAL_DELAY_SECONDS)
                        streamed_parts.append(content)
                        yield "delta", {"index": delta_index, "content": content}
                        delta_index += 1
                    if pending and (
                        timed_out or (result_task.done() and token_queue.empty())
                    ):
                        if not streamed_parts:
                            await asyncio.sleep(_ANSWER_REVEAL_DELAY_SECONDS)
                        streamed_parts.append(pending)
                        yield "delta", {"index": delta_index, "content": pending}
                        delta_index += 1
                        pending = ""
                result = await result_task
            else:
                result = await self._runtime.execute(request, cancellation=token)
            if (
                not recommendations
                and result.status is RunStatus.COMPLETED
                and _answer_mentions_known_product(result.public_content)
            ):
                try:
                    # Follow-up questions are often elliptical (for example,
                    # “其他同类产品呢”) and therefore do not trigger the
                    # input-side recommendation intent. Hydrate every model
                    # explicitly named by the final answer from the catalog so
                    # its stable product_id/image_key card is still emitted.
                    recommendations = await recommend_robots(
                        result.public_content, limit=6
                    )
                except RobotCatalogMcpError:
                    recommendations = ()
            selected_recommendations = _filter_recommendations(
                recommendations, result.public_content
            )
            # A recommendation answer may be phrased generically (for
            # example, “按家庭场景选择即可”) even though the MCP lookup has
            # already produced authoritative candidates. Keep the image cards
            # attached to those candidates instead of dropping them entirely;
            # product_id remains the only key the frontend uses for images.
            if (
                not selected_recommendations
                and recommendations
                and _is_robot_recommendation_intent(execution.input_content)
                and result.status is RunStatus.COMPLETED
            ):
                selected_recommendations = recommendations[:3]
            for recommendation in selected_recommendations:
                yield "product_recommendation", _jsonable(recommendation)
            async with self._lock:
                self._outcomes[execution.run_id] = result
            for citation in result.citations:
                yield "citation", _jsonable(citation)
            if result.status is RunStatus.NEEDS_REVIEW:
                yield (
                    "review_required",
                    {
                        "review_id": result.review_id,
                        "reason_codes": [reason.value for reason in result.review_reasons],
                        "confidence": result.confidence,
                        "draft_withheld": True,
                    },
                )
                yield (
                    "done",
                    {
                        "outcome": "needs_review",
                        "assistant_message_id": None,
                        "finish_reason": "needs_review",
                        "usage": None,
                    },
                )
            elif result.status is RunStatus.COMPLETED:
                if not streamed_parts:
                    chunks = _chunks(result.public_content)
                    if chunks:
                        await asyncio.sleep(_ANSWER_REVEAL_DELAY_SECONDS)
                    for index, content in enumerate(chunks):
                        yield "delta", {"index": index, "content": content}
                yield (
                    "done",
                    {
                        "outcome": "completed",
                        "assistant_message_id": str(execution.assistant_message_id),
                        "finish_reason": "stop",
                        "usage": None,
                    },
                )
            elif result.status is RunStatus.CANCELLED:
                yield (
                    "done",
                    {
                        "outcome": "cancelled",
                        "assistant_message_id": None,
                        "finish_reason": "user_cancelled",
                        "usage": None,
                    },
                )
            else:
                yield (
                    "error",
                    {
                        "code": result.error_code.value if result.error_code else "INTERNAL_ERROR",
                        "message": "run execution failed",
                        "retryable": False,
                        "retry_after_seconds": None,
                    },
                )
        finally:
            async with self._lock:
                self._tokens.pop(execution.run_id, None)

    async def request_cancel(self, run_id: UUID) -> None:
        async with self._lock:
            token = self._tokens.get(run_id)
        if token is not None:
            token.cancel()

    async def get_outcome(self, run_id: UUID) -> Any | None:
        async with self._lock:
            return self._outcomes.pop(run_id, None)

    async def health(self) -> str:
        return "available"

    async def close(self) -> None:
        async with self._lock:
            for token in self._tokens.values():
                token.cancel()
            self._tokens.clear()
            self._outcomes.clear()


def _chunks(content: str, *, size: int = 12) -> list[str]:
    """Emit small transport chunks; the browser animates them character-by-character."""
    if size < 1:
        raise ValueError("chunk size must be positive")
    return [content[index : index + size] for index in range(0, len(content), size)]


_PRODUCT_ALIASES: dict[str, tuple[str, ...]] = {
    "s8-luna": ("s8-luna", "s8 luna", "s8皓月", "皓月"),
    "s8-air": ("s8-air", "s8 air", "s8轻羽", "轻羽"),
    "x9-obsidian": ("x9-obsidian", "x9 obsidian", "x9曜石", "曜石"),
    "x9-edge": ("x9-edge", "x9 edge"),
    "m6-terra": ("m6-terra", "m6 terra", "m6陶土", "陶土", "m6霞陶", "霞陶"),
    "m6-mini": ("m6-mini", "m6 mini", "m6小径", "小径"),
}


def _filter_recommendations(
    recommendations: tuple[Any, ...], answer: str
) -> tuple[Any, ...]:
    """Only emit cards for canonical products explicitly named by the answer."""
    compact = re.sub(r"[\s\-_]", "", answer.casefold())
    selected = []
    for recommendation in recommendations:
        aliases = _PRODUCT_ALIASES.get(recommendation.product_id, (recommendation.product_id,))
        if any(re.sub(r"[\s\-_]", "", alias.casefold()) in compact for alias in aliases):
            selected.append(recommendation)
    return tuple(selected)


def _answer_mentions_known_product(answer: str) -> bool:
    compact = re.sub(r"[\s\-_]", "", answer.casefold())
    return any(
        re.sub(r"[\s\-_]", "", alias.casefold()) in compact
        for aliases in _PRODUCT_ALIASES.values()
        for alias in aliases
    )


def _month_bounds(month: str) -> tuple[datetime, datetime]:
    year, number = (int(value) for value in month.split("-", 1))
    start = datetime(year, number, 1, tzinfo=UTC)
    end = (
        datetime(year + 1, 1, 1, tzinfo=UTC)
        if number == 12
        else datetime(year, number + 1, 1, tzinfo=UTC)
    )
    return start, end


def _is_robot_recommendation_intent(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    robot_subject = any(
        term in compact
        for term in ("机器人", "扫地机", "扫拖机", "扫拖机器人", "型号", "机型")
    )
    # Product-selection questions are often phrased without the word
    # “机器人” (for example, “我家最适合哪一款”).  Keep this list narrow so
    # unrelated requests such as “推荐一部电影” still use the normal route.
    recommendation_request = any(
        term in compact
        for term in (
            "推荐",
            "建议买",
            "选购",
            "买什么",
            "买哪款",
            "哪个型号",
            "什么型号",
            "适合我",
            "最适合",
            "哪一款",
            "哪款",
            "哪一个",
            "哪个更适合",
            "应该选",
            "帮我选",
            "适合什么家庭",
        )
    )
    # A known model name makes an otherwise short suitability question
    # unambiguous (e.g. “S8 皓月适合什么家庭”).
    known_model = any(
        re.sub(r"[\s\-_]", "", alias.casefold()) in compact
        for aliases in _PRODUCT_ALIASES.values()
        for alias in aliases
        if len(re.sub(r"[\s\-_]", "", alias)) >= 3
    )
    color_followup = any(
        color in compact
        for color in (
            "白色", "白的", "黑色", "黑的", "灰色", "灰的", "月白", "云白", "曜石黑", "岩灰"
        )
    ) and any(
        marker in compact for marker in ("喜欢", "偏好", "想要", "想选", "颜色")
    )
    inventory_request = is_catalog_inventory_intent(compact)
    explicit_product_request = "产品" in compact and recommendation_request
    return (
        (robot_subject and recommendation_request)
        or explicit_product_request
        or (known_model and recommendation_request)
        or (not robot_subject and recommendation_request and any(
            marker in compact for marker in ("适合", "选择", "选一", "哪一", "哪款", "哪一个")
        ))
        or color_followup
        or inventory_request
    )
