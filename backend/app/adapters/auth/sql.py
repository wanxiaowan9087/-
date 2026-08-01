from __future__ import annotations

from datetime import datetime
from uuid import UUID

from backend.app.adapters.sql.models import AccessTokenModel, UserModel
from backend.app.application.identity import IdentityStore, IdentityUser
from backend.app.core.errors import conflict
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker


def _user(row: UserModel) -> IdentityUser:
    return IdentityUser(
        id=row.id,
        username=row.username,
        nickname=row.nickname,
        avatar_url=row.avatar_url,
        password_hash=row.password_hash,
        role=row.role,
        created_at=row.created_at,
    )


class SqlIdentityStore(IdentityStore):
    def __init__(self, session_factory: async_sessionmaker[AsyncSession]) -> None:
        self._session_factory = session_factory

    async def create_user(self, user: IdentityUser) -> IdentityUser:
        try:
            async with self._session_factory.begin() as session:
                session.add(
                    UserModel(
                        id=user.id,
                        username=user.username,
                        nickname=user.nickname,
                        avatar_url=user.avatar_url,
                        password_hash=user.password_hash,
                        role=user.role,
                        created_at=user.created_at,
                    )
                )
                await session.flush()
        except IntegrityError as error:
            raise conflict("username is already registered") from error
        return user

    async def find_user_by_username(self, username: str) -> IdentityUser | None:
        async with self._session_factory() as session:
            row = await session.scalar(select(UserModel).where(UserModel.username == username))
        return _user(row) if row else None

    async def find_user_by_id(self, user_id: UUID) -> IdentityUser | None:
        async with self._session_factory() as session:
            row = await session.get(UserModel, user_id)
        return _user(row) if row else None

    async def update_user(
        self, user_id: UUID, *, nickname: str | None, avatar_url: str | None
    ) -> IdentityUser | None:
        async with self._session_factory.begin() as session:
            row = await session.get(UserModel, user_id, with_for_update=True)
            if row is None:
                return None
            if nickname is not None:
                row.nickname = nickname
            if avatar_url is not None:
                row.avatar_url = avatar_url
            await session.flush()
            return _user(row)

    async def update_password(self, user_id: UUID, password_hash: str) -> IdentityUser | None:
        async with self._session_factory.begin() as session:
            row = await session.get(UserModel, user_id, with_for_update=True)
            if row is None:
                return None
            row.password_hash = password_hash
            await session.flush()
            return _user(row)

    async def save_token(self, user_id: UUID, token_digest: str, expires_at: datetime) -> None:
        async with self._session_factory.begin() as session:
            session.add(
                AccessTokenModel(
                    user_id=user_id,
                    token_digest=token_digest,
                    expires_at=expires_at,
                    created_at=datetime.now(expires_at.tzinfo),
                )
            )

    async def authenticate_token(
        self, token_digest: str, now: datetime, refreshed_expires_at: datetime
    ) -> IdentityUser | None:
        async with self._session_factory.begin() as session:
            result = await session.execute(
                select(AccessTokenModel, UserModel)
                .join(UserModel, AccessTokenModel.user_id == UserModel.id)
                .where(
                    AccessTokenModel.token_digest == token_digest,
                    AccessTokenModel.expires_at > now,
                )
                .with_for_update()
            )
            row = result.first()
            if row is None:
                return None
            token, user = row
            token.expires_at = refreshed_expires_at
            return _user(user)
