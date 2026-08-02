from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID, uuid4

import pytest

from backend.app.adapters.llm.platform_executor import RuntimeRunExecutor
from backend.app.adapters.memory.repository import MemoryPlatformRepository
from backend.app.agent.contracts import (
    AgentRequest,
    AgentRunResult,
    ReviewReason,
    RunStatus,
    StepStatus,
    StepType,
    TraceStep,
)
from backend.app.agent.tooling import CancellationToken
from backend.app.application.ports import RunExecution
from backend.app.application.streaming import RunCoordinator
from backend.app.rag.models import Citation


class FixedRuntime:
    def __init__(self, result: AgentRunResult) -> None:
        self.result = result
        self.requests: list[AgentRequest] = []

    async def execute(
        self,
        request: AgentRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        if cancellation is not None:
            cancellation.checkpoint()
        self.requests.append(request)
        return self.result


async def _prepared_run() -> tuple[MemoryPlatformRepository, RunExecution]:
    repository = MemoryPlatformRepository()
    now = datetime.now(UTC)
    async with repository.transaction() as tx:
        session = await tx.create_session("subject-1", "integration", now)
        user, assistant, run = await tx.prepare_chat(
            owner_id="subject-1",
            session_id=session.id,
            content="How should I maintain the filter?",
            original_user_message_id=None,
            session_title=None,
            now=now,
        )
    return repository, RunExecution(
        request_id="request-1234",
        subject_id="subject-1",
        session_id=session.id,
        run_id=run.id,
        user_message_id=user.id,
        assistant_message_id=assistant.id,
        input_content=user.content,
    )


def _trace() -> tuple[TraceStep, ...]:
    now = datetime.now(UTC)
    return (
        TraceStep(
            sequence=1,
            step_type=StepType.RETRIEVAL,
            status=StepStatus.SUCCEEDED,
            started_at=now,
            ended_at=now,
            duration_ms=1,
            summary="retrieval completed",
        ),
    )


def _citation() -> Citation:
    document_id = str(uuid4())
    return Citation(
        citation_id=str(uuid4()),
        document_id=document_id,
        document_version="v1",
        chunk_id=f"{document_id}:v1:0001",
        title="Filter maintenance",
        source="kb://maintenance/filter",
        score=0.95,
        excerpt="Clean the filter regularly.",
    )


@pytest.mark.asyncio
async def test_runtime_result_is_streamed_and_persisted() -> None:
    repository, execution = await _prepared_run()
    runtime = FixedRuntime(
        AgentRunResult(
            run_id=str(execution.run_id),
            status=RunStatus.COMPLETED,
            public_content="Clean the filter regularly.",
            candidate_content=None,
            citations=(_citation(),),
            trace=_trace(),
            confidence=0.91,
            confidence_threshold=0.65,
            model_name="integration-model",
            retrieval_strategy="hybrid-rerank",
        )
    )
    coordinator = RunCoordinator(repository, RuntimeRunExecutor(runtime))

    await coordinator._execute(execution)

    run = repository.runs[execution.run_id]
    message = repository.messages[execution.assistant_message_id]
    events = repository.events[execution.run_id]
    assert run.status == "completed"
    assert run.model == "integration-model"
    assert run.retrieval_strategy == "hybrid-rerank"
    assert run.steps[0]["step_type"] == "retrieval"
    assert run.citations[0]["document_version"] == "v1"
    assert message.content == "Clean the filter regularly."
    assert message.citations == run.citations
    assert events[-1].event["event_type"] == "done"
    assert runtime.requests[0].run_id == str(execution.run_id)


@pytest.mark.asyncio
async def test_withheld_candidate_creates_review_without_publication() -> None:
    repository, execution = await _prepared_run()
    review_id = uuid4()
    runtime = FixedRuntime(
        AgentRunResult(
            run_id=str(execution.run_id),
            status=RunStatus.NEEDS_REVIEW,
            public_content="",
            candidate_content="Candidate visible only to reviewers.",
            citations=(_citation(),),
            trace=_trace(),
            confidence=0.42,
            confidence_threshold=0.65,
            review_reasons=(ReviewReason.LOW_CONFIDENCE,),
            model_name="integration-model",
            review_id=str(review_id),
        )
    )
    coordinator = RunCoordinator(repository, RuntimeRunExecutor(runtime))

    await coordinator._execute(execution)

    run = repository.runs[execution.run_id]
    message = repository.messages[execution.assistant_message_id]
    review = repository.reviews[UUID(str(review_id))]
    events = repository.events[execution.run_id]
    review_event = next(
        event.event for event in events if event.event["event_type"] == "review_required"
    )
    assert run.status == "needs_review"
    assert message.status == "needs_review"
    assert message.content == ""
    assert review.candidate_content == "Candidate visible only to reviewers."
    assert review.reason_codes == ["low_confidence"]
    assert review_event["payload"]["review_id"] == str(review_id)
