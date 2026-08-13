from __future__ import annotations

from typing import cast

from fastapi import Request

from backend.app.application.identity import IdentityService
from backend.app.application.service import PlatformService


def get_service(request: Request) -> PlatformService:
    return cast(PlatformService, request.app.state.platform_service)


def get_identity_service(request: Request) -> IdentityService:
    return cast(IdentityService, request.app.state.identity_service)
