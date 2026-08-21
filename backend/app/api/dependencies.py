from __future__ import annotations

import ipaddress
from typing import cast

from fastapi import Request

from backend.app.application.identity import IdentityService
from backend.app.application.phone_crypto import PhoneProtector
from backend.app.application.security_rate_limit import SecurityRateLimiter
from backend.app.application.service import PlatformService
from backend.app.application.sms_verification import SmsVerificationService


def get_service(request: Request) -> PlatformService:
    return cast(PlatformService, request.app.state.platform_service)


def get_identity_service(request: Request) -> IdentityService:
    return cast(IdentityService, request.app.state.identity_service)


def get_phone_protector(request: Request) -> PhoneProtector:
    return cast(PhoneProtector, request.app.state.phone_protector)


def get_sms_service(request: Request) -> SmsVerificationService:
    return cast(SmsVerificationService, request.app.state.sms_service)


def get_security_rate_limiter(request: Request) -> SecurityRateLimiter:
    return cast(SecurityRateLimiter, request.app.state.security_rate_limiter)


def get_client_ip(request: Request) -> str:
    candidate = request.headers.get("X-Real-IP")
    fallback = request.client.host if request.client is not None else "127.0.0.1"
    try:
        return str(ipaddress.ip_address(candidate or fallback))
    except ValueError:
        return "127.0.0.1"
