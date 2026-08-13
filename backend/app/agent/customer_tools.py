"""Small, grounded tools exposed to the ReAct adapter.

The old project contained random weather/user/location helpers. Those were demo
fixtures and are intentionally not migrated into a customer-facing runtime.
"""

from __future__ import annotations

from dataclasses import dataclass

from .contracts import ToolResult
from .tooling import ToolDefinition, ToolRegistry


@dataclass(frozen=True)
class SupportScopeInput:
    scope: str = "current conversation"


def inspect_support_scope(arguments: SupportScopeInput) -> ToolResult:
    scope = arguments.scope.strip() or "current conversation"
    return ToolResult(
        display_content=(
            f"Support context is limited to {scope}; "
            "use retrieved citations for product claims."
        ),
        internal_content={"scope": scope},
    )


def build_customer_tool_registry() -> ToolRegistry:
    """Return only deterministic, privacy-safe tools for the live agent."""
    return ToolRegistry(
        (
            ToolDefinition(
                name="inspect_support_scope",
                purpose=(
                    "Confirm the current support context before answering. "
                    "This tool does not access external systems or private data."
                ),
                input_type=SupportScopeInput,
                handler=inspect_support_scope,
                timeout_seconds=1.0,
                critical=False,
                display_argument_fields=frozenset({"scope"}),
            ),
        )
    )
