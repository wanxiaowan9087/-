from __future__ import annotations

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from backend.app.core.config import Settings


def create_engine(settings: Settings) -> AsyncEngine:
    connect_args: dict[str, object] = {}
    if settings.database_url.startswith("postgresql+asyncpg://"):
        connect_args["command_timeout"] = settings.database_command_timeout_seconds
    return create_async_engine(
        settings.database_url,
        pool_pre_ping=True,
        pool_timeout=settings.database_pool_timeout_seconds,
        connect_args=connect_args,
    )


def create_session_factory(engine: AsyncEngine) -> async_sessionmaker[AsyncSession]:
    return async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
