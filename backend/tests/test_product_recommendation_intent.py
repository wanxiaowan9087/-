from __future__ import annotations

from backend.app.adapters.llm.platform_executor import _is_robot_recommendation_intent


def test_common_robot_recommendation_phrasing_triggers_catalog_event() -> None:
    assert _is_robot_recommendation_intent("小户型推荐什么机器人")
    assert _is_robot_recommendation_intent("我家有宠物，建议买哪款扫地机")
    assert _is_robot_recommendation_intent("帮我选购适合木地板的型号")


def test_unrelated_recommendation_does_not_trigger_robot_catalog() -> None:
    assert not _is_robot_recommendation_intent("推荐一部电影")
