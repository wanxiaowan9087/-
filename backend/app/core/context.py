from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from uuid import uuid4

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")
session_id_var: ContextVar[str] = ContextVar("session_id", default="-")
run_id_var: ContextVar[str] = ContextVar("run_id", default="-")


def new_request_id() -> str:
    return uuid4().hex


@dataclass(slots=True)
class ContextTokens:
    request_id: Token[str]
    session_id: Token[str] | None = None
    run_id: Token[str] | None = None

    def reset(self) -> None:
        if self.run_id is not None:
            run_id_var.reset(self.run_id)
        if self.session_id is not None:
            session_id_var.reset(self.session_id)
        request_id_var.reset(self.request_id)


def bind_context(
    request_id: str, session_id: str | None = None, run_id: str | None = None
) -> ContextTokens:
    return ContextTokens(
        request_id=request_id_var.set(request_id),
        session_id=session_id_var.set(session_id) if session_id else None,
        run_id=run_id_var.set(run_id) if run_id else None,
    )
