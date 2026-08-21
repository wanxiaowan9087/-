from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Protocol

from backend.app.application.phone_crypto import normalize_mainland_phone
from backend.app.core.errors import AppError


class SmsPurpose(StrEnum):
    REGISTER = "register"
    PASSWORD_RESET = "password_reset"
    ADMIN_PHONE_BINDING = "admin_phone_binding"


class SmsProvider(Protocol):
    async def send(self, phone: str, purpose: SmsPurpose) -> str: ...
    async def check(self, phone: str, purpose: SmsPurpose, code: str) -> bool: ...


@dataclass(frozen=True, slots=True)
class SmsLimits:
    cooldown_seconds: int = 60
    hourly: int = 5
    daily: int = 10


class InMemoryRateLimiter:
    """Deterministic fallback for development; production should inject Redis."""

    def __init__(self, limits: SmsLimits = SmsLimits(), clock=None) -> None:
        self.limits = limits
        self.clock = clock or (lambda: datetime.now(UTC))
        self._sent: dict[str, list[datetime]] = {}

    async def allow(self, key: str) -> bool:
        now = self.clock()
        values = [item for item in self._sent.get(key, []) if item > now - timedelta(days=1)]
        if values and values[-1] > now - timedelta(seconds=self.limits.cooldown_seconds):
            self._sent[key] = values
            return False
        if sum(item > now - timedelta(hours=1) for item in values) >= self.limits.hourly:
            self._sent[key] = values
            return False
        if len(values) >= self.limits.daily:
            self._sent[key] = values
            return False
        values.append(now)
        self._sent[key] = values
        return True


class SmsVerificationService:
    def __init__(self, provider: SmsProvider, limiter: InMemoryRateLimiter | None = None) -> None:
        self.provider = provider
        self.limiter = limiter or InMemoryRateLimiter()

    async def send(self, *, phone: str, purpose: SmsPurpose | str) -> str:
        normalized = normalize_mainland_phone(phone)
        try:
            purpose_enum = SmsPurpose(purpose)
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", "unsupported SMS purpose", 422) from exc
        if not await self.limiter.allow(f"phone:{normalized}"):
            raise AppError("RATE_LIMITED", "too many verification code requests", 429)
        try:
            return await self.provider.send(normalized, purpose_enum)
        except AppError:
            raise
        except (TimeoutError, OSError) as exc:
            raise AppError("SMS_PROVIDER_UNAVAILABLE", "SMS service is temporarily unavailable", 503) from exc
        except Exception as exc:
            raise AppError("SMS_PROVIDER_ERROR", "SMS verification could not be sent", 503) from exc

    async def check(self, *, phone: str, purpose: SmsPurpose | str, code: str) -> bool:
        normalized = normalize_mainland_phone(phone)
        try:
            purpose_enum = SmsPurpose(purpose)
        except ValueError as exc:
            raise AppError("VALIDATION_ERROR", "unsupported SMS purpose", 422) from exc
        if len(code) != 6 or not code.isdigit():
            return False
        try:
            return bool(await self.provider.check(normalized, purpose_enum, code))
        except (TimeoutError, OSError) as exc:
            raise AppError("SMS_PROVIDER_UNAVAILABLE", "SMS service is temporarily unavailable", 503) from exc
        except Exception as exc:
            raise AppError("SMS_PROVIDER_ERROR", "SMS verification could not be checked", 503) from exc
