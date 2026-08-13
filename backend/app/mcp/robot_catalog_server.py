"""Local stdio MCP server for the curated ZENMOP robot catalog.

It intentionally writes protocol frames only to stdout. Diagnostic output is
sent to stderr so this process remains compatible with MCP stdio clients.
"""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

CATALOG_PATH = Path(__file__).resolve().parents[3] / "data" / "catalog" / "zenmop_robot_catalog.md"


@dataclass(frozen=True)
class RobotProduct:
    product_id: str
    model: str
    name: str
    price: int
    highlights: tuple[str, ...]
    recommended_for: tuple[str, ...]
    image_key: str
    catalog_source: str = "data/catalog/zenmop_robot_catalog.md"


PRODUCTS: tuple[RobotProduct, ...] = (
    RobotProduct("s8-luna", "S8-LUNA", "S8 皓月", 2999, ("静音运行", "激光导航", "自动集尘", "拖布热风烘干"), ("夜间清洁", "婴幼儿家庭", "80-140 平方米"), "robot-ivory"),
    RobotProduct("s8-air", "S8-AIR", "S8 Air", 1999, ("轻薄机身", "基础扫拖", "边角清洁优化"), ("60 平方米以内", "小户型", "预算有限"), "robot-ivory"),
    RobotProduct("x9-obsidian", "X9-OBSIDIAN", "X9 曜石", 4299, ("全屋激光建图", "双旋拖布", "地毯识别", "自动上下水接口"), ("大户型", "多房间", "宠物家庭"), "robot-graphite"),
    RobotProduct("x9-edge", "X9-EDGE", "X9 Edge", 3599, ("贴边清洁", "毫米级避障", "强吸力", "地毯增压"), ("家具较多", "边角灰尘", "养宠家庭"), "robot-graphite"),
    RobotProduct("m6-terra", "M6-TERRA", "M6 陶土", 2499, ("高扭矩滚刷", "可调水量", "木地板保护", "分区清洁"), ("木地板", "瓷砖", "混合地面"), "robot-terracotta"),
    RobotProduct("m6-mini", "M6-MINI", "M6 Mini", 1599, ("紧凑机身", "低噪扫拖", "定时任务"), ("40 平方米以内", "单身公寓", "卧室"), "robot-terracotta"),
)


class RecommendInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    query: str = Field(min_length=1, max_length=500)
    budget_max: int | None = Field(default=None, ge=1000, le=10000)
    limit: int = Field(default=3, ge=1, le=3)


class GetInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    product_id: str = Field(min_length=1, max_length=40)


def _to_payload(product: RobotProduct, *, score: float | None = None) -> dict[str, Any]:
    payload = asdict(product)
    payload["highlights"] = list(product.highlights)
    payload["recommended_for"] = list(product.recommended_for)
    if score is not None:
        payload["score"] = score
    return payload


async def robot_catalog_recommend(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return up to three structured robot recommendations from the curated catalog."""
    request = RecommendInput.model_validate(arguments)
    query = request.query.lower()
    terms = tuple(token for token in query.replace("，", " ").replace("。", " ").split() if token)
    ranked: list[tuple[float, RobotProduct]] = []
    for product in PRODUCTS:
        haystack = " ".join((product.model, product.name, *product.highlights, *product.recommended_for)).lower()
        score = sum(1.0 for term in terms if term in haystack)
        if any(word in query for word in ("宠物", "猫", "狗")) and "宠物家庭" in product.recommended_for:
            score += 2.0
        if any(word in query for word in ("大户型", "大房", "别墅")) and "大户型" in product.recommended_for:
            score += 2.0
        if any(word in query for word in ("小户型", "公寓", "宿舍")) and ("小户型" in product.recommended_for or "单身公寓" in product.recommended_for):
            score += 2.0
        if "木地板" in query and "木地板" in product.recommended_for:
            score += 2.0
        if request.budget_max is not None and product.price > request.budget_max:
            continue
        ranked.append((score, product))
    ranked.sort(key=lambda item: (-item[0], item[1].price, item[1].product_id))
    selected = ranked[: request.limit]
    return {
        "items": [_to_payload(product, score=round(score, 2)) for score, product in selected],
        "catalog_source": str(CATALOG_PATH),
        "total": len(selected),
    }


async def robot_catalog_get(arguments: dict[str, Any]) -> dict[str, Any]:
    """Return complete model, price, feature, image-key, and source information for one product."""
    request = GetInput.model_validate(arguments)
    product = next((item for item in PRODUCTS if item.product_id == request.product_id), None)
    if product is None:
        return {"error": "PRODUCT_NOT_FOUND", "message": "Use robot_catalog_recommend to discover valid product_id values."}
    return _to_payload(product)


TOOLS: dict[str, dict[str, Any]] = {
    "robot_catalog_recommend": {
        "description": "Recommend ZENMOP robot models from the curated catalog. Read-only and returns structured model, price, features, image key and product id.",
        "inputSchema": RecommendInput.model_json_schema(),
    },
    "robot_catalog_get": {
        "description": "Get full details for one ZENMOP robot product_id. Read-only.",
        "inputSchema": GetInput.model_json_schema(),
    },
}


async def handle_request(request: dict[str, Any]) -> dict[str, Any] | None:
    method = request.get("method")
    request_id = request.get("id")
    if method == "notifications/initialized":
        return None
    if method == "initialize":
        result: dict[str, Any] = {"protocolVersion": "2025-06-18", "capabilities": {"tools": {}}, "serverInfo": {"name": "robot_catalog_mcp", "version": "1.0.0"}}
    elif method == "tools/list":
        result = {"tools": [{"name": name, **definition, "annotations": {"readOnlyHint": True, "destructiveHint": False, "idempotentHint": True, "openWorldHint": False}} for name, definition in TOOLS.items()]}
    elif method == "tools/call":
        params = request.get("params", {})
        name = params.get("name")
        handler = {"robot_catalog_recommend": robot_catalog_recommend, "robot_catalog_get": robot_catalog_get}.get(name)
        if handler is None:
            result = {"content": [{"type": "text", "text": "Unknown tool. Call tools/list first."}], "isError": True}
        else:
            try:
                structured = await handler(params.get("arguments", {}))
                result = {"content": [{"type": "text", "text": json.dumps(structured, ensure_ascii=False)}], "structuredContent": structured, "isError": "error" in structured}
            except Exception as error:
                result = {"content": [{"type": "text", "text": f"Invalid tool input: {error}"}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


async def serve() -> None:
    for line in sys.stdin:
        try:
            request = json.loads(line)
            response = await handle_request(request)
            if response is not None:
                print(json.dumps(response, ensure_ascii=False), flush=True)
        except json.JSONDecodeError:
            print(json.dumps({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}), flush=True)


if __name__ == "__main__":
    asyncio.run(serve())
