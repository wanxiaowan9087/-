from __future__ import annotations

from collections.abc import Mapping

from ...agent.contracts import AgentModelRequest, ModelDraft
from ...agent.tooling import CancellationToken


class FakeReActEngine:
    """Fixed model adapter with exact, reviewable fixtures."""

    def __init__(
        self,
        responses: Mapping[str, ModelDraft | Exception] | None = None,
        *,
        default_content: str = "请依据已提供资料进行处理。",
        model_name: str = "fixed-fake-react-v1",
    ) -> None:
        self._responses = dict(responses or {})
        self._default_content = default_content
        self.model_name = model_name
        self.requests: list[AgentModelRequest] = []

    async def generate(
        self,
        request: AgentModelRequest,
        cancellation: CancellationToken | None = None,
    ) -> ModelDraft:
        if cancellation is not None:
            cancellation.checkpoint()
        self.requests.append(request)
        fixture = self._responses.get(request.user_text)
        if isinstance(fixture, Exception):
            raise fixture
        if fixture is not None:
            return fixture
        return ModelDraft(
            content=self._default_content,
            model_name=self.model_name,
        )
