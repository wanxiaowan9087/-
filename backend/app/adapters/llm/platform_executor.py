from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from dataclasses import asdict, is_dataclass
from datetime import UTC, date, datetime
from enum import Enum
from typing import Any, Protocol, cast
from uuid import UUID

from backend.app.agent.contracts import (
    AgentRequest,
    AgentRunResult,
    ConversationMode,
    ReportScope,
    RunStatus,
)
from backend.app.agent.tooling import CancellationToken
from backend.app.application.ports import RunExecution
from backend.app.adapters.mcp.robot_catalog import RobotCatalogMcpError, recommend_robots


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
        try:
            if _is_robot_recommendation_intent(execution.input_content):
                recommendations = await recommend_robots(execution.input_content)
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
            result = await self._runtime.execute(request, cancellation=token)
            for recommendation in recommendations:
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
                for index, content in enumerate(_chunks(result.public_content)):
                    yield "delta", {"index": index, "content": content}
                    # Persisting each small delta also gives the browser time
                    # to paint, rather than replacing an empty answer at once.
                    await asyncio.sleep(0.018)
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


def _chunks(content: str, size: int = 24) -> list[str]:
    return [content[index : index + size] for index in range(0, len(content), size)]


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
    compact = text.replace(" ", "").lower()
    return any(term in compact for term in ("推荐机器人", "推荐扫地机器人", "买什么机器人", "机器人推荐", "选什么扫地机"))
