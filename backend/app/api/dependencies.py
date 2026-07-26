from __future__ import annotations

from typing import cast

from fastapi import Request

from backend.app.application.service import PlatformService


def get_service(request: Request) -> PlatformService:
    return cast(PlatformService, request.app.state.platform_service)
