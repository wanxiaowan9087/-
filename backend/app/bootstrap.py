from __future__ import annotations

from typing import Any

from backend.app.adapters.llm.langchain_react import LangChainReActEngine
from backend.app.adapters.llm.platform_executor import RuntimeRunExecutor
from backend.app.adapters.memory.platform_runtime import PlatformMemoryRuntime
from backend.app.adapters.vector.chroma import ChromaVectorStore
from backend.app.adapters.vector.dashscope import DashScopeEmbeddingAdapter
from backend.app.agent.runtime import AgentRuntime
from backend.app.agent.tooling import ToolExecutor, ToolRegistry
from backend.app.application.ports import RunExecutorPort, UnavailableRunExecutor
from backend.app.core.config import Settings
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.retrieval import HybridRetriever, IdentityReranker
from backend.app.repositories.ports import PlatformRepository


class AgentRuntimeBootstrapError(RuntimeError):
    """Configuration or optional dependency failure during live runtime wiring."""


def build_run_executor(
    settings: Settings, repository: PlatformRepository | None = None
) -> RunExecutorPort:
    """Build the real LangChain/Chroma/DashScope runtime only when enabled.

    Keeping this opt-in makes local API and contract work deterministic. A live
    deployment must set APP_AGENT_RUNTIME_ENABLED=true and supply the
    DashScope credential through the provider's standard environment variable.
    """

    if not settings.agent_runtime_enabled:
        return UnavailableRunExecutor()
    try:
        import chromadb
        from langchain_community.chat_models import ChatTongyi
        from langchain_community.embeddings import DashScopeEmbeddings
    except ImportError as error:
        raise AgentRuntimeBootstrapError(
            "AI runtime dependencies are missing; install the project's [ai] extra"
        ) from error

    try:
        client: Any = chromadb.PersistentClient(path=settings.agent_vector_store_path)
        collection = client.get_or_create_collection(
            name=settings.agent_vector_collection_name,
            metadata={"hnsw:space": "cosine"},
        )
        embeddings = DashScopeEmbeddingAdapter(
            DashScopeEmbeddings(model=settings.agent_embedding_model_name)
        )
        retriever = HybridRetriever(
            embeddings,
            ChromaVectorStore(collection),
            BM25KeywordIndex(),
            IdentityReranker(),
        )
        react_engine = LangChainReActEngine(
            model=ChatTongyi(model=settings.agent_model_name),
            tool_executor=ToolExecutor(ToolRegistry()),
            chat_system_prompt=settings.agent_chat_system_prompt,
            report_system_prompt=settings.agent_report_system_prompt,
            model_name=settings.agent_model_name,
        )
    except Exception as error:
        raise AgentRuntimeBootstrapError(
            "AI runtime configuration could not be initialized"
        ) from error
    memory = PlatformMemoryRuntime(repository) if repository is not None else None
    return RuntimeRunExecutor(
        AgentRuntime(
            react_engine=react_engine,
            retriever=retriever,
            memory=memory,
        )
    )
