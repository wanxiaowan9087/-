from __future__ import annotations

import csv
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Awaitable, Callable

from .contracts import ToolExecution, ToolOutcome, ToolResult
from .tooling import ToolDefinition, ToolExecutor, ToolRegistry


@dataclass(frozen=True)
class ReportIdentityInput:
    subject_id: str


@dataclass(frozen=True)
class ReportMonthInput:
    month: str


@dataclass(frozen=True)
class ReportContextInput:
    user_id: str
    month: str


@dataclass(frozen=True)
class ExternalDataInput:
    user_id: str
    month: str


def get_user_id(arguments: ReportIdentityInput) -> ToolResult:
    user_id = arguments.subject_id.strip()
    if not user_id:
        raise ValueError("authenticated subject id is required")
    return ToolResult(display_content=f"report user id resolved: {user_id}")


def get_report_month(arguments: ReportMonthInput) -> ToolResult:
    if not re.fullmatch(r"20\d{2}-(0[1-9]|1[0-2])", arguments.month):
        raise ValueError("report month must use YYYY-MM")
    return ToolResult(display_content=f"report month resolved: {arguments.month}")


def fill_context_for_report(arguments: ReportContextInput) -> ToolResult:
    if not arguments.user_id.strip() or not re.fullmatch(
        r"20\d{2}-(0[1-9]|1[0-2])", arguments.month
    ):
        raise ValueError("report context requires a valid user and month")
    return ToolResult(
        display_content="report context prepared for external usage lookup",
        internal_content={"user_id": arguments.user_id, "month": arguments.month},
    )


class ExternalUsageData:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._rows: dict[tuple[str, str], dict[str, str]] | None = None

    def fetch(self, arguments: ExternalDataInput) -> ToolResult:
        rows = self._load()
        row = rows.get((arguments.user_id, arguments.month))
        if row is None:
            raise LookupError("no usage report data for the requested user and month")
        return ToolResult(
            display_content="external usage record loaded",
            internal_content=row,
            metadata={"user_id": arguments.user_id, "month": arguments.month},
        )

    def available_months(self) -> tuple[str, ...]:
        return tuple(sorted({month for _, month in self._load()}))

    def _load(self) -> dict[tuple[str, str], dict[str, str]]:
        if self._rows is not None:
            return self._rows
        if not self._path.exists():
            raise FileNotFoundError(f"external usage data file not found: {self._path}")
        rows: dict[tuple[str, str], dict[str, str]] = {}
        for source in (self._path, self._path.with_name("records_extension.csv")):
            if not source.exists():
                continue
            with source.open("r", encoding="utf-8-sig", newline="") as handle:
                reader = csv.DictReader(handle, skipinitialspace=True)
                for row in reader:
                    user_id = (row.get("\u7528\u6237ID") or row.get("user_id") or "").strip().strip('"')
                    month = (row.get("\u65f6\u95f4") or row.get("month") or "").strip().strip('"')
                    if user_id and month:
                        rows[(user_id, month)] = {str(key): str(value or "").strip() for key, value in row.items()}
        self._rows = rows
        return rows


@dataclass(frozen=True)
class ReportPreparation:
    month: str
    context: str
    data: str
    tool_executions: tuple[ToolExecution, ...]


class ReportWorkflow:
    """Deterministic report preflight. Model cannot reorder or bypass these steps."""

    def __init__(self, data_path: str | Path, external_user_id_resolver: Callable[[str], Awaitable[str | None]] | None = None) -> None:
        self._data = ExternalUsageData(data_path)
        self._external_user_id_resolver = external_user_id_resolver

    def is_report_intent(self, text: str) -> bool:
        lowered = text.casefold()
        markers = (
            "report", "monthly", "usage summary", "usage report", "habit summary",
            "\u6708\u5ea6\u62a5\u544a", "\u4f7f\u7528\u62a5\u544a",
            "\u4f7f\u7528\u60c5\u51b5", "\u4e60\u60ef\u603b\u7ed3",
        )
        return any(marker in lowered for marker in markers)

    def parse_month(self, text: str) -> str:
        match = re.search(r"20\d{2}-(?:0[1-9]|1[0-2])", text)
        if match:
            return match.group(0)
        chinese = re.search(r"(20\d{2})\s*\u5e74\s*(\d{1,2})\s*\u6708", text)
        if chinese:
            return f"{chinese.group(1)}-{int(chinese.group(2)):02d}"
        months = self._data.available_months()
        if not months:
            raise LookupError("no report months are available")
        return months[-1]

    async def prepare(
        self, *, subject_id: str, user_text: str, cancellation: Any | None = None
    ) -> ReportPreparation:
        month = self.parse_month(user_text)
        external_user_id = subject_id
        if self._external_user_id_resolver is not None:
            external_user_id = await self._external_user_id_resolver(subject_id) or ""
            if not external_user_id:
                raise PermissionError("no authorized external report identity mapping exists")
        registry = ToolRegistry(
            (
                _definition("get_user_id", ReportIdentityInput, get_user_id),
                _definition("get_report_month", ReportMonthInput, get_report_month),
                _definition(
                    "fill_context_for_report", ReportContextInput, fill_context_for_report
                ),
                _definition(
                    "fetch_external_data", ExternalDataInput, self._data.fetch
                ),
            )
        )
        executor = ToolExecutor(registry)
        executions: list[ToolExecution] = []
        calls = (
            ("get_user_id", {"subject_id": external_user_id}),
            ("get_report_month", {"month": month}),
            ("fill_context_for_report", {"user_id": external_user_id, "month": month}),
            ("fetch_external_data", {"user_id": external_user_id, "month": month}),
        )
        result_data: dict[str, Any] | None = None
        for name, payload in calls:
            execution = await executor.execute(name, payload, cancellation=cancellation)
            executions.append(execution)
            if execution.outcome is not ToolOutcome.SUCCEEDED:
                raise RuntimeError(
                    f"report preflight failed at {name}: {execution.error_code}"
                )
            if name == "fetch_external_data":
                result_data = execution.internal_content
        assert result_data is not None
        context = "\n".join(f"{key}: {value}" for key, value in result_data.items())
        return ReportPreparation(month, context, context, tuple(executions))


def _definition(name: str, input_type: type[Any], handler: Any) -> ToolDefinition[Any]:
    return ToolDefinition(
        name=name,
        purpose=f"Required report preflight step: {name}",
        input_type=input_type,
        handler=handler,
        timeout_seconds=2.0,
        critical=True,
    )
