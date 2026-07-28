from __future__ import annotations

from backend.app.application.ports import UnavailableRunExecutor
from backend.app.bootstrap import build_run_executor
from backend.app.core.config import Settings


def test_disabled_live_runtime_keeps_safe_unavailable_executor() -> None:
    executor = build_run_executor(Settings(environment="test"))

    assert isinstance(executor, UnavailableRunExecutor)
