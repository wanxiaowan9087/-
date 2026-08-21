from __future__ import annotations

from typing import cast

from fastapi import Request

from backend.app.application.identity import IdentityService
from backend.app.application.service import PlatformService
from backend.app.application.phone_crypto import PhoneProtector
from backend.app.application.sms_verification import SmsVerificationService


def get_service(request: Request) -> PlatformService:
    return cast(PlatformService, request.app.state.platform_service)


def get_identity_service(request: Request) -> IdentityService:
    return cast(IdentityService, request.app.state.identity_service)


def get_phone_protector(request: Request) -> PhoneProtector:
    return cast(PhoneProtector, request.app.state.phone_protector)


def get_sms_service(request: Request) -> SmsVerificationService:
    return cast(SmsVerificationService, request.app.state.sms_service)
