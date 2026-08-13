from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol
from uuid import UUID, uuid4

from backend.app.core.errors import AppError, conflict, not_found

DEFAULT_AVATAR_URL = (
    "https://images.unsplash.com/photo-1534528741775-53994a69daeb"
    "?auto=format&fit=crop&w=160&q=80"
)


@dataclass(frozen=True, slots=True)
class IdentityUser:
    id: UUID
    username: str
    nickname: str
    avatar_url: str
    password_hash: str
    role: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class IssuedSession:
    access_token: str
    expires_at: datetime
    user: IdentityUser


class IdentityStore(Protocol):
    async def create_user(self, user: IdentityUser) -> IdentityUser: ...

    async def find_user_by_username(self, username: str) -> IdentityUser | None: ...

    async def find_user_by_id(self, user_id: UUID) -> IdentityUser | None: ...

    async def update_user(
        self, user_id: UUID, *, nickname: str | None, avatar_url: str | None
    ) -> IdentityUser | None: ...

    async def update_password(self, user_id: UUID, password_hash: str) -> IdentityUser | None: ...

    async def save_token(self, user_id: UUID, token_digest: str, expires_at: datetime) -> None: ...

    async def authenticate_token(
        self, token_digest: str, now: datetime, refreshed_expires_at: datetime
    ) -> IdentityUser | None: ...

    async def set_role(self, user_id: UUID, role: str) -> IdentityUser | None: ...


class PasswordHasher:
    """Scrypt password hashing without adding a runtime dependency."""

    _n = 16_384
    _r = 8
    _p = 1
    _length = 32

    def hash(self, password: str) -> str:
        salt = secrets.token_bytes(16)
        derived = hashlib.scrypt(
            password.encode("utf-8"), salt=salt, n=self._n, r=self._r, p=self._p, dklen=self._length
        )
        return "$".join(
            (
                "scrypt",
                str(self._n),
                str(self._r),
                str(self._p),
                base64.urlsafe_b64encode(salt).decode("ascii"),
                base64.urlsafe_b64encode(derived).decode("ascii"),
            )
        )

    def verify(self, password: str, encoded: str) -> bool:
        try:
            algorithm, n, r, p, encoded_salt, encoded_hash = encoded.split("$")
            if algorithm != "scrypt":
                return False
            salt = base64.urlsafe_b64decode(encoded_salt.encode("ascii"))
            expected = base64.urlsafe_b64decode(encoded_hash.encode("ascii"))
            actual = hashlib.scrypt(
                password.encode("utf-8"),
                salt=salt,
                n=int(n),
                r=int(r),
                p=int(p),
                dklen=len(expected),
            )
        except (TypeError, ValueError, UnicodeEncodeError):
            return False
        return hmac.compare_digest(actual, expected)


class IdentityService:
    """Small identity seam: registration, login, profile lookup, and bearer verification."""

    def __init__(
        self,
        store: IdentityStore,
        *,
        token_ttl_seconds: int,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._token_ttl = timedelta(seconds=token_ttl_seconds)
        self._hasher = PasswordHasher()
        self._clock = clock or _utc_now

    async def register(
        self, *, username: str, password: str, nickname: str, avatar_url: str | None
    ) -> IssuedSession:
        _validate_password(password)
        normalized_username = username.strip().lower()
        if await self._store.find_user_by_username(normalized_username):
            raise conflict("username is already registered")
        now = self._clock()
        user = IdentityUser(
            id=uuid4(),
            username=normalized_username,
            nickname=nickname.strip(),
            avatar_url=(avatar_url or DEFAULT_AVATAR_URL).strip(),
            password_hash=self._hasher.hash(password),
            role="user",
            created_at=now,
        )
        return await self._issue(await self._store.create_user(user), now)

    async def ensure_admin(self, *, username: str, password: str, nickname: str) -> None:
        _validate_password(password)
        normalized_username = username.strip().lower()
        existing = await self._store.find_user_by_username(normalized_username)
        if existing is None:
            await self._store.create_user(IdentityUser(
                id=uuid4(), username=normalized_username,
                nickname=nickname.strip() or "系统管理员", avatar_url=DEFAULT_AVATAR_URL,
                password_hash=self._hasher.hash(password), role="admin", created_at=self._clock(),
            ))
        else:
            if existing.role != "admin":
                await self._store.set_role(existing.id, "admin")
            if not self._hasher.verify(password, existing.password_hash):
                await self._store.update_password(existing.id, self._hasher.hash(password))

    async def login(self, *, username: str, password: str) -> IssuedSession:
        user = await self._store.find_user_by_username(username.strip().lower())
        if user is None or not self._hasher.verify(password, user.password_hash):
            raise AppError("UNAUTHORIZED", "invalid username or password", 401)
        return await self._issue(user, self._clock())

    async def authenticate(self, token: str) -> IdentityUser:
        now = self._clock()
        user = await self._store.authenticate_token(
            _token_digest(token), now, now + self._token_ttl
        )
        if user is None:
            raise AppError("UNAUTHORIZED", "invalid or expired access token", 401)
        return user

    async def profile(self, subject_id: str) -> IdentityUser:
        try:
            user_id = UUID(subject_id)
        except ValueError as error:
            raise not_found() from error
        user = await self._store.find_user_by_id(user_id)
        if user is None:
            raise not_found()
        return user

    async def update_profile(
        self, subject_id: str, *, nickname: str | None, avatar_url: str | None
    ) -> IdentityUser:
        try:
            user_id = UUID(subject_id)
        except ValueError as error:
            raise not_found() from error
        if nickname is None and avatar_url is None:
            raise AppError("VALIDATION_ERROR", "at least one profile field is required", 422)
        user = await self._store.update_user(
            user_id,
            nickname=nickname.strip() if nickname is not None else None,
            avatar_url=avatar_url.strip() if avatar_url is not None else None,
        )
        if user is None:
            raise not_found()
        return user

    async def change_password(
        self, subject_id: str, *, current_password: str, new_password: str
    ) -> IdentityUser:
        try:
            user_id = UUID(subject_id)
        except ValueError as error:
            raise not_found() from error
        user = await self._store.find_user_by_id(user_id)
        if user is None:
            raise not_found()
        if not self._hasher.verify(current_password, user.password_hash):
            raise AppError("UNAUTHORIZED", "current password is incorrect", 401)
        _validate_password(new_password)
        if current_password == new_password:
            raise AppError(
                "VALIDATION_ERROR", "new password must differ from current password", 422
            )
        updated = await self._store.update_password(user_id, self._hasher.hash(new_password))
        if updated is None:
            raise not_found()
        return updated

    async def _issue(self, user: IdentityUser, now: datetime) -> IssuedSession:
        token = secrets.token_urlsafe(32)
        expires_at = now + self._token_ttl
        await self._store.save_token(user.id, _token_digest(token), expires_at)
        return IssuedSession(access_token=token, expires_at=expires_at, user=user)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _validate_password(password: str) -> None:
    if not 6 <= len(password) <= 20:
        raise AppError("VALIDATION_ERROR", "password must be 6-20 characters", 422)
    if not any(char.isalpha() for char in password):
        raise AppError("VALIDATION_ERROR", "password must include a letter", 422)
    if not any(char.isdigit() for char in password):
        raise AppError("VALIDATION_ERROR", "password must include a number", 422)
    if not any(not char.isalnum() for char in password):
        raise AppError("VALIDATION_ERROR", "password must include a special character", 422)
