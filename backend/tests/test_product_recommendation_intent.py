from __future__ import annotations

from backend.app.adapters.llm.platform_executor import _is_robot_recommendation_intent


def test_common_robot_recommendation_phrasing_triggers_catalog_event() -> None:
    assert _is_robot_recommendation_intent("小户型推荐什么机器人")
    assert _is_robot_recommendation_intent("我家有宠物，建议买哪款扫地机")
    assert _is_robot_recommendation_intent("帮我选购适合木地板的型号")


def test_unrelated_recommendation_does_not_trigger_robot_catalog() -> None:
    assert not _is_robot_recommendation_intent("推荐一部电影")


def test_color_preference_followup_triggers_catalog_event() -> None:
    assert _is_robot_recommendation_intent("我喜欢白色的")


def test_catalog_inventory_questions_trigger_complete_catalog_lookup() -> None:
    assert _is_robot_recommendation_intent("有多少产品适合我")
    assert _is_robot_recommendation_intent("扫地机器人一共有几款")


def test_natural_product_selection_questions_trigger_catalog_lookup() -> None:
    """Selection wording must work even when the subject is omitted."""
    for question in (
        "我家最适合哪一款",
        "哪款适合我",
        "哪个更适合我",
        "我应该选哪一个",
        "帮我选一款",
        "有哪些适合我的产品",
        "S8 皓月适合什么家庭",
    ):
        assert _is_robot_recommendation_intent(question), question


def test_generic_recommendation_without_product_context_stays_on_normal_route() -> None:
    assert not _is_robot_recommendation_intent("推荐一款电影")
