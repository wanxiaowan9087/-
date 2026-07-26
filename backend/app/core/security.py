from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from fastapi import Depends
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.app.core.config import Settings, get_settings
from backend.app.core.errors import AppError


@dataclass(frozen=True, slots=True)
class Principal:
    subject_id: str
    role: str


class AuthenticationPort(Protocol):
    async def authenticate(self, token: str) -> Principal: ...


class DemoAuthenticationAdapter:
    """Local adapter for `subject:role` tokens; production must inject a real verifier."""

    async def authenticate(self, token: str) -> Principal:
        try:
            subject, role = token.rsplit(":", 1)
        except ValueError as exc:
            raise AppError("UNAUTHORIZED", "invalid bearer token", 401) from exc
        if not subject or role not in {"user", "reviewer"}:
            raise AppError("UNAUTHORIZED", "invalid bearer token", 401)
        return Principal(subject_id=subject, role=role)


bearer = HTTPBearer(auto_error=False)


async def get_principal(
    credentials: HTTPAuthorizationCredentials | None = Depends(bearer),
    settings: Settings = Depends(get_settings),
) -> Principal:
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise AppError("UNAUTHORIZED", "authentication required", 401)
    if not settings.demo_auth_enabled:
        raise AppError("UNAUTHORIZED", "no production authentication adapter configured", 401)
    return await DemoAuthenticationAdapter().authenticate(credentials.credentials)


async def require_reviewer(principal: Principal = Depends(get_principal)) -> Principal:
    if principal.role != "reviewer":
        raise AppError("FORBIDDEN", "reviewer role required", 403)
    return principal
