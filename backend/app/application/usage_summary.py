from __future__ import annotations

import asyncio
import logging
from collections import Counter
from datetime import UTC, date, datetime, timedelta
from typing import Any

from backend.app.domain.records import SummaryUpdateJobRecord, UserSummarySnapshotRecord
from backend.app.repositories.ports import PlatformRepository

logger = logging.getLogger(__name__)

_TOPIC_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("选购推荐", ("推荐", "选购", "买", "型号", "预算")),
    ("清洁与维护", ("清洁", "维护", "耗材", "拖布", "尘盒", "滤网")),
    ("故障排查", ("故障", "报错", "无法", "失败", "异常")),
    ("产品功能", ("导航", "避障", "吸力", "静音", "3d")),
)


def empty_usage_summary() -> dict[str, Any]:
    return {
        "conversation_overview": {
            "session_count": 0,
            "message_count": 0,
            "active_days": 0,
            "top_topics": [],
        },
        "facts": [],
        "preferences": [],
        "product_activity": {
            "recommended_models": [],
            "detail_view_count": 0,
            "three_d_view_count": 0,
        },
        "device_usage": {
            "status": "unavailable",
            "reason": "no_authorized_external_data",
        },
    }


class UserUsageAggregator:
    """Builds a user-scoped, traceable summary from persisted platform data."""

    async def build(
        self, repository: PlatformRepository, job: SummaryUpdateJobRecord
    ) -> tuple[dict[str, Any], str, datetime | None]:
        async with repository.transaction() as tx:
            sessions = await tx.list_sessions(job.owner_id, limit=10_000, after=None)
            messages = []
            for session in sessions:
                messages.extend(
                    await tx.list_messages(job.owner_id, session.id, limit=10_000, after=None)
                )
            memories = await tx.list_memories(
                job.owner_id, status="active", memory_type=None, limit=100, after=None
            )
            events = await tx.list_usage_events(job.owner_id, limit=10_000)
            external_user_id = await tx.resolve_external_user_id(job.owner_id)

        user_messages = [message for message in messages if message.role == "user"]
        topic_counts: Counter[str] = Counter()
        for message in user_messages:
            compact = message.content.casefold()
            for topic, keywords in _TOPIC_RULES:
                if any(keyword.casefold() in compact for keyword in keywords):
                    topic_counts[topic] += 1
                    break
        recommendations: Counter[tuple[str | None, str]] = Counter(
            (event.product_id, event.model_code)
            for event in events
            if event.event_type == "product_recommended" and event.model_code
        )
        active_dates = {
            message.created_at.date()
            for message in messages
            if message.created_at is not None
        }
        facts = [
            {
                "content": memory.content,
                "confidence": memory.confidence,
                "source_message_id": str(memory.source_message_id),
            }
            for memory in memories
            if memory.memory_type == "user_fact"
        ]
        preferences = [
            {
                "content": memory.content,
                "confidence": memory.confidence,
                "source_message_id": str(memory.source_message_id),
            }
            for memory in memories
            if memory.memory_type == "preference"
        ]
        data = empty_usage_summary()
        data["conversation_overview"] = {
            "session_count": len(sessions),
            "message_count": len(messages),
            "active_days": len(active_dates),
            "top_topics": [
                {"name": name, "count": count}
                for name, count in topic_counts.most_common(10)
            ],
        }
        data["facts"] = facts[:50]
        data["preferences"] = preferences[:50]
        data["product_activity"] = {
            "recommended_models": [
                {
                    "product_id": product_id,
                    "model_code": model_code,
                    "recommendation_count": count,
                }
                for (product_id, model_code), count in recommendations.most_common(20)
            ],
            "detail_view_count": sum(1 for event in events if event.event_type == "product_detail_viewed"),
            "three_d_view_count": sum(1 for event in events if event.event_type == "product_3d_viewed"),
        }
        if external_user_id:
            data["device_usage"] = {
                "status": "unavailable",
                "reason": "no_real_external_usage_data",
            }
        data_through_at = max(
            (
                timestamp
                for timestamp in (
                    *(message.created_at for message in messages),
                    *(event.occurred_at for event in events),
                )
                if timestamp is not None
            ),
            default=None,
        )
        return data, self._display(data), data_through_at

    @staticmethod
    def _display(data: dict[str, Any]) -> str:
        overview = data["conversation_overview"]
        lines = [
            "这是你的平台使用总结。",
            f"近期共进行了 {overview['session_count']} 个会话、{overview['message_count']} 条消息，活跃 {overview['active_days']} 天。",
        ]
        topics = overview["top_topics"]
        if topics:
            lines.append("常见咨询方向：" + "、".join(item["name"] for item in topics) + "。")
        if data["preferences"]:
            lines.append("已确认的偏好：" + "；".join(item["content"] for item in data["preferences"][:3]) + "。")
        products = data["product_activity"]["recommended_models"]
        if products:
            lines.append("曾向你推荐：" + "、".join(item["model_code"] for item in products[:3]) + "。")
        if data["device_usage"]["status"] == "unavailable":
            lines.append("当前暂无可用的设备使用数据。")
        return "\n\n".join(lines)


class UsageSummaryWorker:
    """Durable queue consumer; failed work stays in PostgreSQL for retry."""

    def __init__(self, repository: PlatformRepository, *, poll_seconds: float = 1.0) -> None:
        self._repository = repository
        self._aggregator = UserUsageAggregator()
        self._poll_seconds = poll_seconds
        self._task: asyncio.Task[None] | None = None
        self._stopped = asyncio.Event()
        self._last_cleanup_day: date | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._stopped.clear()
            self._task = asyncio.create_task(self._run(), name="usage-summary-worker")

    async def run_once(self) -> int:
        now = datetime.now(UTC)
        async with self._repository.transaction() as tx:
            jobs = await tx.claim_summary_update_jobs(now=now, limit=20)
        for job in jobs:
            await self._process(job)
        if self._last_cleanup_day != now.date():
            await self._cleanup(now)
            self._last_cleanup_day = now.date()
        return len(jobs)

    async def _process(self, job: SummaryUpdateJobRecord) -> None:
        now = datetime.now(UTC)
        try:
            summary, display, through_at = await self._aggregator.build(self._repository, job)
            async with self._repository.transaction() as tx:
                await tx.complete_summary_update_job(
                    job.id,
                    summary=summary,
                    display_summary=display,
                    data_through_at=through_at,
                    generator_version="usage-summary-v1",
                    now=now,
                )
        except Exception:
            logger.exception("usage summary update failed", extra={"job_id": str(job.id)})
            retry_at = now + timedelta(seconds=min(60, 2 ** job.attempts))
            async with self._repository.transaction() as tx:
                await tx.fail_summary_update_job(
                    job.id,
                    error_code="SUMMARY_UPDATE_FAILED",
                    error_summary="summary update failed",
                    retry_at=retry_at,
                    now=now,
                )

    async def _cleanup(self, now: datetime) -> None:
        try:
            async with self._repository.transaction() as tx:
                counts = await tx.purge_expired_usage_data(now=now)
            logger.info("usage data retention completed", extra={"cleanup_counts": counts})
        except Exception:
            logger.exception("usage data retention failed")

    async def _run(self) -> None:
        while not self._stopped.is_set():
            await self.run_once()
            try:
                await asyncio.wait_for(self._stopped.wait(), timeout=self._poll_seconds)
            except TimeoutError:
                pass

    async def close(self) -> None:
        self._stopped.set()
        if self._task is not None:
            await self._task


class UsageSummaryProvider:
    """Read-only adapter used by the user-facing summary tool."""

    def __init__(self, repository: PlatformRepository) -> None:
        self._repository = repository

    async def get(self, subject_id: str) -> UserSummarySnapshotRecord | None:
        async with self._repository.transaction() as tx:
            return await tx.get_latest_usage_summary(subject_id)
