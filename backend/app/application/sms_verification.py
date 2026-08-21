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


@dataclass(frozen=True, slots=True)
class SmsVerificationAttemptLimits:
    max_attempts: int = 5
    window_seconds: int = 900
    lock_seconds: int = 900


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


class InMemoryVerificationAttemptLimiter:
    """Development/test adapter for failed verification attempt tracking."""

    def __init__(
        self,
        limits: SmsVerificationAttemptLimits = SmsVerificationAttemptLimits(),
        clock=None,
    ) -> None:
        self.limits = limits
        self.clock = clock or (lambda: datetime.now(UTC))
        self._failures: dict[str, tuple[int, datetime]] = {}
        self._locks: dict[str, datetime] = {}
        self._in_flight: set[str] = set()

    async def acquire(self, key: str) -> bool:
        now = self.clock()
        expires_at = self._locks.get(key)
        if expires_at is not None:
            if expires_at > now:
                return False
            self._locks.pop(key, None)
        if key in self._in_flight:
            return False
        self._in_flight.add(key)
        return True

    async def record_failure(self, key: str) -> None:
        self._in_flight.discard(key)
        now = self.clock()
        failures, expires_at = self._failures.get(key, (0, now))
        if expires_at <= now:
            failures = 0
        failures += 1
        if failures >= self.limits.max_attempts:
            self._locks[key] = now + timedelta(seconds=self.limits.lock_seconds)
            self._failures.pop(key, None)
            return
        self._failures[key] = (
            failures,
            now + timedelta(seconds=self.limits.window_seconds),
        )

    async def clear(self, key: str) -> None:
        self._in_flight.discard(key)
        self._failures.pop(key, None)
        self._locks.pop(key, None)

    async def release(self, key: str) -> None:
        self._in_flight.discard(key)


class RedisVerificationAttemptLimiter:
    """Distributed failed-attempt limiter. Redis expiry keeps this state disposable."""

    _RECORD_FAILURE_SCRIPT = """
    redis.call('DEL', KEYS[3])
    local failures = redis.call('INCR', KEYS[1])
    if failures == 1 then redis.call('EXPIRE', KEYS[1], ARGV[2]) end
    if failures >= tonumber(ARGV[1]) then
      redis.call('SET', KEYS[2], '1', 'EX', ARGV[3])
      redis.call('DEL', KEYS[1])
    end
    return failures
    """
    _ACQUIRE_SCRIPT = """
    if redis.call('EXISTS', KEYS[1]) == 1 then return 0 end
    return redis.call('SET', KEYS[2], '1', 'NX', 'EX', ARGV[1]) and 1 or 0
    """

    def __init__(
        self,
        client,
        limits: SmsVerificationAttemptLimits = SmsVerificationAttemptLimits(),
    ) -> None:
        self.client = client
        self.limits = limits

    def _keys(self, key: str) -> tuple[str, str, str]:
        digest = hashlib.sha256(key.encode("utf-8")).hexdigest()
        prefix = f"agent:sms-verify:{digest}"
        return f"{prefix}:failures", f"{prefix}:lock", f"{prefix}:in-flight"

    async def acquire(self, key: str) -> bool:
        _, lock_key, in_flight_key = self._keys(key)
        try:
            result = await self.client.eval(
                self._ACQUIRE_SCRIPT, 2, lock_key, in_flight_key, 30
            )
            return bool(int(result))
        except Exception as exc:
            raise AppError("RATE_LIMIT_BACKEND_UNAVAILABLE", "短信服务暂时不可用", 503) from exc

    async def record_failure(self, key: str) -> None:
        failure_key, lock_key, in_flight_key = self._keys(key)
        try:
            await self.client.eval(
                self._RECORD_FAILURE_SCRIPT,
                3,
                failure_key,
                lock_key,
                in_flight_key,
                self.limits.max_attempts,
                self.limits.window_seconds,
                self.limits.lock_seconds,
            )
        except Exception as exc:
            raise AppError("RATE_LIMIT_BACKEND_UNAVAILABLE", "短信服务暂时不可用", 503) from exc

    async def clear(self, key: str) -> None:
        failure_key, lock_key, in_flight_key = self._keys(key)
        try:
            await self.client.delete(failure_key, lock_key, in_flight_key)
        except Exception as exc:
            raise AppError("RATE_LIMIT_BACKEND_UNAVAILABLE", "短信服务暂时不可用", 503) from exc

    async def release(self, key: str) -> None:
        _, _, in_flight_key = self._keys(key)
        try:
            await self.client.delete(in_flight_key)
        except Exception as exc:
            raise AppError("RATE_LIMIT_BACKEND_UNAVAILABLE", "短信服务暂时不可用", 503) from exc


class SmsVerificationService:
    def __init__(self, provider: SmsProvider, limiter=None, attempt_limiter=None) -> None:
        self.provider = provider
        self.limiter = limiter or InMemoryRateLimiter()
        self.attempt_limiter = attempt_limiter or InMemoryVerificationAttemptLimiter()

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
            raise AppError(
                "SMS_PROVIDER_UNAVAILABLE", "SMS service is temporarily unavailable", 503
            ) from exc
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
        key = f"phone:{normalized}:purpose:{purpose_enum}"
        if not await self.attempt_limiter.acquire(key):
            return False
        try:
            valid = bool(await self.provider.check(normalized, purpose_enum, code))
        except (TimeoutError, OSError) as exc:
            await self.attempt_limiter.release(key)
            raise AppError(
                "SMS_PROVIDER_UNAVAILABLE", "SMS service is temporarily unavailable", 503
            ) from exc
        except Exception as exc:
            await self.attempt_limiter.release(key)
            raise AppError(
                "SMS_PROVIDER_ERROR", "SMS verification could not be checked", 503
            ) from exc
        if valid:
            await self.attempt_limiter.clear(key)
            return True
        await self.attempt_limiter.record_failure(key)
        return False
