from __future__ import annotations

import hashlib
import math
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from backend.app.core.errors import AppError


@dataclass(frozen=True, slots=True)
class RateLimit:
    requests: int
    window_seconds: int


def rate_limited(retry_after_seconds: int) -> AppError:
    retry_after = max(1, retry_after_seconds)
    return AppError(
        "RATE_LIMITED",
        "request rate limit exceeded",
        429,
        {"retryable": True, "retry_after_seconds": retry_after},
        {"Retry-After": str(retry_after)},
    )


class InMemorySecurityRateLimiter:
    """Deterministic fallback for local development and isolated tests."""

    def __init__(self, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._events: dict[str, deque[datetime]] = {}

    async def retry_after(self, key: str, limit: RateLimit) -> int | None:
        now = self._clock()
        window_start = now - timedelta(seconds=limit.window_seconds)
        events = self._events.setdefault(key, deque())
        while events and events[0] <= window_start:
            events.popleft()
        if len(events) >= limit.requests:
            return max(
                1,
                math.ceil(
                    (events[0] + timedelta(seconds=limit.window_seconds) - now).total_seconds()
                ),
            )
        events.append(now)
        return None


class RedisSecurityRateLimiter:
    """Atomic fixed-window limiter; Redis stores only hashed, expiring identifiers."""

    _SCRIPT = """
    local count = redis.call('INCR', KEYS[1])
    if count == 1 then redis.call('EXPIRE', KEYS[1], ARGV[1]) end
    if count > tonumber(ARGV[2]) then
      local ttl = redis.call('TTL', KEYS[1])
      return ttl > 0 and ttl or 1
    end
    return 0
    """

    def __init__(self, client) -> None:
        self._client = client

    async def retry_after(self, key: str, limit: RateLimit) -> int | None:
        try:
            result = int(
                await self._client.eval(self._SCRIPT, 1, key, limit.window_seconds, limit.requests)
            )
        except Exception as exc:
            raise AppError(
                "RATE_LIMIT_BACKEND_UNAVAILABLE", "security rate limit is unavailable", 503
            ) from exc
        return result if result > 0 else None


class SecurityRateLimiter:
    """Small application seam for rate limiting unauthenticated and chat requests."""

    def __init__(self, adapter: InMemorySecurityRateLimiter | RedisSecurityRateLimiter) -> None:
        self._adapter = adapter

    async def enforce(self, *, scope: str, identifier: str, limit: RateLimit) -> None:
        digest = hashlib.sha256(identifier.encode("utf-8")).hexdigest()
        retry_after = await self._adapter.retry_after(f"agent:rate:{scope}:{digest}", limit)
        if retry_after is not None:
            raise rate_limited(retry_after)
