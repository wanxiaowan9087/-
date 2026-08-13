from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent.report_tools import ReportWorkflow


@pytest.mark.asyncio
async def test_report_requires_authorized_external_identity_mapping() -> None:
    async def missing_mapping(_: str) -> str | None:
        return None

    workflow = ReportWorkflow(
        Path("data/external/records.csv"), external_user_id_resolver=missing_mapping
    )

    with pytest.raises(PermissionError, match="mapping"):
        await workflow.prepare(subject_id="platform-user-uuid", user_text="2025-01 月度报告")


@pytest.mark.asyncio
async def test_report_uses_mapped_external_identity() -> None:
    async def mapped_identity(_: str) -> str | None:
        return "1001"

    workflow = ReportWorkflow(
        Path("data/external/records.csv"), external_user_id_resolver=mapped_identity
    )

    report = await workflow.prepare(subject_id="platform-user-uuid", user_text="2025-01 月度报告")

    assert "1001" in report.context
