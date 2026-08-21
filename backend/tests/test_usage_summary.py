from __future__ import annotations

from datetime import UTC, datetime, timedelta
import pytest

from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.application.usage_summary import UsageSummaryWorker
from backend.app.domain.records import UserSummarySnapshotRecord


@pytest.mark.asyncio
async def test_completed_chat_creates_user_scoped_summary_and_recommendation() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        session = await tx.create_session("user-a", "选购咨询", now)
        user, assistant, run = await tx.prepare_chat(
            owner_id="user-a",
            session_id=session.id,
            content="我偏好安静，推荐适合木地板的机器人",
            original_user_message_id=None,
            session_title=None,
            now=now,
        )
        await tx.update_run(
            run.id,
            expected_statuses={"queued"},
            status="completed",
            now=now,
            assistant_content="推荐 M6 Mini",
        )
        await tx.create_memory(
            "user-a",
            memory_type="preference",
            content="我偏好安静",
            confidence=0.9,
            source_message_id=user.id,
            now=now,
        )
        await tx.record_product_recommendations(
            owner_id="user-a",
            session_id=session.id,
            message_id=assistant.id,
            recommendations=[{"product_id": "m6-mini", "model": "M6 Mini"}],
            now=now,
        )
        await tx.enqueue_summary_update(
            owner_id="user-a",
            session_id=session.id,
            trigger_message_id=assistant.id,
            now=now,
        )

    worker = UsageSummaryWorker(repository, poll_seconds=0.01)
    assert await worker.run_once() == 1
    async with repository.transaction() as tx:
        snapshot = await tx.get_latest_usage_summary("user-a")
        other = await tx.get_latest_usage_summary("user-b")
    assert snapshot is not None
    assert other is None
    assert snapshot.summary["product_activity"]["recommended_models"][0]["model_code"] == "M6 Mini"
    assert snapshot.summary["preferences"][0]["content"].startswith("我偏好安静")


@pytest.mark.asyncio
async def test_duplicate_trigger_message_is_idempotent_and_summary_empty_is_explicit() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        session = await tx.create_session("user-a", "空会话", now)
        _, assistant, _ = await tx.prepare_chat(
            owner_id="user-a",
            session_id=session.id,
            content="你好",
            original_user_message_id=None,
            session_title=None,
            now=now,
        )
        first = await tx.enqueue_summary_update(
            owner_id="user-a", session_id=session.id, trigger_message_id=assistant.id, now=now
        )
        second = await tx.enqueue_summary_update(
            owner_id="user-a", session_id=session.id, trigger_message_id=assistant.id, now=now
        )
    assert first.id == second.id
    worker = UsageSummaryWorker(repository, poll_seconds=0.01)
    await worker.run_once()
    async with repository.transaction() as tx:
        snapshot = await tx.get_latest_usage_summary("user-a")
    assert snapshot is not None
    assert snapshot.summary["conversation_overview"]["session_count"] == 1


@pytest.mark.asyncio
async def test_retention_keeps_recent_snapshot_and_removes_old_events() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        event = await tx.record_usage_event(
            owner_id="user-a",
            event_type="product_detail_viewed",
            product_id="m6-mini",
            model_code="M6 Mini",
            now=now - timedelta(days=61),
        )
        await tx.purge_expired_usage_data(now=now)
        protected_events = await tx.list_usage_events("user-a", limit=20)
        assert [item.id for item in protected_events] == [event.id]

        repository.summary_snapshots[event.id] = UserSummarySnapshotRecord(
            event.id,
            "user-a",
            1,
            "active",
            {},
            "已覆盖的测试快照",
            now,
            now,
            "test",
        )
        await tx.purge_expired_usage_data(now=now)
        events = await tx.list_usage_events("user-a", limit=20)
    assert events == []


@pytest.mark.asyncio
async def test_retention_never_removes_old_messages_without_active_snapshot_coverage() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    created_at = now - timedelta(days=91)
    async with repository.transaction() as tx:
        session = await tx.create_session("user-a", "保留边界", created_at)
        user, assistant, _ = await tx.prepare_chat(
            owner_id="user-a",
            session_id=session.id,
            content="这条历史消息还没有被总结快照覆盖",
            original_user_message_id=None,
            session_title=None,
            now=created_at,
        )
        deleted = await tx.purge_expired_usage_data(now=now)
        messages = await tx.list_messages("user-a", session.id, limit=20, after=None)

    assert deleted["messages"] == 0
    assert {message.id for message in messages} == {user.id, assistant.id}
