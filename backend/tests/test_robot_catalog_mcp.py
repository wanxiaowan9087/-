from __future__ import annotations

import pytest

from backend.app.adapters.mcp.robot_catalog import recommend_robots


@pytest.mark.asyncio
async def test_catalog_mcp_returns_all_six_products_for_inventory_query() -> None:
    products = await recommend_robots("有多少产品适合我", limit=6)

    assert len(products) == 6
    assert {product.product_id for product in products} == {
        "s8-luna",
        "s8-air",
        "x9-obsidian",
        "x9-edge",
        "m6-terra",
        "m6-mini",
    }
