from __future__ import annotations

from uuid import NAMESPACE_URL, uuid5

from backend.app.rag.evidence import (
    assess_evidence,
    select_citation_hits,
    select_query_evidence_hits,
)
from backend.app.rag.models import Chunk, DocumentType, SearchHit


def _hit(
    content: str,
    *,
    vector_score: float | None = 0.58,
    keyword_score: float | None = 0.62,
    fused_score: float = 0.61,
) -> SearchHit:
    return SearchHit(
        chunk=Chunk(
            document_id=str(uuid5(NAMESPACE_URL, content)),
            document_version="v1",
            chunk_id=f"chunk-{abs(hash(content))}",
            title="扫地机器人维护指南",
            source="kb://maintenance",
            content=content,
            document_type=DocumentType.TEXT,
        ),
        vector_score=vector_score,
        keyword_score=keyword_score,
        fused_score=fused_score,
    )


def test_dual_channel_direct_evidence_can_pass_below_global_threshold() -> None:
    decision = assess_evidence(
        "主刷缠绕毛发应该怎样清理？",
        (_hit("主刷缠绕毛发时，应先断电，再拆下主刷并清理毛发。"),),
        confidence=0.61,
    )

    assert decision.supported
    assert not decision.knowledge_boundary_unsupported


def test_unrelated_high_confidence_hit_is_not_treated_as_support() -> None:
    decision = assess_evidence(
        "X9-EDGE 的精确越障高度是多少毫米？",
        (
            _hit(
                "这篇资料介绍扫地机器人的日常保养方法。",
                vector_score=0.91,
                keyword_score=0.88,
                fused_score=0.93,
            ),
        ),
        confidence=0.94,
    )

    assert not decision.supported
    assert decision.knowledge_boundary_unsupported


def test_exact_parameter_is_supported_only_when_evidence_contains_the_value() -> None:
    decision = assess_evidence(
        "S8-LUNA 的电池容量是多少毫安时？",
        (_hit("S8-LUNA 的电池容量为 5200mAh。"),),
        confidence=0.72,
    )

    assert decision.supported
    assert not decision.knowledge_boundary_unsupported


def test_other_model_parameter_cannot_ground_requested_model() -> None:
    decision = assess_evidence(
        "X9-EDGE 的精确越障高度是多少毫米？",
        (_hit("M6-MINI 小径的越障高度为 18 毫米。"),),
        confidence=0.91,
    )

    assert not decision.supported
    assert decision.knowledge_boundary_unsupported


def test_realtime_private_and_future_requests_are_outside_snapshot_knowledge() -> None:
    questions = (
        "今天杭州每家门店的实时库存分别是多少？",
        "请提供 ZENMOP 法务负责人的私人手机号。",
        "下一版固件准确发布日期和未公开功能是什么？",
        "哪款机器人获得了 2027 年尚未公布的奖项？",
        "小径的窄机身是否保证能进入所有家具缝？",
        "霞陶越障高度能否按照其他型号参数推断？",
        "曜石是否确认支持任意清洁液？",
        "曜石 Edge 有强吸力，所以一定带自动上下水吗？",
    )

    for question in questions:
        decision = assess_evidence(
            question,
            (_hit("ZENMOP 产品与维护资料。"),),
            confidence=0.9,
        )
        assert not decision.supported, question
        assert decision.knowledge_boundary_unsupported, question


def test_citation_selector_uses_final_answer_to_choose_supporting_chunk() -> None:
    mapping = _hit(
        "X9-OBSIDIAN 支持多房间建图。",
        vector_score=0.93,
        keyword_score=0.90,
        fused_score=0.95,
    )
    battery = _hit(
        "X9-OBSIDIAN 长期不使用时应关机并放在阴凉干燥处；电池异常发热时立即停用。",
        vector_score=0.86,
        keyword_score=0.82,
        fused_score=0.88,
    )

    selected = select_citation_hits(
        "曜石长期不用和电池异常时怎么办？",
        "长期不用时请关机并置于阴凉干燥处；如果电池异常发热，应立即停用。",
        (mapping, battery),
        limit=2,
    )

    assert selected[0] is battery


def test_query_evidence_selector_is_available_before_generation() -> None:
    mapping = _hit("X9-OBSIDIAN 支持多房间建图。", fused_score=0.95)
    battery = _hit("X9-OBSIDIAN 长期不用时应关机并保持干燥。", fused_score=0.88)

    selected = select_query_evidence_hits(
        "曜石长期不用时怎么办？",
        (mapping, battery),
        limit=1,
    )

    assert selected[0] is battery
