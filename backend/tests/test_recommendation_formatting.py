from backend.app.adapters.llm.platform_executor import _chunks
from backend.app.adapters.llm.platform_executor import _filter_recommendations
from backend.app.adapters.mcp.robot_catalog import RecommendedRobot
from backend.app.mcp.robot_catalog_server import PRODUCTS
from backend.app.agent.runtime import format_user_visible_answer


def _robot(product_id: str, name: str) -> RecommendedRobot:
    return RecommendedRobot(
        product_id=product_id,
        model=product_id.upper(),
        name=name,
        price=1,
        highlights=(),
        recommended_for=(),
        image_key=f"robot-{product_id}",
        score=1.0,
        catalog_source="catalog",
    )


def test_recommendations_are_filtered_to_models_named_in_answer() -> None:
    candidates = (_robot("s8-luna", "S8 皓月"), _robot("m6-mini", "M6 Mini"))

    selected = _filter_recommendations(candidates, "结合你的偏好，我推荐 S8 皓月。")

    assert [item.product_id for item in selected] == ["s8-luna"]


def test_recommendations_are_hidden_when_answer_has_no_explicit_model() -> None:
    candidates = (_robot("s8-luna", "S8 皓月"), _robot("m6-mini", "M6 Mini"))

    assert _filter_recommendations(candidates, "可以根据面积和地面材质选择合适方案。") == ()


def test_answer_formatting_preserves_newlines_and_adds_sentence_paragraphs() -> None:
    content = "第一句说明。第二句说明！第三句说明？"

    assert format_user_visible_answer(content) == "第一句说明。\n\n第二句说明！\n\n第三句说明？"


def test_stream_chunks_keep_paragraph_boundaries() -> None:
    assert _chunks("第一段。\n\n第二段。") == ["第一段。\n\n", "第二段。"]


def test_catalog_assigns_one_image_key_per_sku() -> None:
    keys = [product.image_key for product in PRODUCTS]

    assert len(keys) == len(set(keys)) == 6
    assert next(product for product in PRODUCTS if product.product_id == "m6-terra").name == "M6 霞陶"
