from __future__ import annotations

import pytest
from httpx import AsyncClient


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
