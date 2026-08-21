from __future__ import annotations

from datetime import UTC, datetime, timedelta
from secrets import randbelow

from backend.app.application.sms_verification import SmsPurpose
from backend.app.application.phone_crypto import normalize_mainland_phone


class FakeSmsProvider:
    """Deterministic test provider; codes are exposed only through ``last_code``."""

    def __init__(self, *, ttl_seconds: int = 300, code: str | None = "123456", clock=None) -> None:
        self.ttl_seconds = ttl_seconds
        self.fixed_code = code
        self.clock = clock or (lambda: datetime.now(UTC))
        self.codes: dict[tuple[str, SmsPurpose], tuple[str, datetime]] = {}
        self.last_code: str | None = None
        self.last_request_id: str | None = None

    async def send(self, phone: str, purpose: SmsPurpose) -> str:
        normalized = normalize_mainland_phone(phone)
        code = self.fixed_code or f"{randbelow(1_000_000):06d}"
        self.codes[(normalized, purpose)] = (code, self.clock() + timedelta(seconds=self.ttl_seconds))
        self.last_code = code
        self.last_request_id = f"fake-{len(self.codes)}"
        return self.last_request_id

    async def check(self, phone: str, purpose: SmsPurpose, code: str) -> bool:
        key = (normalize_mainland_phone(phone), purpose)
        stored = self.codes.pop(key, None)
        return bool(stored and stored[1] > self.clock() and stored[0] == code)
