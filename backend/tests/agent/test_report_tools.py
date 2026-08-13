from __future__ import annotations

from pathlib import Path

import pytest

from backend.app.agent.report_tools import ReportWorkflow


@pytest.mark.asyncio
async def test_report_workflow_enforces_identity_month_context_data_order() -> None:
    workflow = ReportWorkflow(Path("data/external/records.csv"))

    preparation = await workflow.prepare(
        subject_id="1001",
        user_text="请生成我的 2025-01 使用报告",
    )

    assert preparation.month == "2025-01"
    assert [item.tool_name for item in preparation.tool_executions] == [
        "get_user_id",
        "get_report_month",
        "fill_context_for_report",
        "fetch_external_data",
    ]
    assert all(item.outcome.value == "succeeded" for item in preparation.tool_executions)
    assert "用户ID: 1001" in preparation.data


@pytest.mark.asyncio
async def test_report_workflow_does_not_return_another_users_data() -> None:
    workflow = ReportWorkflow(Path("data/external/records.csv"))

    with pytest.raises(RuntimeError, match="fetch_external_data"):
        await workflow.prepare(
            subject_id="missing-user",
            user_text="生成 2025-01 使用报告",
        )


def test_report_intent_and_month_fallback_are_deterministic() -> None:
    workflow = ReportWorkflow(Path("data/external/records.csv"))

    assert workflow.is_report_intent("帮我总结一下本月使用情况")
    assert workflow.parse_month("生成 2025年2月月度报告") == "2025-02"
    assert workflow.parse_month("生成月度报告") == "2025-12"
