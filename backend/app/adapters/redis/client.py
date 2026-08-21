from __future__ import annotations

import redis.asyncio as redis


class OptionalRedisAdapter:
    """Redis accelerates ephemeral features; failures never replace PostgreSQL truth."""

    def __init__(self, url: str | None, timeout_seconds: float) -> None:
        self._url = url
        self._timeout = timeout_seconds
        self._client: redis.Redis | None = None

    def _get_client(self) -> redis.Redis | None:
        if self._url is None:
            return None
        if self._client is None:
            self._client = redis.from_url(  # type: ignore[no-untyped-call]
                self._url,
                socket_connect_timeout=self._timeout,
                socket_timeout=self._timeout,
                decode_responses=True,
            )
        return self._client

    def client(self) -> redis.Redis | None:
        """Return the lazily-created client for shared ephemeral services."""
        return self._get_client()

    async def health(self) -> str:
        client = self._get_client()
        if client is None:
            return "not_checked"
        try:
            return "available" if await client.ping() else "degraded"
        except (redis.RedisError, TimeoutError):
            return "degraded"

    async def close(self) -> None:
        if self._client is not None:
            await self._client.aclose()
