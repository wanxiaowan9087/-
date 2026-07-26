from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from time import monotonic
from typing import Callable

from .contracts import (
    ErrorCode,
    RunStatus,
    StepStatus,
    StepType,
    TraceStep,
    utc_now,
)
from .redaction import redact_text


_ALLOWED_TRANSITIONS: dict[RunStatus, frozenset[RunStatus]] = {
    RunStatus.QUEUED: frozenset({RunStatus.RUNNING, RunStatus.CANCELLED}),
    RunStatus.RUNNING: frozenset(
        {
            RunStatus.COMPLETED,
            RunStatus.NEEDS_REVIEW,
            RunStatus.CANCELLED,
            RunStatus.FAILED,
        }
    ),
    RunStatus.NEEDS_REVIEW: frozenset(
        {RunStatus.COMPLETED, RunStatus.REJECTED}
    ),
    RunStatus.COMPLETED: frozenset(),
    RunStatus.CANCELLED: frozenset(),
    RunStatus.FAILED: frozenset(),
    RunStatus.REJECTED: frozenset(),
}


class InvalidRunTransition(ValueError):
    pass


class RunStateMachine:
    def __init__(self) -> None:
        self._status = RunStatus.QUEUED

    @property
    def status(self) -> RunStatus:
        return self._status

    def transition(self, target: RunStatus) -> RunStatus:
        if target not in _ALLOWED_TRANSITIONS[self._status]:
            raise InvalidRunTransition(
                f"cannot transition run from {self._status} to {target}"
            )
        self._status = target
        return target


class TraceRecorder:
    """Records only redacted, caller-safe summaries at the runtime seam."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] = utc_now,
        timer: Callable[[], float] = monotonic,
    ) -> None:
        self._clock = clock
        self._timer = timer
        self._steps: list[TraceStep] = []
        self._timers: dict[int, float] = {}

    def start(self, step_type: StepType, summary: str) -> int:
        sequence = len(self._steps) + 1
        self._steps.append(
            TraceStep(
                sequence=sequence,
                step_type=step_type,
                status=StepStatus.STARTED,
                started_at=self._clock(),
                ended_at=None,
                duration_ms=None,
                summary=redact_text(summary)[:1000],
            )
        )
        self._timers[sequence] = self._timer()
        return sequence

    def finish(
        self,
        sequence: int,
        status: StepStatus,
        summary: str,
        error_code: ErrorCode | None = None,
    ) -> TraceStep:
        if sequence < 1 or sequence > len(self._steps):
            raise IndexError("unknown trace step")
        current = self._steps[sequence - 1]
        if current.status is not StepStatus.STARTED:
            raise ValueError("trace step is already terminal")
        ended_at = self._clock()
        duration_ms = max(
            0, round((self._timer() - self._timers.pop(sequence)) * 1000)
        )
        finished = replace(
            current,
            status=status,
            ended_at=ended_at,
            duration_ms=duration_ms,
            summary=redact_text(summary)[:1000],
            error_code=error_code,
        )
        self._steps[sequence - 1] = finished
        return finished

    def cancel_open_steps(self) -> None:
        for step in tuple(self._steps):
            if step.status is StepStatus.STARTED:
                self.finish(
                    step.sequence,
                    StepStatus.CANCELLED,
                    "step cancelled",
                    ErrorCode.CANCELLED,
                )

    def snapshot(self) -> tuple[TraceStep, ...]:
        return tuple(self._steps)
