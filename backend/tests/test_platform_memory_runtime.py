from __future__ import annotations

from datetime import UTC, datetime

import pytest

from backend.app.adapters.memory.platform_runtime import PlatformMemoryRuntime
from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.agent.contracts import ConversationMessage


@pytest.mark.asyncio
async def test_platform_memory_runtime_reads_history_and_persists_explicit_preference() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        session = await tx.create_session("subject-1", "memory", now)
        user, _, _ = await tx.prepare_chat(
            owner_id="subject-1",
            session_id=session.id,
            content="I prefer concise answers.",
            original_user_message_id=None,
            now=now,
        )
    runtime = PlatformMemoryRuntime(repository)

    await runtime.extract_best_effort(
        "subject-1",
        ConversationMessage(str(user.id), "user", user.content, now),
    )
    context = await runtime.build_context(str(session.id), "subject-1")

    assert any(
        message.content == "I prefer concise answers."
        for message in context.window
    )
    assert len(context.facts) == 1
    assert context.facts[0].memory_type.value == "preference"
    assert context.facts[0].source_message_id == str(user.id)
