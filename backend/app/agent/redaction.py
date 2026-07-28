from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

_SENSITIVE_KEY = re.compile(
    r"(authorization|api[-_]?key|token|secret|password|credential|cookie|"
    r"email|phone|id_card)",
    re.IGNORECASE,
)
_TEXT_PATTERNS = (
    re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}"),
    re.compile(r"(?i)\b(?:sk|ak)-[A-Za-z0-9_-]{8,}"),
    re.compile(r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"),
    re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"),
)


def redact_text(value: str) -> str:
    redacted = value
    for pattern in _TEXT_PATTERNS:
        redacted = pattern.sub("[REDACTED]", redacted)
    return redacted


def redact_value(value: Any) -> Any:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        return {
            str(key): (
                "[REDACTED]"
                if _SENSITIVE_KEY.search(str(key))
                else redact_value(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list | tuple):
        return [redact_value(item) for item in value]
    if isinstance(value, int | float | bool) or value is None:
        return value
    return "[REDACTED_OBJECT]"
