from __future__ import annotations

import json
from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest
from httpx import ASGITransport, AsyncClient, Response

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.core.config import Settings
from backend.app.main import create_app
from backend.tests.conftest import FakeExecutor, ScenarioHarness

USER = {"Authorization": "Bearer alice:user"}
REVIEWER = {"Authorization": "Bearer reviewer:reviewer"}
OTHER_USER = {"Authorization": "Bearer bob:user"}


def _headers(key: str, principal: dict[str, str] = USER) -> dict[str, str]:
    return {**principal, "Idempotency-Key": key}


def _assert_envelope(response: Response) -> None:
    body = response.json()
    assert "request_id" in body
    assert response.headers["x-request-id"] == body["request_id"]


async def _create_session(client: AsyncClient, key: str, title: str = "Scenario") -> str:
    response = await client.post("/api/v1/sessions", headers=_headers(key), json={"title": title})
    assert response.status_code == 201
    _assert_envelope(response)
    return response.json()["data"]["id"]


async def _stream_new(
    client: AsyncClient, session_id: str, key: str, content: str = "hello"
) -> str:
    response = await client.post(
        "/api/v1/chat/stream",
        headers=_headers(key),
        json={"mode": "new", "session_id": session_id, "content": content},
    )
    assert response.status_code == 200
    assert response.headers["x-request-id"]
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data:")
    ]
    assert [event["event_type"] for event in events] == ["meta", "status", "delta", "done"]
    assert events[-1]["payload"]["outcome"] == "completed"
    return response.headers["x-run-id"]


async def _streamed_ids(client: AsyncClient, session_id: str, key: str) -> tuple[str, str, str]:
    run_id = await _stream_new(client, session_id, key)
    messages = await client.get(f"/api/v1/sessions/{session_id}/messages", headers=USER)
    assert messages.status_code == 200
    _assert_envelope(messages)
    items = messages.json()["data"]["items"]
    return run_id, items[0]["id"], items[-1]["id"]


@pytest.mark.asyncio
async def test_apifox_001_session_stream_messages_and_trace(
    scenario_harness: ScenarioHarness,
) -> None:
    client = scenario_harness.client
    session_id = await _create_session(client, "scenario-001-session")
    run_id, _, _ = await _streamed_ids(client, session_id, "scenario-001-stream")

    messages = await client.get(f"/api/v1/sessions/{session_id}/messages?limit=20", headers=USER)
    assert messages.status_code == 200
    assert [item["role"] for item in messages.json()["data"]["items"]] == ["user", "assistant"]

    trace = await client.get(f"/api/v1/runs/{run_id}/trace", headers=USER)
    assert trace.status_code == 200
    _assert_envelope(trace)
    assert trace.json()["data"]["run_id"] == run_id


@pytest.mark.asyncio
async def test_apifox_002_idempotency_replay_and_key_reuse(
    scenario_harness: ScenarioHarness,
) -> None:
    client = scenario_harness.client
    headers = _headers("scenario-002-session")
    first = await client.post("/api/v1/sessions", headers=headers, json={"title": "Replay"})
    replay = await client.post("/api/v1/sessions", headers=headers, json={"title": "Replay"})
    conflict = await client.post("/api/v1/sessions", headers=headers, json={"title": "Changed"})

    assert first.status_code == replay.status_code == 201
    assert replay.headers["idempotency-replayed"] == "true"
    assert replay.json()["data"]["id"] == first.json()["data"]["id"]
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "IDEMPOTENCY_KEY_REUSED"
    _assert_envelope(conflict)


@pytest.mark.asyncio
async def test_apifox_003_retry_reuses_original_user_message(
    scenario_harness: ScenarioHarness,
) -> None:
    client = scenario_harness.client
    session_id = await _create_session(client, "scenario-003-session")
    first_run_id, original_user_message_id, _ = await _streamed_ids(
        client, session_id, "scenario-003-new"
    )

    retry = await client.post(
        "/api/v1/chat/stream",
        headers=_headers("scenario-003-retry"),
        json={
            "mode": "retry",
            "session_id": session_id,
            "original_user_message_id": original_user_message_id,
        },
    )
    assert retry.status_code == 200
    assert retry.headers["x-run-id"] != first_run_id
    messages = await client.get(f"/api/v1/sessions/{session_id}/messages", headers=USER)
    items = messages.json()["data"]["items"]
    assert [item["role"] for item in items] == ["user", "assistant", "assistant"]
    assert [item["id"] for item in items].count(original_user_message_id) == 1


@pytest.mark.asyncio
async def test_apifox_004_run_cancellation_is_idempotent(scenario_harness: ScenarioHarness) -> None:
    client = scenario_harness.client
    session_id = await _create_session(client, "scenario-004-session")
    run_id = await _stream_new(client, session_id, "scenario-004-stream")
    headers = _headers("scenario-004-cancel")
    first = await client.post(
        f"/api/v1/runs/{run_id}/cancel", headers=headers, json={"reason": "stop"}
    )
    replay = await client.post(
        f"/api/v1/runs/{run_id}/cancel", headers=headers, json={"reason": "stop"}
    )

    assert first.status_code == replay.status_code == 200
    assert first.json()["data"]["status"] == "already_terminal"
    assert replay.headers["idempotency-replayed"] == "true"


@pytest.mark.asyncio
async def test_apifox_005_pagination_cursor_and_object_isolation(
    scenario_harness: ScenarioHarness,
) -> None:
    client = scenario_harness.client
    own_session_id = await _create_session(client, "scenario-005-own")
    foreign = await client.post(
        "/api/v1/sessions",
        headers=_headers("scenario-005-foreign", OTHER_USER),
        json={"title": "Bob"},
    )
    foreign_session_id = foreign.json()["data"]["id"]

    listed = await client.get("/api/v1/sessions?limit=20", headers=USER)
    malformed = await client.get("/api/v1/sessions?cursor=malformed-cursor&limit=20", headers=USER)
    foreign_read = await client.get(f"/api/v1/sessions/{foreign_session_id}", headers=USER)
    unknown = await client.get(f"/api/v1/sessions/{uuid4()}", headers=USER)

    assert listed.status_code == 200
    assert own_session_id in {item["id"] for item in listed.json()["data"]["items"]}
    assert malformed.status_code == 400
    assert foreign_read.status_code == unknown.status_code == 404
    for response in (malformed, foreign_read, unknown):
        _assert_envelope(response)


@pytest.mark.asyncio
async def test_apifox_006_feedback_submission_is_deduplicated(
    scenario_harness: ScenarioHarness,
) -> None:
    client = scenario_harness.client
    session_id = await _create_session(client, "scenario-006-session")
    _, _, assistant_message_id = await _streamed_ids(client, session_id, "scenario-006-stream")
    body = {"rating": "positive", "reason": "helpful"}
    first = await client.post(
        f"/api/v1/chat/{assistant_message_id}/feedback",
        headers=_headers("scenario-006-feedback-one"),
        json=body,
    )
    duplicate = await client.post(
        f"/api/v1/chat/{assistant_message_id}/feedback",
        headers=_headers("scenario-006-feedback-two"),
        json=body,
    )

    assert first.status_code == 201
    assert duplicate.status_code == 409
    _assert_envelope(first)
    _assert_envelope(duplicate)


@pytest.mark.asyncio
async def test_apifox_007_memory_version_controls(scenario_harness: ScenarioHarness) -> None:
    client = scenario_harness.client
    now = datetime.now(UTC)
    async with scenario_harness.repository.transaction() as tx:
        memory = await tx.create_memory(
            "alice",
            memory_type="preference",
            content="prefers concise replies",
            confidence=0.9,
            source_message_id=uuid4(),
            now=now,
        )

    listed = await client.get("/api/v1/memories?limit=20", headers=USER)
    corrected = await client.patch(
        f"/api/v1/memories/{memory.id}",
        headers=_headers("scenario-007-correct"),
        json={"action": "correct", "content": "prefers detailed replies", "expected_version": 1},
    )
    stale = await client.patch(
        f"/api/v1/memories/{memory.id}",
        headers=_headers("scenario-007-stale"),
        json={"action": "deactivate", "expected_version": 1, "reason": "obsolete"},
    )
    deleted = await client.delete(
        f"/api/v1/memories/{memory.id}",
        headers={**_headers("scenario-007-delete"), "If-Match": "2"},
    )

    assert listed.status_code == 200
    assert str(memory.id) in {item["id"] for item in listed.json()["data"]["items"]}
    assert corrected.status_code == 200
    assert corrected.json()["data"]["version"] == 2
    assert stale.status_code == 409
    assert deleted.status_code == 200


@pytest.mark.asyncio
async def test_apifox_008_reviewer_decision_and_user_denial(
    scenario_harness: ScenarioHarness,
) -> None:
    client = scenario_harness.client
    session_id = await _create_session(client, "scenario-008-session")
    run_id, user_message_id, _ = await _streamed_ids(client, session_id, "scenario-008-stream")
    async with scenario_harness.repository.transaction() as tx:
        review = await tx.create_review(
            run_id=UUID(run_id),
            session_id=UUID(session_id),
            owner_id="alice",
            user_message_id=UUID(user_message_id),
            candidate_content="Reviewed answer",
            reason_codes=["low_confidence"],
            confidence=0.5,
            now=datetime.now(UTC),
        )

    denied = await client.get("/api/v1/reviews?limit=20", headers=USER)
    listed = await client.get("/api/v1/reviews?limit=20", headers=REVIEWER)
    approve = await client.post(
        f"/api/v1/reviews/{review.id}/decision",
        headers=_headers("scenario-008-approve", REVIEWER),
        json={"decision": "approve", "expected_version": 1},
    )
    repeated = await client.post(
        f"/api/v1/reviews/{review.id}/decision",
        headers=_headers("scenario-008-repeat", REVIEWER),
        json={"decision": "approve", "expected_version": 1},
    )

    assert denied.status_code == 403
    assert listed.status_code == 200
    assert approve.status_code == 200
    assert approve.json()["data"]["status"] == "approved"
    assert repeated.status_code == 409


class _UnavailableRepository(MemoryPlatformRepository):
    async def ping(self) -> bool:
        return False


@pytest.mark.asyncio
async def test_apifox_009_validation_auth_and_dependency_errors_are_safe() -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./scenario-unavailable.db",
        redis_url=None,
        demo_auth_enabled=True,
    )
    app = create_app(settings, repository=_UnavailableRepository(), executor=FakeExecutor())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        invalid = await client.post(
            "/api/v1/sessions", headers=_headers("scenario-009-invalid"), json={"title": ""}
        )
        missing_auth = await client.get("/api/v1/sessions")
        unavailable = await client.get("/api/v1/health/ready")

    assert invalid.status_code == 422
    assert missing_auth.status_code == 401
    assert unavailable.status_code == 503
    for response in (invalid, missing_auth, unavailable):
        _assert_envelope(response)
    assert unavailable.json()["code"] == "STORAGE_UNAVAILABLE"
    await app.state.platform_service.close()
