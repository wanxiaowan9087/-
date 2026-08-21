from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from backend.app.adapters.auth.memory import MemoryIdentityStore
from backend.app.application.legal import CURRENT_LEGAL_DOCUMENTS
from backend.app.application.identity import IdentityService
from backend.app.core.errors import AppError


async def register(client: AsyncClient, phone: str, nickname: str) -> str:
    versions = {item.document_type: item.version for item in CURRENT_LEGAL_DOCUMENTS}
    code_response = await client.post(
        "/api/v1/auth/sms-codes", json={"phone": phone, "purpose": "register"}
    )
    assert code_response.status_code == 200
    provider = client._transport.app.state.sms_service.provider  # type: ignore[attr-defined]
    response = await client.post(
        "/api/v1/auth/register",
        json={
            "phone": phone,
            "password": "Safe@123",
            "nickname": nickname,
            "verification_code": provider.last_code,
            "user_agreement_version": versions["user_agreement"],
            "privacy_policy_version": versions["privacy_policy"],
            "agree_user_agreement": True,
            "agree_privacy_policy": True,
        },
    )
    assert response.status_code == 201
    assert response.json()["data"]["user"]["nickname"] == nickname
    assert response.json()["data"]["user"]["avatar_url"].startswith("https://images.unsplash.com/")
    return response.json()["data"]["access_token"]


@pytest.mark.asyncio
async def test_registered_identity_isolated_sessions(identity_client: AsyncClient) -> None:
    alice_token = await register(identity_client, "13800138001", "Alice")
    bob_token = await register(identity_client, "13800138002", "Bob")
    alice = {"Authorization": f"Bearer {alice_token}"}
    bob = {"Authorization": f"Bearer {bob_token}"}

    profile = await identity_client.get("/api/v1/auth/me", headers=alice)
    assert profile.status_code == 200
    assert profile.json()["data"]["username"].startswith("phone_")

    created = await identity_client.post(
        "/api/v1/sessions",
        headers={**alice, "Idempotency-Key": "identity-session-key-00001"},
        json={"title": "Alice private session"},
    )
    assert created.status_code == 201
    session_id = created.json()["data"]["id"]

    bob_sessions = await identity_client.get("/api/v1/sessions", headers=bob)
    assert bob_sessions.status_code == 200
    assert bob_sessions.json()["data"]["items"] == []
    blocked = await identity_client.get(f"/api/v1/sessions/{session_id}", headers=bob)
    assert blocked.status_code == 404


@pytest.mark.asyncio
async def test_login_rejects_wrong_password(identity_client: AsyncClient) -> None:
    await register(identity_client, "13800138003", "登录用户")
    response = await identity_client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138003", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_profile_can_update_nickname_and_avatar(identity_client: AsyncClient) -> None:
    token = await register(identity_client, "13800138004", "原昵称")
    response = await identity_client.patch(
        "/api/v1/auth/me",
        headers={"Authorization": f"Bearer {token}"},
        json={"nickname": "新昵称", "avatar_url": "https://example.com/avatar.png"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["nickname"] == "新昵称"
    assert response.json()["data"]["avatar_url"] == "https://example.com/avatar.png"


@pytest.mark.asyncio
async def test_profile_password_change_requires_current_password(
    identity_client: AsyncClient,
) -> None:
    token = await register(identity_client, "13800138005", "Password user")
    headers = {"Authorization": f"Bearer {token}"}
    changed = await identity_client.patch(
        "/api/v1/auth/me/password",
        headers=headers,
        json={"current_password": "Safe@123", "new_password": "New@456"},
    )
    assert changed.status_code == 200
    login = await identity_client.post(
        "/api/v1/auth/login",
        json={"phone": "13800138005", "password": "New@456"},
    )
    assert login.status_code == 200


@pytest.mark.asyncio
async def test_valid_token_uses_a_seven_day_sliding_expiration() -> None:
    current_time = [datetime(2026, 8, 1, 8, tzinfo=UTC)]
    identity = IdentityService(
        MemoryIdentityStore(),
        token_ttl_seconds=7 * 24 * 60 * 60,
        clock=lambda: current_time[0],
    )
    grant = await identity.register(
        username="sliding_user",
        password="Safe@123",
        nickname="滑动续期用户",
        avatar_url=None,
    )

    current_time[0] += timedelta(days=6)
    assert (await identity.authenticate(grant.access_token)).id == grant.user.id

    # The original token would have expired on day seven. Authentication on day six
    # renews it, so it remains valid on day twelve and renews once more.
    current_time[0] += timedelta(days=6)
    assert (await identity.authenticate(grant.access_token)).id == grant.user.id

    current_time[0] += timedelta(days=8)
    with pytest.raises(AppError) as error:
        await identity.authenticate(grant.access_token)
    assert error.value.code == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_password_requires_letter_number_and_special_character(
    identity_client: AsyncClient,
) -> None:
    response = await identity_client.post(
        "/api/v1/auth/register",
        json={
            "phone": "13800138006",
            "password": "letters123",
            "nickname": "弱密码",
            "verification_code": "123456",
            "user_agreement_version": CURRENT_LEGAL_DOCUMENTS[0].version,
            "privacy_policy_version": CURRENT_LEGAL_DOCUMENTS[1].version,
            "agree_user_agreement": True,
            "agree_privacy_policy": True,
        },
    )
    assert response.status_code == 422
    assert response.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_ensure_admin_synchronizes_bootstrap_password() -> None:
    store = MemoryIdentityStore()
    service = IdentityService(store, token_ttl_seconds=3600)
    await service.ensure_admin(username="xiaow", password="Old@123", nickname="Admin")
    await service.ensure_admin(username="xiaow", password="New@456", nickname="Admin")

    session = await service.login(username="xiaow", password="New@456")

    assert session.user.role == "admin"
