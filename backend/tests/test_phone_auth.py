from __future__ import annotations

from collections.abc import AsyncIterator

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.application.legal import CURRENT_LEGAL_DOCUMENTS
from backend.app.core.config import Settings
from backend.app.main import create_app


@pytest_asyncio.fixture
async def phone_client() -> AsyncIterator[tuple[AsyncClient, object, object]]:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./phone-auth-test.db",
        redis_url=None,
        demo_auth_enabled=False,
        sms_provider="fake",
        phone_encryption_key="phone-auth-test-encryption-key",
        phone_lookup_hmac_key="phone-auth-test-lookup-key",
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, app.state.sms_service.provider, app
    await app.state.platform_service.close()


def legal_versions() -> dict[str, str]:
    return {item.document_type: item.version for item in CURRENT_LEGAL_DOCUMENTS}


async def send_code(client: AsyncClient, provider: object, phone: str, purpose: str) -> str:
    response = await client.post(
        "/api/v1/auth/sms-codes", json={"phone": phone, "purpose": purpose}
    )
    assert response.status_code == 200
    return str(provider.last_code)  # type: ignore[attr-defined]


async def register_phone(
    client: AsyncClient, provider: object, phone: str = "13800138000"
) -> tuple[str, str]:
    code = await send_code(client, provider, phone, "register")
    versions = legal_versions()
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "nickname": "测试用户",
            "password": "Safe@123",
            "verification_code": code,
            "user_agreement_version": versions["user_agreement"],
            "privacy_policy_version": versions["privacy_policy"],
            "agree_user_agreement": True,
            "agree_privacy_policy": True,
        },
    )
    assert response.status_code == 201
    return response.json()["data"]["access_token"], code


async def test_phone_registration_login_and_consent(phone_client) -> None:
    client, provider, _ = phone_client
    token, _ = await register_phone(client, provider)
    profile = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert profile.status_code == 200
    assert profile.json()["data"]["username"].startswith("phone_")
    login = await client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138000", "password": "Safe@123"},
    )
    assert login.status_code == 200
    assert profile.json()["data"]["id"] == login.json()["data"]["user"]["id"]
    assert provider is not None


async def test_phone_reset_revokes_old_token(phone_client) -> None:
    client, provider, app = phone_client
    token, _ = await register_phone(client, provider, "13900139000")
    app.state.sms_service.limiter._sent.clear()  # type: ignore[attr-defined]
    code = await send_code(client, provider, "13900139000", "password_reset")
    reset = await client.post(
        "/api/v1/auth/password-resets",
        json={
            "phone": "13900139000",
            "verification_code": code,
            "new_password": "Reset@456",
        },
    )
    assert reset.status_code == 200
    old_profile = await client.get(
        "/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert old_profile.status_code == 401
    login = await client.post(
        "/api/v1/auth/login",
        json={"phone": "13900139000", "password": "Reset@456"},
    )
    assert login.status_code == 200
