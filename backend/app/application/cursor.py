from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import datetime
from typing import Any
from uuid import UUID

from backend.app.core.errors import AppError


class CursorCodec:
    def __init__(self, secret: str) -> None:
        self._secret = secret.encode()

    def encode(
        self, kind: str, filters: dict[str, Any], timestamp: datetime | None, item_id: UUID
    ) -> str:
        payload = {
            "v": 1,
            "kind": kind,
            "filters": filters,
            "timestamp": timestamp.isoformat() if timestamp else None,
            "id": str(item_id),
        }
        raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
        signature = hmac.new(self._secret, raw, hashlib.sha256).digest()
        return base64.urlsafe_b64encode(raw + b"." + signature).decode().rstrip("=")

    def decode(
        self, cursor: str | None, *, kind: str, filters: dict[str, Any]
    ) -> tuple[datetime | None, UUID] | None:
        if cursor is None:
            return None
        try:
            padded = cursor + "=" * (-len(cursor) % 4)
            raw_signed = base64.urlsafe_b64decode(padded)
            raw, signature = raw_signed.rsplit(b".", 1)
            expected = hmac.new(self._secret, raw, hashlib.sha256).digest()
            if not hmac.compare_digest(signature, expected):
                raise ValueError
            payload = json.loads(raw)
            if payload != {
                "v": 1,
                "kind": kind,
                "filters": filters,
                "timestamp": payload.get("timestamp"),
                "id": payload.get("id"),
            }:
                raise ValueError
            timestamp = (
                datetime.fromisoformat(payload["timestamp"]) if payload["timestamp"] else None
            )
            return timestamp, UUID(payload["id"])
        except (ValueError, KeyError, TypeError, json.JSONDecodeError) as exc:
            raise AppError("BAD_REQUEST", "invalid cursor", 400) from exc
