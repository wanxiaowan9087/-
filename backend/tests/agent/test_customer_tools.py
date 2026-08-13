from __future__ import annotations

import pytest

from backend.app.agent.contracts import ToolOutcome
from backend.app.agent.customer_tools import SupportScopeInput, build_customer_tool_registry
from backend.app.agent.tooling import ToolExecutor


@pytest.mark.asyncio
async def test_customer_tool_registry_is_typed_and_grounded() -> None:
    result = await ToolExecutor(build_customer_tool_registry()).execute(
        "inspect_support_scope", {"scope": "uploaded product manual"}
    )

    assert result.outcome is ToolOutcome.SUCCEEDED
    assert "uploaded product manual" in (result.result_preview or "")


def test_customer_tool_input_is_dataclass() -> None:
    assert SupportScopeInput("session").scope == "session"
