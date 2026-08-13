from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from backend.app.adapters.memory.platform_runtime import PlatformMemoryRuntime
from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.agent.contracts import ConversationMessage
from backend.app.domain.records import MessageRecord


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
            session_title=None,
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


@pytest.mark.asyncio
async def test_platform_memory_runtime_persists_older_turn_summary_and_keeps_last_eight() -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        session = await tx.create_session("subject-1", "memory", now)
        for index in range(12):
            message_id = uuid4()
            created_at = now + timedelta(seconds=index)
            repository.messages[message_id] = MessageRecord(
                message_id,
                session.id,
                "subject-1",
                "user" if index % 2 == 0 else "assistant",
                "completed",
                f"turn {index}",
                None,
                None,
                [],
                created_at,
                created_at,
            )

    first_runtime = PlatformMemoryRuntime(repository)
    first_context = await first_runtime.build_context(str(session.id), "subject-1")
    assert [message.content for message in first_context.window] == [
        f"turn {index}" for index in range(4, 12)
    ]
    assert first_context.summary is not None
    assert "turn 0" in first_context.summary
    assert "turn 3" in first_context.summary
    assert "turn 4" not in first_context.summary

    second_runtime = PlatformMemoryRuntime(repository)
    restored_context = await second_runtime.build_context(str(session.id), "subject-1")
    assert restored_context.summary == first_context.summary

    next_message_id = uuid4()
    next_created_at = now + timedelta(seconds=12)
    repository.messages[next_message_id] = MessageRecord(
        next_message_id,
        session.id,
        "subject-1",
        "user",
        "completed",
        "turn 12",
        None,
        None,
        [],
        next_created_at,
        next_created_at,
    )
    advanced_context = await second_runtime.build_context(str(session.id), "subject-1")
    assert [message.content for message in advanced_context.window] == [
        f"turn {index}" for index in range(5, 13)
    ]
    assert advanced_context.summary is not None
    assert "turn 4" in advanced_context.summary


@pytest.mark.asyncio
async def test_platform_memory_runtime_upserts_preferences_and_ignores_sensitive_facts(
) -> None:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        session = await tx.create_session("subject-1", "memory", now)
        messages = []
        for index, content in enumerate(
            (
                "I prefer concise answers.",
                "  i PREFER   concise answers. ",
                "My device is Model-A.",
                "My password is never-store-this.",
            )
        ):
            message_id = uuid4()
            created_at = now + timedelta(seconds=index)
            message = MessageRecord(
                message_id,
                session.id,
                "subject-1",
                "user",
                "completed",
                content,
                None,
                None,
                [],
                created_at,
                created_at,
            )
            repository.messages[message_id] = message
            messages.append(message)

    runtime = PlatformMemoryRuntime(repository)
    for message in messages:
        await runtime.extract_best_effort(
            "subject-1",
            ConversationMessage(
                str(message.id), message.role, message.content, message.created_at
            ),
        )
    context = await runtime.build_context(str(session.id), "subject-1")
    assert [(fact.memory_type.value, fact.content) for fact in context.facts] == [
        ("user_fact", "My device is Model-A."),
        ("preference", "I prefer concise answers."),
    ]
