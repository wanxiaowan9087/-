from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Protocol
from uuid import NAMESPACE_URL, uuid4, uuid5
from zoneinfo import ZoneInfo

from ..rag.citations import CitationService
from ..rag.models import Chunk, Citation, DocumentType, RetrievalResult, SearchHit
from ..rag.ports import RetrieverPort
from ..rag.retrieval import RetrievalUnavailable
from ..rag.security import (
    render_untrusted_context,
    scan_retrieved_content,
)
from .contracts import (
    AgentModelRequest,
    AgentRequest,
    AgentRunResult,
    CatalogProduct,
    ConversationMessage,
    ConversationMode,
    ErrorCode,
    MemoryContext,
    ModelDraft,
    RunStatus,
    StepStatus,
    StepType,
    ToolOutcome,
    utc_now,
)
from .customer_tools import (
    get_user_usage_summary,
    reset_request_context,
    set_request_context,
)
from .memory import NullMemoryCoordinator
from .ports import ModelTimeout, ModelUnavailable, ReActEnginePort
from .route_graph import EvidencePolisher, route_memory_then_knowledge
from .safety import (
    DeterministicReviewPolicy,
    DraftGate,
    PolicyAction,
    PolicyDecision,
    PolicyInput,
    PromptInjectionDetector,
    classify_user_risk,
    required_fields_missing,
)
from .tooling import CancellationToken
from .tracing import RunStateMachine, TraceRecorder

logger = logging.getLogger(__name__)

_LABELED_LOCAL_PATH = re.compile(
    r"(?:文件路径|本地路径|file path|source path)\s*[:：]\s*`?file://[^\s`)\]）]+`?",
    flags=re.IGNORECASE,
)
_RAW_LOCAL_PATH = re.compile(r"`?file://[^\s`)\]）]+`?", flags=re.IGNORECASE)
_DOCUMENT_ID = re.compile(
    r"(?:文档\s*ID|document\s*ID)\s*[:：]\s*`?[0-9a-f-]{8,}`?",
    flags=re.IGNORECASE,
)
_DOCUMENT_VERSION = re.compile(
    r"(?:文档\s*)?(?:版本|version)\s*[:：]\s*`?[A-Za-z0-9._-]+`?",
    flags=re.IGNORECASE,
)
_INTERNAL_MODEL_REFERENCE = re.compile(
    r"(?i)(?:通义千问|千问|qwen|langgraph|react(?:\s*engine)?|agent(?:\s*runtime|\s*engine)?)"
)


def redact_local_source_paths(content: str) -> str:
    """Keep citations traceable internally without exposing local paths in model text."""
    redacted = _LABELED_LOCAL_PATH.sub("", content)
    redacted = _RAW_LOCAL_PATH.sub("受控知识库资料", redacted)
    redacted = _DOCUMENT_ID.sub("", redacted)
    redacted = _DOCUMENT_VERSION.sub("", redacted)
    redacted = re.sub(r"[（(]\s*[，,;；\s]*[）)]", "", redacted)
    # Collapse horizontal spacing only. Newlines are presentation semantics.
    return re.sub(r"[ \t]{2,}", " ", redacted).strip()


def redact_internal_model_references(content: str) -> str:
    """Prevent provider/framework names from leaking into public replies."""
    return _INTERNAL_MODEL_REFERENCE.sub("小智", content)


def _renumber_ordered_lists(content: str) -> str:
    """Normalize repeated model-generated ordered-list markers.

    Models occasionally emit ``1.`` for every item.  Renumber only markers at
    the beginning of a line (optionally indented), including items separated
    by wrapped explanatory prose. Inline numbers and dates are left untouched.
    """
    lines = content.splitlines()
    markers = [
        re.match(r"^\s*(\d+)[.、)]\s*.+?\s*$", line)
        for line in lines
    ]
    # A common provider failure is to emit ``1.`` for every top-level item.
    # Number all such markers in one answer, even when an item has wrapped
    # explanatory lines between it and the next marker.
    repeated_one_markers = (
        sum(bool(match and match.group(1) == "1") for match in markers) >= 2
        and all(not match or match.group(1) == "1" for match in markers)
    )
    if not repeated_one_markers:
        return content

    output: list[str] = []
    next_number = 1
    for line, match in zip(lines, markers, strict=True):
        if match:
            prefix = re.match(r"^(\s*)", line).group(1)
            body = re.sub(r"^\s*\d+[.、)]\s*", "", line).strip()
            output.append(f"{prefix}{next_number}. {body}")
            next_number += 1
        else:
            output.append(line)
    return "\n".join(output)


def format_user_visible_answer(content: str) -> str:
    """Normalize compact model lists so each recommendation remains scannable."""
    # Strip emphasis first because providers often wrap the whole marker in
    # Markdown (``**1. item**``), which otherwise hides it from the list parser.
    content = re.sub(r"\*\*(.+?)\*\*", r"\1", content)
    content = _renumber_ordered_lists(content)
    formatted = re.sub(
        r"[ \t]+-[ \t]+(?=(?:\*\*)?[A-Z][A-Z0-9-]{1,})",
        "\n\n- ",
        content,
    )
    formatted = re.sub(r"(?<=[。！？!?])(?=[^\n])", "\n\n", formatted)
    formatted = re.sub(r"\n{3,}", "\n\n", formatted)
    return re.sub(r"[ \t]{2,}", " ", formatted).strip()


def is_catalog_inventory_intent(text: str) -> bool:
    """Recognize exact catalog count/list questions before generic RAG."""
    compact = re.sub(r"\s+", "", text).casefold()
    inventory_markers = (
        "有多少",
        "多少款",
        "多少个",
        "几款",
        "有哪些",
        "全部型号",
        "型号列表",
        "产品清单",
        "产品目录",
        "全系产品",
    )
    if not any(marker in compact for marker in inventory_markers):
        return False
    # “产品” is sufficient in this product-only assistant; explicit robot
    # terms are accepted as well for callers that phrase the question narrowly.
    return "产品" in compact or any(
        term in compact for term in ("机器人", "扫地机", "扫拖", "型号", "机型")
    )


def format_catalog_inventory(products: Sequence[CatalogProduct]) -> str:
    """Render the complete curated catalog without relying on top-k retrieval."""
    unique: dict[str, CatalogProduct] = {}
    for product in products:
        if product.product_id and product.product_id not in unique:
            unique[product.product_id] = product
    items = tuple(unique.values())
    if not items:
        return "当前没有可用的产品目录资料。"
    lines = [f"目前目录中共有 {len(items)} 款扫地机器人："]
    for index, product in enumerate(items, 1):
        price = f"，参考价 {product.price} 元" if product.price > 0 else ""
        lines.append(f"{index}. {product.name}（{product.model}）{price}")
    lines.append("如果你想知道哪一款适合你的家庭，请告诉我房屋面积、地面材质和预算。")
    return "\n".join(lines)


def _humanize_recalled_message(content: str) -> str:
    """Remove internal speaker labels before quoting an older turn."""
    cleaned = redact_internal_model_references(redact_local_source_paths(content))
    cleaned = re.sub(
        r"(?im)^\s*(?:你|小智|assistant|assistant_message|agent)\s*[:：]\s*",
        "",
        cleaned,
    )
    return format_user_visible_answer(cleaned)[:2000]


IDENTITY_INTENT_PATTERNS = (
    "你是谁",
    "你能做什么",
    "你可以做什么",
    "你会什么",
    "你会做什么",
    "你会干什么",
    "你都会什么",
    "你会哪些",
    "你能干嘛",
    "你能做啥",
    "你能帮我什么",
    "你能帮我做什么",
    "你能提供哪些帮助",
    "你的功能",
    "介绍一下你",
    "怎么上传知识",
    "如何上传知识",
    "上传新文件",
    "上传资料",
    "知识库怎么更新",
)

MODEL_IDENTITY_PATTERNS = (
    "\u4ec0\u4e48\u6a21\u578b",
    "\u4ec0\u4e48\u5927\u6a21\u578b",
    "\u4f60\u662f\u6a21\u578b\u5417",
    "\u4f60\u662f\u4e0d\u662f\u5c0f\u667a",
    "\u4f60\u662f\u5c0f\u667a\u5417",
    "qwen",
    "\u901a\u4e49",
    "\u5343\u95ee",
    "\u5e95\u5c42\u6a21\u578b",
    "\u6a21\u578b\u63d0\u4f9b\u5546",
)

PROFILE_INTENT_PATTERNS = (
    "我的个人信息",
    "我的资料",
    "用户信息",
    "我的使用习惯",
    "我的偏好",
    "我叫什么",
    "我的名字",
    "我是谁",
    "用户是谁",
    "我的型号",
    "我使用的型号",
    "我用的型号",
    "我的设备",
)
USER_NAME_INTENT_PATTERNS = ("我叫什么", "我的名字", "我是谁", "用户是谁")
USER_NAME_FACT_MARKERS = ("名字", "姓名", "昵称", "称呼", "叫我", "我叫")
RECENT_HISTORY_INTENT_PATTERNS = (
    "刚才说了什么",
    "刚才说什么",
    "刚才我们说了什么",
    "刚才我们说什么",
    "刚才聊了什么",
    "刚才我们聊了什么",
    "刚才问了什么",
    "上一条说了什么",
    "上一句说了什么",
    "回顾刚才",
    "总结刚才",
)
USAGE_SUMMARY_INTENT_PATTERNS = (
    "总结我的使用情况",
    "总结我的使用习惯",
    "我的使用总结",
    "平台使用总结",
    "使用报告",
)

_CHINA_TIME_ZONE = ZoneInfo("Asia/Shanghai")
_WEEKDAY_NAMES = ("星期一", "星期二", "星期三", "星期四", "星期五", "星期六", "星期日")


def answer_calendar_intent(user_text: str, *, now: datetime | None = None) -> str | None:
    """Answer explicit current-date questions without sending them to the RAG policy."""
    compact = re.sub(r"\s+", "", user_text)
    asks_today = any(marker in compact for marker in ("今天", "今日", "现在", "当前"))
    asks_calendar = any(
        marker in compact
        for marker in (
            "星期几",
            "周几",
            "礼拜几",
            "几号",
            "日期",
            "几月几日",
        )
    )
    if not asks_today or not asks_calendar:
        return None
    current = (now or datetime.now(UTC)).astimezone(_CHINA_TIME_ZONE)
    return (
        f"今天是 {current.year} 年 {current.month} 月 {current.day} 日，"
        f"{_WEEKDAY_NAMES[current.weekday()]}。"
    )


def classify_meaningless_input(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).casefold()
    if not compact:
        return True
    # Unicode \w is not reliable across clients with mixed encodings. Treat
    # input as meaningful when it contains a letter, CJK character, or digit;
    # punctuation-only and numeric-only messages are otherwise ignored.
    if not re.search(r"[A-Za-z\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]", compact):
        return True
    return compact in {"嗯", "嗯嗯", "好的", "好", "ok", "okay", "收到", "谢谢"}


def answer_profile_intent(user_text: str, context: MemoryContext) -> str | None:
    compact = re.sub(r"\s+", "", user_text)
    if not any(pattern in compact for pattern in PROFILE_INTENT_PATTERNS):
        return None
    facts = [fact.content for fact in context.facts]
    if any(pattern in compact for pattern in USER_NAME_INTENT_PATTERNS):
        identity_facts = [
            fact for fact in facts if any(marker in fact for marker in USER_NAME_FACT_MARKERS)
        ]
        if not identity_facts:
            return "我目前没有足够的个人资料来确认你的称呼。"
        return "记得，你的名字是" + "、".join(
            re.sub(r"^(?:我的)?(?:名字|姓名|昵称|称呼)是", "", fact).strip()
            for fact in identity_facts
        ) + "。"
    preferences = [fact for fact in facts if "偏好" in fact or "喜欢" in fact or "请用" in fact]
    lines = [f"会话摘要：{context.summary}" if context.summary else "会话摘要：暂无已生成摘要。"]
    lines.append("我记得的相关信息：" + ("；".join(facts) if facts else "暂时没有"))
    lines.append("你的使用偏好：" + ("；".join(preferences) if preferences else "暂时没有稳定记录"))
    return "\n".join(lines)


def answer_recent_history_intent(user_text: str, context: MemoryContext) -> str | None:
    """Answer explicit conversation-recall questions from the scoped message window.

    ``prepare_chat`` persists the current user message before the runtime builds
    context, so the last matching user message must be removed first.  Without
    this rule, "我刚才说了什么" would simply echo itself instead of recalling
    the preceding turn.
    """
    compact = re.sub(r"\s+", "", user_text)
    if not any(pattern in compact for pattern in RECENT_HISTORY_INTENT_PATTERNS):
        return None
    messages = list(context.window)
    if (
        messages
        and messages[-1].role == "user"
        and re.sub(r"\s+", "", messages[-1].content) == compact
    ):
        messages.pop()
    messages = [message for message in messages if message.content.strip()]
    if not messages:
        if context.summary:
            return f"当前短期窗口里没有更早的消息。较早的会话摘要是：{context.summary}"
        return "当前会话里还没有可以回顾的上一条内容。"

    if "我刚才" in compact or "我上一" in compact:
        previous = next((item for item in reversed(messages) if item.role == "user"), None)
        if previous is None:
            return "当前会话里还没有可以回顾的上一条用户消息。"
        return f"你刚才说的是：“{_humanize_recalled_message(previous.content)}”"
    if "你刚才" in compact or "你上一" in compact:
        previous = next((item for item in reversed(messages) if item.role == "assistant"), None)
        if previous is None:
            return "当前会话里还没有可以回顾的小智上一条回答。"
        return f"小智刚才回答的是：“{_humanize_recalled_message(previous.content)}”"

    recent = messages[-4:]
    user_turn = next((item for item in reversed(recent) if item.role == "user"), None)
    assistant_turn = next((item for item in reversed(recent) if item.role == "assistant"), None)
    if user_turn and assistant_turn:
        return (
            f"刚才你提到：“{_humanize_recalled_message(user_turn.content)}”。"
            f"小智当时回复：“{_humanize_recalled_message(assistant_turn.content)}”"
        )
    if user_turn:
        return f"刚才你提到：“{_humanize_recalled_message(user_turn.content)}”"
    if assistant_turn:
        return f"小智刚才回复：“{_humanize_recalled_message(assistant_turn.content)}”"
    return "当前会话里还没有可以回顾的内容。"


def answer_memory_intent(user_text: str, context: MemoryContext) -> str | None:
    """Single memory-routing entry: short-term recall, then durable user facts."""
    return answer_recent_history_intent(user_text, context) or answer_profile_intent(
        user_text, context
    )


def answer_identity_intent(user_text: str) -> str | None:
    compact = re.sub(r"\s+", "", user_text.lower())
    if any(pattern in compact for pattern in MODEL_IDENTITY_PATTERNS):
        return (
            "我是小智，ZENMOP 的智能客服助手。我会基于受控知识库协助处理机器人选购、"
            "使用、维护、故障排查和已授权的使用报告问题。"
        )
    if not any(pattern in compact for pattern in IDENTITY_INTENT_PATTERNS):
        return None
    return (
        "我是小智智能客服，专门处理扫地和扫拖机器人相关问题。"
        "我会优先基于已接入的产品资料回答选购、使用、维护和故障排查问题；"
        "对于资料无法支持的内容，我会明确说明目前无法确认，避免编造；资料更新请联系管理员处理。"
    )


class MemoryRuntimePort(Protocol):
    async def build_context(self, session_id: str, subject_id: str) -> MemoryContext: ...

    async def extract_best_effort(
        self, subject_id: str, source: ConversationMessage
    ) -> str | None: ...


class UsageSummaryRuntimePort(Protocol):
    async def get(self, subject_id: str) -> object | None: ...


@dataclass(frozen=True)
class RuntimeConfig:
    confidence_threshold: float = 0.65
    citation_limit: int = 3

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence_threshold <= 1.0:
            raise ValueError("confidence_threshold must be between 0 and 1")
        if self.citation_limit < 1:
            raise ValueError("citation_limit must be positive")


class AgentRuntime:
    """Deep runtime interface around ReAct, retrieval, memory, and policy."""

    def __init__(
        self,
        *,
        react_engine: ReActEnginePort,
        retriever: RetrieverPort,
        memory: MemoryRuntimePort | None = None,
        usage_summary: UsageSummaryRuntimePort | None = None,
        policy: DeterministicReviewPolicy | None = None,
        citation_service: CitationService | None = None,
        injection_detector: PromptInjectionDetector | None = None,
        draft_gate: DraftGate | None = None,
        config: RuntimeConfig = RuntimeConfig(),
        evidence_polisher: EvidencePolisher | None = None,
    ) -> None:
        self._react_engine = react_engine
        self._retriever = retriever
        self._memory = memory or NullMemoryCoordinator()
        self._usage_summary = usage_summary
        self._policy = policy or DeterministicReviewPolicy(
            confidence_threshold=config.confidence_threshold
        )
        self._citations = citation_service or CitationService()
        self._detector = injection_detector or PromptInjectionDetector()
        self._draft_gate = draft_gate or DraftGate()
        self._config = config
        self._evidence_polisher = evidence_polisher

    async def execute(
        self,
        request: AgentRequest,
        *,
        cancellation: CancellationToken | None = None,
    ) -> AgentRunResult:
        token = cancellation or CancellationToken()
        run_id = request.run_id or str(uuid4())
        state = RunStateMachine()
        trace = TraceRecorder()
        state.transition(RunStatus.RUNNING)
        degraded: list[str] = []

        try:
            token.checkpoint()
            context_token = set_request_context(request.subject_id, request.session_id)
            identity_answer = answer_identity_intent(request.user_text)
            if identity_answer is not None:
                step = trace.start(
                    StepType.POLICY,
                    "answering supported assistant identity and usage intent",
                )
                state.transition(RunStatus.COMPLETED)
                trace.finish(
                    step,
                    StepStatus.SUCCEEDED,
                    "deterministic identity answer selected before retrieval",
                )
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=identity_answer,
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=(),
                    memory_warning=memory_warning,
                    model_name="deterministic-identity",
                    retrieval_strategy="identity-intent",
                )
            calendar_answer = answer_calendar_intent(request.user_text)
            if calendar_answer is not None:
                step = trace.start(
                    StepType.POLICY,
                    "answering deterministic calendar intent before retrieval",
                )
                state.transition(RunStatus.COMPLETED)
                trace.finish(
                    step,
                    StepStatus.SUCCEEDED,
                    "deterministic China-time calendar answer selected before retrieval",
                )
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=calendar_answer,
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=(),
                    memory_warning=memory_warning,
                    model_name="deterministic-calendar",
                    retrieval_strategy="calendar-intent",
                )
            memory_context = await self._prepare_memory(request, trace, degraded)
            compact_request = re.sub(r"\s+", "", request.user_text)
            if any(pattern in compact_request for pattern in USAGE_SUMMARY_INTENT_PATTERNS):
                if self._usage_summary is None:
                    # Keep isolated runtime tests and non-platform adapters useful;
                    # the production bootstrap always supplies the durable snapshot provider.
                    usage_summary_answer = (
                        answer_profile_intent(request.user_text, memory_context)
                        or "当前还没有可用的使用总结。"
                    )
                else:
                    tool_result = await get_user_usage_summary(self._usage_summary)
                    usage_summary_answer = tool_result.display_content
                state.transition(RunStatus.COMPLETED)
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=usage_summary_answer,
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=tuple(dict.fromkeys(degraded)),
                    memory_warning=memory_warning,
                    model_name="deterministic-user-usage-summary",
                    retrieval_strategy="usage-summary-snapshot",
                )
            if classify_meaningless_input(request.user_text):
                state.transition(RunStatus.COMPLETED)
                message = "请继续描述具体需求，例如机器人型号、故障现象、使用场景或报告月份。"
                if memory_context.window or memory_context.summary:
                    message = "我还在当前会话中。请补充完整问题，或继续上一个问题的具体细节。"
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=message,
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=tuple(dict.fromkeys(degraded)),
                    model_name="deterministic-input-guard",
                    retrieval_strategy="input-guard",
                )
            if request.report_tool_executions:
                self._record_tool_executions(trace, request.report_tool_executions)
            if request.catalog_products and is_catalog_inventory_intent(request.user_text):
                step = trace.start(
                    StepType.TOOL,
                    "reading the complete curated robot catalog",
                )
                state.transition(RunStatus.COMPLETED)
                trace.finish(
                    step,
                    StepStatus.SUCCEEDED,
                    f"catalog inventory loaded: {len(request.catalog_products)} products",
                )
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=format_catalog_inventory(request.catalog_products),
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=tuple(dict.fromkeys(degraded)),
                    memory_warning=memory_warning,
                    model_name="curated-catalog",
                    retrieval_strategy="catalog-inventory",
                )
            # Explicit LangGraph route: inspect durable memory first, then
            # perform grounded retrieval.  This keeps ordinary questions from
            # being answered with unrelated profile/device data and gives the
            # no-evidence policy a single, deterministic entry point.
            route = await route_memory_then_knowledge(
                request.user_text,
                memory_context,
                answer_memory=answer_memory_intent,
                retrieve=lambda query: self._retrieve(query, trace, degraded),
                polish=self._evidence_polisher,
            )
            profile_answer = route.get("memory_answer")
            if profile_answer is not None:
                state.transition(RunStatus.COMPLETED)
                memory_warning = await self._extract_memory(request)
                return AgentRunResult(
                    run_id=run_id,
                    status=state.status,
                    public_content=profile_answer,
                    candidate_content=None,
                    citations=(),
                    trace=trace.snapshot(),
                    confidence=1.0,
                    confidence_threshold=self._config.confidence_threshold,
                    degraded_dependencies=tuple(dict.fromkeys(degraded)),
                    memory_warning=memory_warning,
                    model_name="deterministic-user-context",
                    retrieval_strategy="memory-context",
                )
            token.checkpoint()
            retrieval = route.get("retrieval")
            if retrieval is None:
                retrieval = RetrievalResult(hits=(), confidence=0.0, strategy="memory-route")
            if request.mode is ConversationMode.REPORT and request.report_context:
                retrieval = _with_report_evidence(retrieval, request)
            rendered_evidence = render_untrusted_context(retrieval.hits)
            polished_context = route.get("polished_context")
            if polished_context:
                rendered_evidence = (
                    "千问整理的事实摘要（仅供组织语言，不能替代下方原始证据）：\n"
                    f"{polished_context}\n\n{rendered_evidence}"
                )
            token.checkpoint()

            user_injection = self._detector.scan(request.user_text, source="user")
            retrieved_injection = scan_retrieved_content(retrieval.hits, self._detector)
            high_risk, sensitive_claim, warranty_claim = classify_user_risk(request.user_text)
            missing_required_fields = required_fields_missing(request.user_text)
            preliminary_citations = self._citations.build(
                retrieval.hits, limit=self._config.citation_limit
            )
            preliminary_validation = self._citations.validate(
                preliminary_citations,
                [hit.chunk for hit in retrieval.hits],
            )
            preliminary_decision = self._policy.decide(
                PolicyInput(
                    confidence=retrieval.confidence,
                    has_evidence=retrieval.has_evidence,
                    citations_valid=(bool(preliminary_citations) and preliminary_validation.valid),
                    high_risk=high_risk,
                    safety_or_repair_claim=sensitive_claim,
                    warranty_claim=warranty_claim,
                    missing_required_fields=missing_required_fields,
                    conflicting_sources=retrieval.conflicting_sources,
                    prompt_injection=bool(user_injection or retrieved_injection),
                    user_requested_human=request.user_requested_human,
                )
            )
            if preliminary_decision.action is not PolicyAction.PUBLISH:
                candidate = (
                    "检测到需要人工确认的请求，未生成可发布结论。"
                    if preliminary_decision.action is PolicyAction.WITHHOLD_FOR_REVIEW
                    else ""
                )
                return await self._finalize_without_model(
                    request=request,
                    run_id=run_id,
                    state=state,
                    trace=trace,
                    retrieval=retrieval,
                    decision=preliminary_decision,
                    candidate=candidate,
                    citations=preliminary_citations,
                    degraded=degraded,
                )

            generation_step = trace.start(
                StepType.GENERATION, "generating answer with ReAct engine"
            )
            try:
                draft = await self._react_engine.generate(
                    AgentModelRequest(
                        run_id=run_id,
                        mode=request.mode,
                        user_text=request.user_text,
                        rendered_context=rendered_evidence,
                        short_term_messages=memory_context.window,
                        conversation_summary=memory_context.summary,
                        long_term_facts=memory_context.facts,
                        report_scope=request.report_scope,
                        report_context=request.report_context,
                    ),
                    token,
                )
                token.checkpoint()
                trace.finish(
                    generation_step,
                    StepStatus.SUCCEEDED,
                    "ReAct engine produced a candidate draft",
                )
            except ModelTimeout:
                trace.finish(
                    generation_step,
                    StepStatus.TIMEOUT,
                    "model timed out",
                    ErrorCode.MODEL_TIMEOUT,
                )
                state.transition(RunStatus.FAILED)
                return self._failure_result(
                    run_id,
                    state,
                    trace,
                    retrieval,
                    degraded,
                    ErrorCode.MODEL_TIMEOUT,
                )
            except ModelUnavailable:
                trace.finish(
                    generation_step,
                    StepStatus.FAILED,
                    "model unavailable",
                    ErrorCode.MODEL_UNAVAILABLE,
                )
                state.transition(RunStatus.FAILED)
                return self._failure_result(
                    run_id,
                    state,
                    trace,
                    retrieval,
                    degraded,
                    ErrorCode.MODEL_UNAVAILABLE,
                )

            draft = replace(
                draft,
                content=format_user_visible_answer(
                    redact_internal_model_references(redact_local_source_paths(draft.content))
                ),
            )
            self._record_tool_steps(trace, draft)
            citations = self._citations.build(
                retrieval.hits,
                selected_chunk_ids=draft.cited_chunk_ids,
                limit=self._config.citation_limit,
            )
            validation = self._citations.validate(citations, [hit.chunk for hit in retrieval.hits])
            policy_step = trace.start(StepType.POLICY, "checking deterministic release policy")
            final_decision = self._policy.decide(
                PolicyInput(
                    confidence=retrieval.confidence,
                    has_evidence=retrieval.has_evidence,
                    citations_valid=bool(citations) and validation.valid,
                    high_risk=high_risk,
                    safety_or_repair_claim=sensitive_claim,
                    warranty_claim=warranty_claim,
                    missing_required_fields=missing_required_fields,
                    conflicting_sources=retrieval.conflicting_sources,
                    prompt_injection=False,
                    user_requested_human=request.user_requested_human,
                    tool_executions=draft.tool_executions,
                )
            )
            release = self._draft_gate.release(draft.content, final_decision)
            if final_decision.action is PolicyAction.WITHHOLD_FOR_REVIEW:
                state.transition(RunStatus.NEEDS_REVIEW)
                trace.finish(
                    policy_step,
                    StepStatus.WAITING,
                    "candidate withheld for reviewer decision",
                    ErrorCode.REVIEW_REQUIRED,
                )
            else:
                state.transition(RunStatus.COMPLETED)
                trace.finish(
                    policy_step,
                    StepStatus.SUCCEEDED,
                    "candidate passed deterministic release policy",
                )
            memory_warning = await self._extract_memory(request)
            review_id = str(uuid4()) if release.withheld else None
            return AgentRunResult(
                run_id=run_id,
                status=state.status,
                public_content=release.public_content,
                candidate_content=release.candidate_content,
                citations=citations,
                trace=trace.snapshot(),
                confidence=retrieval.confidence,
                confidence_threshold=self._config.confidence_threshold,
                review_reasons=final_decision.reasons,
                degraded_dependencies=tuple(dict.fromkeys(degraded)),
                memory_warning=memory_warning,
                error_code=(ErrorCode.REVIEW_REQUIRED if release.withheld else None),
                model_name=draft.model_name,
                retrieval_strategy=retrieval.strategy,
                review_id=review_id,
            )
        except asyncio.CancelledError:
            trace.cancel_open_steps()
            if state.status is RunStatus.RUNNING:
                state.transition(RunStatus.CANCELLED)
            return AgentRunResult(
                run_id=run_id,
                status=RunStatus.CANCELLED,
                public_content="",
                candidate_content=None,
                citations=(),
                trace=trace.snapshot(),
                confidence=0.0,
                confidence_threshold=self._config.confidence_threshold,
                degraded_dependencies=tuple(dict.fromkeys(degraded)),
                error_code=ErrorCode.CANCELLED,
            )
        except Exception:
            logger.exception("agent runtime failed", extra={"error_code": "INTERNAL_ERROR"})
            trace.cancel_open_steps()
            if state.status is RunStatus.RUNNING:
                state.transition(RunStatus.FAILED)
            return AgentRunResult(
                run_id=run_id,
                status=RunStatus.FAILED,
                public_content="",
                candidate_content=None,
                citations=(),
                trace=trace.snapshot(),
                confidence=0.0,
                confidence_threshold=self._config.confidence_threshold,
                degraded_dependencies=tuple(dict.fromkeys(degraded)),
                error_code=ErrorCode.INTERNAL_ERROR,
            )
        finally:
            if "context_token" in locals():
                reset_request_context(context_token)

    async def _prepare_memory(
        self,
        request: AgentRequest,
        trace: TraceRecorder,
        degraded: list[str],
    ) -> MemoryContext:
        step = trace.start(StepType.CONTEXT, "preparing window, summary, and sourced facts")
        try:
            context = await self._memory.build_context(request.session_id, request.subject_id)
            trace.finish(
                step,
                StepStatus.SUCCEEDED,
                (
                    f"context prepared: window={len(context.window)}, "
                    f"facts={len(context.facts)}, "
                    f"summary={'yes' if context.summary else 'no'}"
                ),
            )
            return context
        except Exception:
            logger.warning("memory context degraded", exc_info=True, extra={"component": "memory"})
            degraded.append("memory")
            trace.finish(
                step,
                StepStatus.FAILED,
                "memory unavailable; continued with empty context",
            )
            return await NullMemoryCoordinator().build_context(
                request.session_id, request.subject_id
            )

    async def _retrieve(
        self,
        query: str,
        trace: TraceRecorder,
        degraded: list[str],
    ) -> RetrievalResult:
        step = trace.start(
            StepType.RETRIEVAL,
            "running vector and keyword retrieval with fusion",
        )
        try:
            result = await self._retriever.retrieve(query)
            degraded.extend(result.degraded_dependencies)
            trace.finish(
                step,
                StepStatus.SUCCEEDED,
                (
                    f"retrieval completed: hits={len(result.hits)}, "
                    f"confidence={result.confidence:.3f}"
                ),
            )
            return result
        except RetrievalUnavailable:
            degraded.extend(("vector_store", "keyword_index"))
            trace.finish(
                step,
                StepStatus.FAILED,
                "retrieval unavailable; applying no-evidence degradation",
                ErrorCode.RETRIEVAL_FAILED,
            )
            return RetrievalResult(
                hits=(),
                confidence=0.0,
                degraded_dependencies=(
                    "vector_store",
                    "keyword_index",
                ),
            )

    async def _finalize_without_model(
        self,
        *,
        request: AgentRequest,
        run_id: str,
        state: RunStateMachine,
        trace: TraceRecorder,
        retrieval: RetrievalResult,
        decision: PolicyDecision,
        candidate: str,
        citations: Sequence[Citation],
        degraded: list[str],
    ) -> AgentRunResult:
        step = trace.start(StepType.POLICY, "applying deterministic pre-generation policy")
        release = self._draft_gate.release(candidate, decision)
        if decision.action is PolicyAction.WITHHOLD_FOR_REVIEW:
            state.transition(RunStatus.NEEDS_REVIEW)
            trace.finish(
                step,
                StepStatus.WAITING,
                "generation blocked and draft withheld for review",
                ErrorCode.REVIEW_REQUIRED,
            )
        else:
            state.transition(RunStatus.COMPLETED)
            trace.finish(
                step,
                StepStatus.SUCCEEDED,
                "safe no-evidence refusal selected",
            )
        memory_warning = await self._extract_memory(request)
        review_id = str(uuid4()) if release.withheld else None
        return AgentRunResult(
            run_id=run_id,
            status=state.status,
            public_content=release.public_content,
            candidate_content=release.candidate_content,
            # A refusal/review card must not present low-confidence or
            # unrelated search hits as if they supported the visible answer.
            # The full retrieval trace remains available to the operator.
            citations=(),
            trace=trace.snapshot(),
            confidence=retrieval.confidence,
            confidence_threshold=self._config.confidence_threshold,
            review_reasons=decision.reasons,
            degraded_dependencies=tuple(dict.fromkeys(degraded)),
            memory_warning=memory_warning,
            error_code=(ErrorCode.REVIEW_REQUIRED if release.withheld else None),
            retrieval_strategy=retrieval.strategy,
            review_id=review_id,
        )

    async def _extract_memory(self, request: AgentRequest) -> str | None:
        try:
            return await self._memory.extract_best_effort(
                request.subject_id,
                ConversationMessage(
                    message_id=request.user_message_id,
                    role="user",
                    content=request.user_text,
                    created_at=utc_now(),
                ),
            )
        except Exception:
            logger.warning(
                "memory extraction degraded", exc_info=True, extra={"component": "memory"}
            )
            # Extraction is explicitly best-effort and cannot turn a valid
            # answer into an internal failure.
            return "memory_extraction_degraded"

    def _record_tool_steps(self, trace: TraceRecorder, draft: ModelDraft) -> None:
        self._record_tool_executions(trace, draft.tool_executions)

    def _record_tool_executions(self, trace: TraceRecorder, executions: Sequence[object]) -> None:
        for execution in executions:
            step = trace.start(
                StepType.TOOL,
                f"tool {execution.tool_name} finished",
            )
            if execution.outcome is ToolOutcome.SUCCEEDED:
                status = StepStatus.SUCCEEDED
            elif execution.outcome is ToolOutcome.TIMEOUT:
                status = StepStatus.TIMEOUT
            elif execution.outcome is ToolOutcome.CANCELLED:
                status = StepStatus.CANCELLED
            else:
                status = StepStatus.FAILED
            trace.finish(
                step,
                status,
                (
                    f"tool={execution.tool_name}, "
                    f"outcome={execution.outcome.value}, "
                    f"attempts={execution.attempts}, "
                    f"duration_ms={execution.duration_ms}"
                ),
                execution.error_code,
            )

    def _failure_result(
        self,
        run_id: str,
        state: RunStateMachine,
        trace: TraceRecorder,
        retrieval: RetrievalResult,
        degraded: list[str],
        error_code: ErrorCode,
    ) -> AgentRunResult:
        return AgentRunResult(
            run_id=run_id,
            status=state.status,
            public_content="",
            candidate_content=None,
            citations=(),
            trace=trace.snapshot(),
            confidence=retrieval.confidence,
            confidence_threshold=self._config.confidence_threshold,
            degraded_dependencies=tuple(dict.fromkeys(degraded)),
            error_code=error_code,
            retrieval_strategy=retrieval.strategy,
        )


def _with_report_evidence(retrieval: RetrievalResult, request: AgentRequest) -> RetrievalResult:
    """Treat a completed report preflight as grounded, traceable evidence."""
    document_id = str(uuid5(NAMESPACE_URL, "agent://external-usage-records"))
    month = request.report_scope.start_at.strftime("%Y-%m") if request.report_scope else "unknown"
    chunk = Chunk(
        document_id=document_id,
        document_version=month,
        chunk_id=f"{document_id}:{request.subject_id}:{month}",
        title="External monthly usage record",
        source="file://data/external/records.csv",
        content=request.report_context or "",
        document_type=DocumentType.TABLE,
    )
    evidence = SearchHit(
        chunk=chunk,
        vector_score=None,
        keyword_score=1.0,
        fused_score=1.0,
        rerank_score=1.0,
    )
    return RetrievalResult(
        hits=(evidence, *retrieval.hits),
        confidence=max(retrieval.confidence, 0.95),
        strategy=f"{retrieval.strategy}+external-report-preflight",
        degraded_dependencies=retrieval.degraded_dependencies,
        conflicting_sources=retrieval.conflicting_sources,
    )
