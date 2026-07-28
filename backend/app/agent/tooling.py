from __future__ import annotations

import asyncio
import inspect
import logging
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import MISSING, dataclass, fields, is_dataclass
from time import monotonic
from typing import (
    Any,
    Generic,
    Protocol,
    TypeVar,
    cast,
    get_args,
    get_origin,
    get_type_hints,
)
from uuid import uuid4

from .contracts import ErrorCode, ToolExecution, ToolOutcome, ToolResult
from .redaction import redact_text, redact_value

logger = logging.getLogger(__name__)

InputT = TypeVar("InputT")
HandlerInputT = TypeVar("HandlerInputT", contravariant=True)


class ToolHandler(Protocol[HandlerInputT]):
    def __call__(
        self, arguments: HandlerInputT
    ) -> ToolResult | Awaitable[ToolResult]: ...


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 2
    base_delay_seconds: float = 0.05
    max_delay_seconds: float = 0.5
    jitter_seconds: float = 0.0

    def __post_init__(self) -> None:
        if self.max_attempts < 1:
            raise ValueError("max_attempts must be at least one")
        if min(
            self.base_delay_seconds,
            self.max_delay_seconds,
            self.jitter_seconds,
        ) < 0:
            raise ValueError("retry delays must not be negative")

    def delay(self, failed_attempt: int, jitter: Callable[[], float]) -> float:
        exponential = self.base_delay_seconds * (2 ** (failed_attempt - 1))
        return float(min(self.max_delay_seconds, exponential) + (
            self.jitter_seconds * max(0.0, min(1.0, jitter()))
        ))


@dataclass(frozen=True)
class ToolDefinition(Generic[InputT]):
    name: str
    purpose: str
    input_type: type[InputT]
    handler: ToolHandler[InputT]
    timeout_seconds: float = 5.0
    retry_policy: RetryPolicy = RetryPolicy()
    critical: bool = False
    display_argument_fields: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.purpose.strip():
            raise ValueError("tool name and purpose are required")
        if self.timeout_seconds <= 0:
            raise ValueError("tool timeout must be positive")
        if not is_dataclass(self.input_type) and not hasattr(
            self.input_type, "model_validate"
        ):
            raise TypeError(
                "tool input_type must be a dataclass or Pydantic v2 model"
            )


class ToolAdapterError(RuntimeError):
    def __init__(
        self,
        safe_message: str,
        *,
        retryable: bool = False,
        code: ErrorCode = ErrorCode.TOOL_FAILED,
    ) -> None:
        super().__init__(safe_message)
        self.safe_message = safe_message
        self.retryable = retryable
        self.code = code


class CancellationToken:
    def __init__(self) -> None:
        self._event = asyncio.Event()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def cancel(self) -> None:
        self._event.set()

    async def wait_cancelled(self) -> None:
        await self._event.wait()

    def checkpoint(self) -> None:
        if self.cancelled:
            raise asyncio.CancelledError


class ToolRegistry:
    def __init__(self, definitions: tuple[ToolDefinition[Any], ...] = ()) -> None:
        self._definitions: dict[str, ToolDefinition[Any]] = {}
        for definition in definitions:
            self.register(definition)

    def register(self, definition: ToolDefinition[Any]) -> None:
        if definition.name in self._definitions:
            raise ValueError(f"tool already registered: {definition.name}")
        self._definitions[definition.name] = definition

    def get(self, name: str) -> ToolDefinition[Any] | None:
        return self._definitions.get(name)

    def definitions(self) -> tuple[ToolDefinition[Any], ...]:
        return tuple(self._definitions.values())


class ToolExecutor:
    """Typed, bounded tool execution with cancellation and safe previews."""

    def __init__(
        self,
        registry: ToolRegistry,
        *,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        jitter: Callable[[], float] = lambda: 0.0,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self.registry = registry
        self._sleep = sleep
        self._jitter = jitter
        self._timer = timer

    async def execute(
        self,
        name: str,
        payload: Mapping[str, Any],
        *,
        cancellation: CancellationToken | None = None,
        tool_call_id: str | None = None,
    ) -> ToolExecution:
        started = self._timer()
        definition = self.registry.get(name)
        call_id = tool_call_id or str(uuid4())
        if definition is None:
            return self._failure(
                call_id,
                name,
                False,
                ToolOutcome.FAILED,
                0,
                started,
                None,
                ErrorCode.TOOL_FAILED,
            )

        arguments_preview = _arguments_preview(definition, payload)
        try:
            arguments = _decode_input(definition.input_type, payload)
        except (TypeError, ValueError):
            return self._failure(
                call_id,
                name,
                definition.critical,
                ToolOutcome.FAILED,
                0,
                started,
                arguments_preview,
                ErrorCode.VALIDATION_ERROR,
            )

        token = cancellation or CancellationToken()
        for attempt in range(1, definition.retry_policy.max_attempts + 1):
            if token.cancelled:
                return self._failure(
                    call_id,
                    name,
                    definition.critical,
                    ToolOutcome.CANCELLED,
                    attempt - 1,
                    started,
                    arguments_preview,
                    ErrorCode.CANCELLED,
                )
            try:
                result = await self._invoke_once(definition, arguments, token)
                return ToolExecution(
                    tool_call_id=call_id,
                    tool_name=name,
                    critical=definition.critical,
                    outcome=ToolOutcome.SUCCEEDED,
                    attempts=attempt,
                    duration_ms=_duration_ms(started, self._timer),
                    result_preview=redact_text(result.display_content)[:1000],
                    arguments_preview=arguments_preview,
                    error_code=None,
                    internal_content=result.internal_content,
                )
            except asyncio.CancelledError:
                return self._failure(
                    call_id,
                    name,
                    definition.critical,
                    ToolOutcome.CANCELLED,
                    attempt,
                    started,
                    arguments_preview,
                    ErrorCode.CANCELLED,
                )
            except TimeoutError:
                if attempt >= definition.retry_policy.max_attempts:
                    return self._failure(
                        call_id,
                        name,
                        definition.critical,
                        ToolOutcome.TIMEOUT,
                        attempt,
                        started,
                        arguments_preview,
                        ErrorCode.TOOL_TIMEOUT,
                    )
                await self._retry_delay(definition.retry_policy, attempt, token)
            except ToolAdapterError as error:
                if (
                    not error.retryable
                    or attempt >= definition.retry_policy.max_attempts
                ):
                    return self._failure(
                        call_id,
                        name,
                        definition.critical,
                        ToolOutcome.FAILED,
                        attempt,
                        started,
                        arguments_preview,
                        error.code,
                    )
                await self._retry_delay(definition.retry_policy, attempt, token)
            except Exception:
                logger.exception(
                    "tool adapter failed",
                    extra={"tool_name": name, "error_code": "TOOL_FAILED"},
                )
                return self._failure(
                    call_id,
                    name,
                    definition.critical,
                    ToolOutcome.FAILED,
                    attempt,
                    started,
                    arguments_preview,
                    ErrorCode.TOOL_FAILED,
                )

        raise AssertionError("bounded tool loop exhausted unexpectedly")

    async def _invoke_once(
        self,
        definition: ToolDefinition[InputT],
        arguments: InputT,
        token: CancellationToken,
    ) -> ToolResult:
        async def call_handler() -> ToolResult:
            if inspect.iscoroutinefunction(definition.handler):
                result = await definition.handler(arguments)
            else:
                result = await asyncio.to_thread(definition.handler, arguments)
                if inspect.isawaitable(result):
                    result = await result
            if not isinstance(result, ToolResult):
                raise TypeError("tool adapters must return ToolResult")
            return result

        handler_task = asyncio.create_task(call_handler())
        cancellation_task = asyncio.create_task(token.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {handler_task, cancellation_task},
                timeout=definition.timeout_seconds,
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation_task in done:
                handler_task.cancel()
                raise asyncio.CancelledError
            if handler_task not in done:
                handler_task.cancel()
                raise TimeoutError
            return handler_task.result()
        finally:
            cancellation_task.cancel()

    async def _retry_delay(
        self,
        retry_policy: RetryPolicy,
        attempt: int,
        token: CancellationToken,
    ) -> None:
        delay_task: asyncio.Future[None] = asyncio.ensure_future(
            self._sleep(retry_policy.delay(attempt, self._jitter))
        )
        cancellation_task = asyncio.create_task(token.wait_cancelled())
        try:
            done, _ = await asyncio.wait(
                {delay_task, cancellation_task},
                return_when=asyncio.FIRST_COMPLETED,
            )
            if cancellation_task in done:
                delay_task.cancel()
                raise asyncio.CancelledError
        finally:
            cancellation_task.cancel()

    def _failure(
        self,
        call_id: str,
        name: str,
        critical: bool,
        outcome: ToolOutcome,
        attempts: int,
        started: float,
        arguments_preview: Mapping[str, Any] | None,
        code: ErrorCode,
    ) -> ToolExecution:
        return ToolExecution(
            tool_call_id=call_id,
            tool_name=name,
            critical=critical,
            outcome=outcome,
            attempts=attempts,
            duration_ms=_duration_ms(started, self._timer),
            result_preview=None,
            arguments_preview=arguments_preview,
            error_code=code,
            internal_content=None,
        )


def _duration_ms(started: float, timer: Callable[[], float]) -> int:
    return max(0, round((timer() - started) * 1000))


def _arguments_preview(
    definition: ToolDefinition[Any], payload: Mapping[str, Any]
) -> Mapping[str, Any] | None:
    if not definition.display_argument_fields:
        return None
    return {
        key: redact_value(payload[key])
        for key in sorted(definition.display_argument_fields)
        if key in payload
    }


def _decode_input(input_type: type[InputT], payload: Mapping[str, Any]) -> InputT:
    model_validate = getattr(input_type, "model_validate", None)
    if callable(model_validate):
        return cast(InputT, model_validate(dict(payload)))
    if not is_dataclass(input_type):
        raise TypeError("unsupported tool input type")

    declared = {field.name: field for field in fields(input_type)}
    extras = set(payload) - set(declared)
    if extras:
        raise ValueError(f"unexpected tool arguments: {sorted(extras)}")
    hints = get_type_hints(input_type)
    values: dict[str, Any] = {}
    for name, declared_field in declared.items():
        if name not in payload:
            if (
                declared_field.default is MISSING
                and declared_field.default_factory is MISSING
            ):
                raise ValueError(f"missing tool argument: {name}")
            continue
        value = payload[name]
        expected = hints.get(name, Any)
        if not _matches_type(value, expected):
            raise TypeError(f"invalid tool argument type: {name}")
        values[name] = value
    return input_type(**values)


def _matches_type(value: Any, expected: Any) -> bool:
    if expected is Any:
        return True
    origin = get_origin(expected)
    if origin is None:
        if expected is float:
            return isinstance(value, float | int) and not isinstance(value, bool)
        if expected is int:
            return isinstance(value, int) and not isinstance(value, bool)
        return isinstance(value, expected)
    if origin in (list, tuple):
        args = get_args(expected)
        item_type = args[0] if args else Any
        return isinstance(value, origin) and all(
            _matches_type(item, item_type) for item in value
        )
    if origin is dict:
        return isinstance(value, dict)
    if origin is type(None):
        return value is None
    args = get_args(expected)
    return any(_matches_type(value, item_type) for item_type in args)
