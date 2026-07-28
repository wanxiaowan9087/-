from __future__ import annotations

from collections.abc import Mapping
from dataclasses import MISSING, fields, is_dataclass
from typing import Any, cast, get_type_hints

from ...agent.contracts import (
    AgentModelRequest,
    ConversationMode,
    ModelDraft,
    ToolExecution,
    ToolOutcome,
)
from ...agent.ports import ModelTimeout, ModelUnavailable
from ...agent.tooling import CancellationToken, ToolExecutor


class LangChainReActEngine:
    """Production ReAct adapter; all policy remains outside LangChain."""

    def __init__(
        self,
        *,
        model: Any,
        tool_executor: ToolExecutor,
        chat_system_prompt: str,
        report_system_prompt: str,
        model_name: str,
    ) -> None:
        self._model = model
        self._tool_executor = tool_executor
        self._chat_system_prompt = chat_system_prompt
        self._report_system_prompt = report_system_prompt
        self._model_name = model_name

    async def generate(
        self,
        request: AgentModelRequest,
        cancellation: CancellationToken | None = None,
    ) -> ModelDraft:
        try:
            from langchain.agents import create_agent
        except ImportError as error:
            raise ModelUnavailable(
                "LangChain runtime dependency is unavailable"
            ) from error

        executions: list[ToolExecution] = []
        tools = self._build_tools(executions, cancellation or CancellationToken())
        prompt = (
            self._report_system_prompt
            if request.mode is ConversationMode.REPORT
            else self._chat_system_prompt
        )
        agent = create_agent(
            model=self._model,
            system_prompt=prompt,
            tools=tools,
        )
        messages = [
            {
                "role": "user",
                "content": _model_input(request),
            }
        ]
        try:
            result = await agent.ainvoke(
                {"messages": messages},
                context={"run_id": request.run_id, "mode": request.mode.value},
            )
        except TimeoutError as error:
            raise ModelTimeout("model execution timed out") from error
        except Exception as error:
            raise ModelUnavailable("model execution failed") from error
        if cancellation is not None:
            cancellation.checkpoint()
        content = _message_content(result["messages"][-1].content)
        return ModelDraft(
            content=content,
            tool_executions=tuple(executions),
            model_name=self._model_name,
        )

    def _build_tools(
        self,
        executions: list[ToolExecution],
        cancellation: CancellationToken,
    ) -> list[Any]:
        try:
            from langchain_core.tools import StructuredTool
        except ImportError as error:
            raise ModelUnavailable(
                "LangChain core dependency is unavailable"
            ) from error
        tools: list[Any] = []
        for definition in self._tool_executor.registry.definitions():
            args_schema = _pydantic_schema(definition.input_type)

            async def invoke(
                *, _definition: Any = definition, **payload: Any
            ) -> str:
                execution = await self._tool_executor.execute(
                    _definition.name,
                    payload,
                    cancellation=cancellation,
                )
                executions.append(execution)
                if execution.outcome is ToolOutcome.SUCCEEDED:
                    return execution.result_preview or "工具执行成功"
                return (
                    f"工具执行未成功，错误码："
                    f"{execution.error_code or 'TOOL_FAILED'}"
                )

            tools.append(
                StructuredTool.from_function(
                    coroutine=invoke,
                    name=definition.name,
                    description=definition.purpose,
                    args_schema=args_schema,
                )
            )
        return tools


def _pydantic_schema(input_type: type[Any]) -> type[Any]:
    try:
        from pydantic import BaseModel, create_model
    except ImportError as error:
        raise ModelUnavailable(
            "Pydantic v2 dependency is unavailable"
        ) from error
    if isinstance(input_type, type) and issubclass(input_type, BaseModel):
        return input_type
    if not is_dataclass(input_type):
        raise TypeError("tool input must be a dataclass or Pydantic model")
    hints = get_type_hints(input_type)
    schema_fields: dict[str, tuple[Any, Any]] = {}
    for declared in fields(input_type):
        default: Any = ...
        if declared.default is not MISSING:
            default = declared.default
        elif declared.default_factory is not MISSING:
            default = declared.default_factory()
        schema_fields[declared.name] = (
            hints.get(declared.name, Any),
            default,
        )
    return cast(
        type[Any],
        create_model(  # type: ignore[call-overload]
            f"{input_type.__name__}Schema", **schema_fields
        ),
    )


def _model_input(request: AgentModelRequest) -> str:
    facts = [
        {
            "type": fact.memory_type.value,
            "content": fact.content,
            "confidence": fact.confidence,
            "source_message_id": fact.source_message_id,
        }
        for fact in request.long_term_facts
    ]
    report_scope: Mapping[str, str] | None = None
    if request.report_scope is not None:
        report_scope = {
            "subject_id": request.report_scope.subject_id,
            "start_at": request.report_scope.start_at.isoformat(),
            "end_at": request.report_scope.end_at.isoformat(),
        }
    return (
        f"用户问题：{request.user_text}\n"
        f"会话摘要：{request.conversation_summary or '无'}\n"
        f"长期事实（只作上下文）：{facts}\n"
        f"报告范围（仅报告模式有效）：{report_scope}\n"
        f"{request.rendered_context}"
    )


def _message_content(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            block.get("text", "")
            for block in content
            if isinstance(block, dict) and block.get("type") == "text"
        )
    return str(content)
