from __future__ import annotations

import json

import pytest
from httpx import AsyncClient

USER = {"Authorization": "Bearer alice:user"}
REVIEWER = {"Authorization": "Bearer reviewer:reviewer"}


@pytest.mark.asyncio
async def test_health_is_anonymous_and_ready_is_degraded(client: AsyncClient) -> None:
    live = await client.get("/api/v1/health/live")
    assert live.status_code == 200
    assert live.json()["data"]["status"] == "alive"
    ready = await client.get("/api/v1/health/ready")
    assert ready.status_code == 200
    assert ready.json()["data"]["dependencies"]["postgresql"] == "available"
    assert "request_id" in ready.json()


@pytest.mark.asyncio
async def test_session_idempotency_replay_and_reuse(client: AsyncClient) -> None:
    headers = {**USER, "Idempotency-Key": "session-key-000001"}
    first = await client.post("/api/v1/sessions", headers=headers, json={"title": "Support"})
    assert first.status_code == 201
    second = await client.post("/api/v1/sessions", headers=headers, json={"title": "Support"})
    assert second.status_code == 201
    assert second.headers["idempotency-replayed"] == "true"
    assert second.json()["data"]["id"] == first.json()["data"]["id"]
    reused = await client.post("/api/v1/sessions", headers=headers, json={"title": "Different"})
    assert reused.status_code == 409
    assert reused.json()["code"] == "IDEMPOTENCY_KEY_REUSED"


@pytest.mark.asyncio
async def test_chat_new_stream_persists_message_and_replays(client: AsyncClient) -> None:
    session = await client.post(
        "/api/v1/sessions",
        headers={**USER, "Idempotency-Key": "session-key-000002"},
        json={},
    )
    session_id = session.json()["data"]["id"]
    headers = {**USER, "Idempotency-Key": "stream-key-000001"}
    response = await client.post(
        "/api/v1/chat/stream",
        headers=headers,
        json={"mode": "new", "session_id": session_id, "content": "hello"},
    )
    assert response.status_code == 200
    lines = [line for line in response.text.splitlines() if line.startswith("data:")]
    events = [json.loads(line.removeprefix("data: ")) for line in lines]
    assert [event["event_type"] for event in events] == ["meta", "status", "delta", "done"]
    assert events[-1]["payload"]["outcome"] == "completed"
    run_id = response.headers["x-run-id"]
    replay = await client.post(
        "/api/v1/chat/stream",
        headers={**headers, "Last-Event-ID": "2"},
        json={"mode": "new", "session_id": session_id, "content": "hello"},
    )
    assert replay.status_code == 200
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.headers["x-run-id"] == run_id
    replay_events = [
        json.loads(line.removeprefix("data: "))
        for line in replay.text.splitlines()
        if line.startswith("data:")
    ]
    assert [event["event_type"] for event in replay_events] == ["delta", "done"]

    messages = await client.get(f"/api/v1/sessions/{session_id}/messages", headers=USER)
    assert messages.status_code == 200
    assert [item["role"] for item in messages.json()["data"]["items"]] == ["user", "assistant"]


@pytest.mark.asyncio
async def test_auth_and_validation_errors_are_contract_envelopes(client: AsyncClient) -> None:
    missing = await client.get("/api/v1/sessions")
    assert missing.status_code == 401
    assert missing.json()["code"] == "UNAUTHORIZED"
    bad = await client.post(
        "/api/v1/sessions",
        headers={**USER, "Idempotency-Key": "too-short"},
        json={"title": ""},
    )
    assert bad.status_code == 422
    assert bad.json()["code"] == "VALIDATION_ERROR"


@pytest.mark.asyncio
async def test_cancel_is_idempotent_and_terminal(client: AsyncClient) -> None:
    session = await client.post(
        "/api/v1/sessions",
        headers={**USER, "Idempotency-Key": "session-key-000003"},
        json={},
    )
    session_id = session.json()["data"]["id"]
    # Stream is already terminal with the deterministic fake, so cancellation uses 200.
    streamed = await client.post(
        "/api/v1/chat/stream",
        headers={**USER, "Idempotency-Key": "stream-key-000002"},
        json={"mode": "new", "session_id": session_id, "content": "hello"},
    )
    run_id = streamed.headers["x-run-id"]
    cancelled = await client.post(
        f"/api/v1/runs/{run_id}/cancel",
        headers={**USER, "Idempotency-Key": "cancel-key-000001"},
        json={"reason": "user requested"},
    )
    assert cancelled.status_code == 200
    assert cancelled.json()["data"]["status"] == "already_terminal"
