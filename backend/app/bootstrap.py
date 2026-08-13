from __future__ import annotations

from backend.app.adapters.llm.deterministic_executor import DeterministicRunExecutor
from backend.app.adapters.llm.langchain_react import LangChainReActEngine
from backend.app.adapters.llm.platform_executor import RuntimeRunExecutor
from backend.app.adapters.llm.query_rewriter import LangChainQueryRewriter
from backend.app.adapters.memory.platform_runtime import PlatformMemoryRuntime
from backend.app.adapters.vector.dashscope import DashScopeEmbeddingAdapter
from backend.app.adapters.vector.json_store import JsonVectorStore
from backend.app.agent.customer_tools import build_customer_tool_registry
from backend.app.agent.report_tools import ReportWorkflow
from backend.app.agent.runtime import AgentRuntime
from backend.app.agent.tooling import ToolExecutor
from backend.app.application.ports import RunExecutorPort, UnavailableRunExecutor
from backend.app.core.config import Settings
from backend.app.rag.chunking import DocumentChunker
from backend.app.rag.ingestion import KnowledgeIndexer
from backend.app.rag.lexical import BM25KeywordIndex
from backend.app.rag.local_corpus import LocalTextCorpusRetriever
from backend.app.rag.models import DocumentRecord, DocumentType
from uuid import NAMESPACE_URL, uuid5
from backend.app.adapters.mcp.robot_catalog import recommend_robots
from backend.app.rag.retrieval import (
    HybridRetriever,
    IdentityReranker,
    MergedRetriever,
    MultiQueryRetriever,
)
from backend.app.repositories.ports import PlatformRepository


class AgentRuntimeBootstrapError(RuntimeError):
    """Configuration or optional dependency failure during live runtime wiring."""


def build_run_executor(
    settings: Settings, repository: PlatformRepository | None = None
) -> RunExecutorPort:
    """Build the real LangChain/file-vector-store/DashScope runtime when enabled.

    Keeping this opt-in makes local API and contract work deterministic. A live
    deployment must set APP_AGENT_RUNTIME_ENABLED=true and supply the
    DashScope credential through the provider's standard environment variable.
    """

    if settings.test_executor_enabled:
        return DeterministicRunExecutor()
    if not settings.agent_runtime_enabled:
        return UnavailableRunExecutor()
    try:
        from langchain_community.chat_models import ChatTongyi
        from langchain_community.embeddings import DashScopeEmbeddings
    except ImportError as error:
        raise AgentRuntimeBootstrapError(
            "AI runtime dependencies are missing; install the project's [ai] extra"
        ) from error

    try:
        embeddings = DashScopeEmbeddingAdapter(
            DashScopeEmbeddings(model=settings.agent_embedding_model_name)
        )
        vector_store = JsonVectorStore(settings.agent_vector_store_path)
        indexed_chunks = vector_store.load_all_chunks()
        local_corpus = LocalTextCorpusRetriever(
            settings.agent_local_corpus_dir, include_uploaded_files=False
        )
        model = ChatTongyi(model=settings.agent_model_name)
        keyword_index = BM25KeywordIndex(indexed_chunks)
        hybrid = HybridRetriever(
            embeddings,
            vector_store,
            keyword_index,
            IdentityReranker(),
        )
        # Keep the hybrid stack live even when the process starts with an empty
        # vector store. Admin uploads mutate these shared indexes at runtime;
        # using only the local fallback here would silently bypass rewrite/RRF.
        retriever = MergedRetriever(
            MultiQueryRetriever(hybrid, LangChainQueryRewriter(model)),
            local_corpus,
        )
        memory = PlatformMemoryRuntime(repository) if repository is not None else None
        react_engine = LangChainReActEngine(
            model=model,
            tool_executor=ToolExecutor(build_customer_tool_registry(memory)),
            chat_system_prompt=settings.agent_chat_system_prompt,
            report_system_prompt=settings.agent_report_system_prompt,
            model_name=settings.agent_model_name,
        )
    except Exception as error:
        raise AgentRuntimeBootstrapError(
            "AI runtime configuration could not be initialized"
        ) from error
    executor = RuntimeRunExecutor(
        AgentRuntime(
            react_engine=react_engine,
            retriever=retriever,
            memory=memory,
        ),
        report_workflow=ReportWorkflow(
            settings.agent_external_records_path,
            external_user_id_resolver=(repository_adapter_resolver(repository) if repository is not None else None),
        ),
    )
    executor.knowledge_indexer = KnowledgeIndexer(
        DocumentChunker(), embeddings, vector_store, keyword_index
    )
    catalog_path = settings.agent_local_corpus_dir + "/catalog/zenmop_robot_catalog.md"
    try:
        with open(catalog_path, encoding="utf-8") as catalog_file:
            executor.robot_catalog_document = DocumentRecord(
                document_id=str(uuid5(NAMESPACE_URL, "zenmop-robot-catalog")),
                title="ZENMOP 机器人型号目录",
                source="file://data/catalog/zenmop_robot_catalog.md",
                document_type=DocumentType.MARKDOWN,
                content=catalog_file.read(),
                version="catalog-v1",
            )
    except OSError:
        executor.robot_catalog_document = None
    executor.robot_catalog_recommend = recommend_robots
    return executor


def repository_adapter_resolver(repository: PlatformRepository):
    async def resolve(subject_id: str) -> str | None:
        async with repository.transaction() as tx:
            return await tx.resolve_external_user_id(subject_id)
    return resolve
