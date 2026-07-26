from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.adapters.memory.repository import MemoryPlatformRepository


@pytest.mark.asyncio
async def test_internal_memory_and_review_creation_ports() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as transaction:
        session = await transaction.create_session("alice", "Internal ports", now)
        user, _, run = await transaction.prepare_chat(
            owner_id="alice",
            session_id=session.id,
            content="remember this",
            original_user_message_id=None,
            now=now,
        )
        memory = await transaction.create_memory(
            "alice",
            memory_type="user_fact",
            content="prefers concise answers",
            confidence=0.9,
            source_message_id=user.id,
            now=now,
        )
        review = await transaction.create_review(
            run_id=run.id,
            session_id=session.id,
            owner_id="alice",
            user_message_id=user.id,
            candidate_content="withheld",
            reason_codes=["low_confidence"],
            confidence=0.4,
            now=now,
        )
    assert memory.version == 1
    assert review.status == "pending"
