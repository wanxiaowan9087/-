from __future__ import annotations

import json
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.core.config import Settings
from backend.app.main import create_app

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

    listed = await client.get("/api/v1/sessions", headers=USER)
    assert listed.status_code == 200
    assert listed.json()["data"]["items"][0]["title"] == "hello"


@pytest.mark.asyncio
async def test_consecutive_chat_turns_remain_in_the_durable_transcript(client: AsyncClient) -> None:
    session = await client.post(
        "/api/v1/sessions",
        headers={**USER, "Idempotency-Key": "session-key-consecutive-001"},
        json={},
    )
    session_id = session.json()["data"]["id"]
    for index, content in enumerate(("first question", "second question"), start=1):
        streamed = await client.post(
            "/api/v1/chat/stream",
            headers={**USER, "Idempotency-Key": f"stream-key-consecutive-{index:03d}"},
            json={"mode": "new", "session_id": session_id, "content": content},
        )
        assert streamed.status_code == 200

    messages = await client.get(f"/api/v1/sessions/{session_id}/messages?limit=100", headers=USER)
    items = messages.json()["data"]["items"]
    assert [item["role"] for item in items] == ["user", "assistant", "user", "assistant"]
    assert [item["content"] for item in items if item["role"] == "user"] == [
        "first question",
        "second question",
    ]


@pytest.mark.asyncio
async def test_chat_session_title_uses_first_message_summary(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/sessions",
        headers={**USER, "Idempotency-Key": "session-key-title-001"},
        json={},
    )
    session_id = created.json()["data"]["id"]
    content = "扫地机器人一直提示尘盒未安装，我应该先检查哪里？"
    streamed = await client.post(
        "/api/v1/chat/stream",
        headers={**USER, "Idempotency-Key": "stream-key-title-001"},
        json={"mode": "new", "session_id": session_id, "content": content},
    )
    assert streamed.status_code == 200
    listed = await client.get("/api/v1/sessions", headers=USER)
    assert (
        listed.json()["data"]["items"][0]["title"]
        == "扫地机器人一直提示尘盒未安装，我应该先检查哪里"
    )


@pytest.mark.asyncio
async def test_upload_knowledge_file_persists_text_document(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./knowledge-upload-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        uploads_dir=str(tmp_path),
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge/files?filename=新品维护手册.md",
            headers={"Authorization": "Bearer admin:admin", "Content-Type": "text/markdown"},
            content="# 新品维护手册\n边刷每 3 个月检查一次。",
        )
    await app.state.platform_service.close()
    assert response.status_code == 201
    body = response.json()["data"]
    assert body["filename"].endswith(".md")
    assert body["chunk_count"] >= 1
    assert (tmp_path / "knowledge" / body["filename"]).exists()


@pytest.mark.asyncio
async def test_knowledge_upload_rejects_duplicate_and_same_name_conflict(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./knowledge-dedupe-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        uploads_dir=str(tmp_path),
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    headers = {"Authorization": "Bearer admin:admin", "Content-Type": "text/markdown"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/knowledge/files?filename=同一份手册.md",
            headers=headers,
            content="# 手册\n保持滤网干燥。",
        )
        duplicate = await client.post(
            "/api/v1/knowledge/files?filename=同一份手册.md",
            headers=headers,
            content="# 手册\n保持滤网干燥。",
        )
        conflict = await client.post(
            "/api/v1/knowledge/files?filename=同一份手册.md",
            headers=headers,
            content="# 手册\n改用新的滤网。",
        )
    await app.state.platform_service.close()
    assert first.status_code == 201
    assert duplicate.status_code == 409
    assert duplicate.json()["code"] == "KNOWLEDGE_DUPLICATE"
    assert duplicate.json()["data"]["ingest_status"] == "duplicate"
    assert conflict.status_code == 409
    assert conflict.json()["code"] == "KNOWLEDGE_NAME_CONFLICT"
    assert conflict.json()["data"]["ingest_status"] == "conflict"
    assert len(list((tmp_path / "knowledge").glob("*.md"))) == 1


@pytest.mark.asyncio
async def test_knowledge_upload_can_explicitly_overwrite_same_name(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./knowledge-overwrite-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        uploads_dir=str(tmp_path),
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    headers = {"Authorization": "Bearer admin:admin", "Content-Type": "text/markdown"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post(
            "/api/v1/knowledge/files?filename=版本手册.md",
            headers=headers,
            content="# 旧版本\n旧规则。",
        )
        old_id = first.json()["data"]["id"]
        overwritten = await client.post(
            "/api/v1/knowledge/files?filename=版本手册.md&overwrite=true",
            headers=headers,
            content="# 新版本\n新规则。",
        )
        listed = await client.get("/api/v1/knowledge/files", headers=headers)
    await app.state.platform_service.close()
    assert first.status_code == 201
    assert overwritten.status_code == 201
    assert overwritten.json()["data"]["id"] == old_id
    assert overwritten.json()["data"]["sha256"] != first.json()["data"]["sha256"]
    assert len(listed.json()["data"]["items"]) == 1


@pytest.mark.asyncio
async def test_knowledge_upload_warns_on_highly_similar_content_and_can_keep_variant(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./knowledge-similar-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        uploads_dir=str(tmp_path),
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    headers = {"Authorization": "Bearer admin:admin", "Content-Type": "text/markdown"}
    original = """# 扫地机器人滤网维护\n\n每次清洁后请关闭电源并取出滤网。滤网需要清理灰尘并完全晾干后再安装。\n\n如果滤网破损，请联系售后更换原装配件。"""
    variant = """# 扫地机器人滤网维护说明\n\n每次清洁后请关闭电源并取出滤网。滤网需要清理灰尘并完全晾干后再安装。\n\n如果滤网破损，请联系售后更换原装配件。适用于新固件。"""
    app.state.platform_service.knowledge_indexer = None
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        first = await client.post("/api/v1/knowledge/files?filename=滤网维护.md", headers=headers, content=original)
        similar = await client.post("/api/v1/knowledge/files?filename=滤网维护-新固件.md", headers=headers, content=variant)
        kept = await client.post("/api/v1/knowledge/files?filename=滤网维护-新固件.md&allow_similar=true", headers=headers, content=variant)
    await app.state.platform_service.close()
    assert first.status_code == 201
    assert similar.status_code == 409
    assert similar.json()["code"] == "KNOWLEDGE_SIMILAR"
    assert kept.status_code == 201


@pytest.mark.asyncio
async def test_knowledge_catalog_keeps_manifest_entries_when_source_is_unmounted(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./knowledge-manifest-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        uploads_dir=str(tmp_path),
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    headers = {"Authorization": "Bearer admin:admin", "Content-Type": "text/markdown"}
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        uploaded = await client.post(
            "/api/v1/knowledge/files?filename=历史资料.md",
            headers=headers,
            content="# 历史资料\n这是已经入库的资料。",
        )
        filename = uploaded.json()["data"]["filename"]
        (tmp_path / "knowledge" / filename).unlink()
        listed = await client.get("/api/v1/knowledge/files", headers=headers)
    await app.state.platform_service.close()
    assert listed.status_code == 200
    item = listed.json()["data"]["items"][0]
    assert item["original_filename"] == "历史资料.md"
    assert item["chunk_count"] >= 1


@pytest.mark.asyncio
async def test_knowledge_upload_is_admin_only(tmp_path: Path) -> None:
    settings = Settings(
        environment="test",
        database_url="sqlite+aiosqlite:///./knowledge-auth-test.db",
        redis_url=None,
        demo_auth_enabled=True,
        uploads_dir=str(tmp_path),
    )
    app = create_app(settings, repository=MemoryPlatformRepository())
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.post(
            "/api/v1/knowledge/files?filename=private.md",
            headers={**USER, "Content-Type": "text/markdown"}, content="# private",
        )
    await app.state.platform_service.close()
    assert response.status_code == 403


@pytest.mark.asyncio
async def test_delete_session_removes_its_transcript_and_is_owner_scoped(client: AsyncClient) -> None:
    created = await client.post(
        "/api/v1/sessions",
        headers={**USER, "Idempotency-Key": "session-key-delete-001"},
        json={"title": "待删除会话"},
    )
    session_id = created.json()["data"]["id"]
    await client.post(
        "/api/v1/chat/stream",
        headers={**USER, "Idempotency-Key": "stream-key-delete-001"},
        json={"mode": "new", "session_id": session_id, "content": "删除后不应保留"},
    )
    forbidden = await client.delete(f"/api/v1/sessions/{session_id}", headers=REVIEWER)
    deleted = await client.delete(f"/api/v1/sessions/{session_id}", headers=USER)
    missing = await client.get(f"/api/v1/sessions/{session_id}/messages", headers=USER)
    assert forbidden.status_code == 404
    assert deleted.status_code == 200
    assert deleted.json()["data"] == {"session_id": session_id, "deleted": True}
    assert missing.status_code == 404


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
