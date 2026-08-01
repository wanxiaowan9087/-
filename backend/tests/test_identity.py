from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from httpx import AsyncClient

from backend.app.adapters.auth.memory import MemoryIdentityStore
from backend.app.application.identity import IdentityService
from backend.app.core.errors import AppError


async def register(client: AsyncClient, username: str, nickname: str) -> str:
    response = await client.post(
        "/api/v1/auth/register",
        json={"username": username, "password": "safe-password-123", "nickname": nickname},
    )
    assert response.status_code == 201
    assert response.json()["data"]["user"]["nickname"] == nickname
    assert response.json()["data"]["user"]["avatar_url"].startswith("https://images.unsplash.com/")
    return response.json()["data"]["access_token"]


@pytest.mark.asyncio
async def test_registered_identity_isolated_sessions(identity_client: AsyncClient) -> None:
    alice_token = await register(identity_client, "alice_01", "Alice")
    bob_token = await register(identity_client, "bob_01", "Bob")
    alice = {"Authorization": f"Bearer {alice_token}"}
    bob = {"Authorization": f"Bearer {bob_token}"}

    profile = await identity_client.get("/api/v1/auth/me", headers=alice)
    assert profile.status_code == 200
    assert profile.json()["data"]["username"] == "alice_01"

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
    await register(identity_client, "login_user", "登录用户")
    response = await identity_client.post(
        "/api/v1/auth/login",
        json={"username": "login_user", "password": "wrong-password"},
    )
    assert response.status_code == 401
    assert response.json()["code"] == "UNAUTHORIZED"


@pytest.mark.asyncio
async def test_profile_can_update_nickname_and_avatar(identity_client: AsyncClient) -> None:
    token = await register(identity_client, "profile_01", "原昵称")
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
    token = await register(identity_client, "password_01", "Password user")
    headers = {"Authorization": f"Bearer {token}"}
    changed = await identity_client.patch(
        "/api/v1/auth/me/password",
        headers=headers,
        json={"current_password": "safe-password-123", "new_password": "new-safe-password-456"},
    )
    assert changed.status_code == 200
    login = await identity_client.post(
        "/api/v1/auth/login",
        json={"username": "password_01", "password": "new-safe-password-456"},
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
        password="safe-password-123",
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
