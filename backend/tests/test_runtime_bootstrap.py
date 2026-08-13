from __future__ import annotations

from backend.app.adapters.llm.deterministic_executor import DeterministicRunExecutor
from backend.app.application.ports import UnavailableRunExecutor
from backend.app.bootstrap import build_run_executor
from backend.app.core.config import Settings


def test_disabled_live_runtime_keeps_safe_unavailable_executor() -> None:
    executor = build_run_executor(Settings(environment="test"))

    assert isinstance(executor, UnavailableRunExecutor)


def test_test_environment_can_use_deterministic_executor() -> None:
    executor = build_run_executor(Settings(environment="test", test_executor_enabled=True))

    assert isinstance(executor, DeterministicRunExecutor)
