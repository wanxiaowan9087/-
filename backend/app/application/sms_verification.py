from __future__ import annotations

import hashlib
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


class RedisRateLimiter:
    """Atomic distributed SMS limiter used when Redis is configured."""

    _SCRIPT = """
    local cooldown = redis.call('SET', KEYS[1], '1', 'NX', 'EX', ARGV[1])
    if not cooldown then return 0 end
    local hourly = redis.call('INCR', KEYS[2])
    if hourly == 1 then redis.call('EXPIRE', KEYS[2], 3600) end
    local daily = redis.call('INCR', KEYS[3])
    if daily == 1 then redis.call('EXPIRE', KEYS[3], 86400) end
    if hourly > tonumber(ARGV[2]) or daily > tonumber(ARGV[3]) then
      redis.call('DEL', KEYS[1])
      redis.call('DECR', KEYS[2])
      redis.call('DECR', KEYS[3])
      return 0
    end
    return 1
    """

    def __init__(self, client, limits: SmsLimits = SmsLimits()) -> None:
        self.client = client
        self.limits = limits

    async def allow(self, key: str) -> bool:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        prefix = f"agent:sms:{digest}"
        try:
            result = await self.client.eval(
                self._SCRIPT,
                3,
                f"{prefix}:cooldown",
                f"{prefix}:hour",
                f"{prefix}:day",
                self.limits.cooldown_seconds,
                self.limits.hourly,
                self.limits.daily,
            )
        except Exception as exc:
            raise AppError("RATE_LIMIT_BACKEND_UNAVAILABLE", "短信服务暂时不可用", 503) from exc
        return bool(int(result))


class SmsVerificationService:
    def __init__(self, provider: SmsProvider, limiter=None) -> None:
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
