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

    async def save_token(self, user_id: UUID, token_digest: str, expires_at: datetime) -> None:
        self._tokens[token_digest] = (user_id, expires_at)

    async def find_user_by_token_digest(
        self, token_digest: str, now: datetime
    ) -> IdentityUser | None:
        token = self._tokens.get(token_digest)
        if token is None or token[1] <= now:
            return None
        return self._users.get(token[0])
