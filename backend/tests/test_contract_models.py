from __future__ import annotations

import pytest
from pydantic import TypeAdapter, ValidationError

from backend.app.schemas.events import SseEvent
from backend.app.schemas.resources import NewChatRequest


def test_discriminated_chat_rejects_unknown_mode() -> None:
    with pytest.raises(ValidationError):
        NewChatRequest.model_validate(
            {"mode": "retry", "session_id": "00000000-0000-0000-0000-000000000001", "content": "x"}
        )


def test_sse_event_requires_typed_payload() -> None:
    with pytest.raises(ValidationError):
        TypeAdapter(SseEvent).validate_python(
            {
                "event_type": "delta",
                "sequence": 1,
                "request_id": "request_123",
                "session_id": "00000000-0000-0000-0000-000000000001",
                "run_id": "00000000-0000-0000-0000-000000000002",
                "timestamp": "2026-07-26T00:00:00Z",
                "payload": {"index": 0, "content": ""},
            }
        )
