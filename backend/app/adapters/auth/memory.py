from __future__ import annotations

from datetime import datetime
from uuid import UUID

from backend.app.application.identity import IdentityStore, IdentityUser
from backend.app.core.errors import conflict


class MemoryIdentityStore(IdentityStore):
    def __init__(self) -> None:
        self._users: dict[UUID, IdentityUser] = {}
        self._usernames: dict[str, UUID] = {}
        self._tokens: dict[str, tuple[UUID, datetime]] = {}

    async def create_user(self, user: IdentityUser) -> IdentityUser:
        if user.username in self._usernames:
            raise conflict("username is already registered")
        self._users[user.id] = user
        self._usernames[user.username] = user.id
        return user

    async def find_user_by_username(self, username: str) -> IdentityUser | None:
        user_id = self._usernames.get(username)
        return self._users.get(user_id) if user_id else None

    async def find_user_by_id(self, user_id: UUID) -> IdentityUser | None:
        return self._users.get(user_id)

    async def update_user(
        self, user_id: UUID, *, nickname: str | None, avatar_url: str | None
    ) -> IdentityUser | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        updated = IdentityUser(
            id=user.id,
            username=user.username,
            nickname=nickname if nickname is not None else user.nickname,
            avatar_url=avatar_url if avatar_url is not None else user.avatar_url,
            password_hash=user.password_hash,
            role=user.role,
            created_at=user.created_at,
        )
        self._users[user_id] = updated
        return updated

    async def update_password(self, user_id: UUID, password_hash: str) -> IdentityUser | None:
        user = self._users.get(user_id)
        if user is None:
            return None
        updated = IdentityUser(
            id=user.id,
            username=user.username,
            nickname=user.nickname,
            avatar_url=user.avatar_url,
            password_hash=password_hash,
            role=user.role,
            created_at=user.created_at,
        )
        self._users[user_id] = updated
        return updated

    async def save_token(self, user_id: UUID, token_digest: str, expires_at: datetime) -> None:
        self._tokens[token_digest] = (user_id, expires_at)

    async def authenticate_token(
        self, token_digest: str, now: datetime, refreshed_expires_at: datetime
    ) -> IdentityUser | None:
        token = self._tokens.get(token_digest)
        if token is None or token[1] <= now:
            return None
        self._tokens[token_digest] = (token[0], refreshed_expires_at)
        return self._users.get(token[0])
