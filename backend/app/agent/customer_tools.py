"""Small, grounded tools exposed to the ReAct adapter.

The old project contained random weather/user/location helpers. Those were demo
fixtures and are intentionally not migrated into a customer-facing runtime.
"""

from __future__ import annotations

from contextvars import ContextVar
from dataclasses import dataclass
from typing import Protocol

from .contracts import MemoryContext

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


class UserContextProvider(Protocol):
    async def get_context(self, subject_id: str, session_id: str) -> MemoryContext: ...


@dataclass(frozen=True)
class EmptyUserContextProvider:
    async def get_context(self, subject_id: str, session_id: str) -> MemoryContext:
        return MemoryContext()


_request_context: ContextVar[tuple[str, str] | None] = ContextVar(
    "agent_request_context", default=None
)


def set_request_context(subject_id: str, session_id: str):
    return _request_context.set((subject_id, session_id))


def reset_request_context(token: object) -> None:
    _request_context.reset(token)  # type: ignore[arg-type]


def _context_text(context: MemoryContext) -> str:
    facts = "\n".join(f"- {fact.content}" for fact in context.facts)
    summary = context.summary or "暂无历史会话摘要"
    return f"会话摘要：{summary}\n长期事实：{facts or '暂无已确认的长期事实'}"


async def get_current_user_profile(
    provider: UserContextProvider,
) -> ToolResult:
    identity = _request_context.get()
    if identity is None:
        return ToolResult("当前请求未绑定用户身份，无法读取个人资料。")
    subject_id, session_id = identity
    context = await provider.get_context(subject_id, session_id)
    return ToolResult(
        display_content=_context_text(context),
        internal_content={"subject_id": subject_id, "summary": context.summary},
    )


async def summarize_user_habits(provider: UserContextProvider) -> ToolResult:
    identity = _request_context.get()
    if identity is None:
        return ToolResult("当前请求未绑定用户身份，无法总结使用习惯。")
    subject_id, session_id = identity
    context = await provider.get_context(subject_id, session_id)
    preferences = [fact.content for fact in context.facts if fact.memory_type.value == "preference"]
    profile = "；".join(preferences) if preferences else "暂未形成稳定的偏好记录"
    return ToolResult(
        display_content=f"根据当前账号已确认的记录，使用偏好：{profile}。\n{context.summary or '历史对话不足，暂无更多总结。'}",
        internal_content={"subject_id": subject_id, "preference_count": len(preferences)},
    )


def build_customer_tool_registry(
    provider: UserContextProvider | None = None,
) -> ToolRegistry:
    """Return only deterministic, privacy-safe tools for the live agent."""
    context_provider = provider or EmptyUserContextProvider()
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
            ToolDefinition(
                name="get_current_user_profile",
                purpose="读取当前已认证用户的会话摘要和已确认个人事实，不得读取其他用户数据。",
                input_type=SupportScopeInput,
                handler=lambda _arguments: get_current_user_profile(context_provider),
                timeout_seconds=2.0,
                critical=False,
            ),
            ToolDefinition(
                name="summarize_user_habits",
                purpose="根据当前已认证用户的长期记忆和会话摘要总结使用习惯。",
                input_type=SupportScopeInput,
                handler=lambda _arguments: summarize_user_habits(context_provider),
                timeout_seconds=2.0,
                critical=False,
            ),
        )
    )
