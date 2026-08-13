"""Typed client adapter for the local robot catalog MCP server."""

from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any


class RobotCatalogMcpError(RuntimeError):
    """A safe, caller-actionable MCP transport or response error."""


@dataclass(frozen=True)
class RecommendedRobot:
    product_id: str
    model: str
    name: str
    price: int
    highlights: tuple[str, ...]
    recommended_for: tuple[str, ...]
    image_key: str
    score: float
    catalog_source: str


class RobotCatalogMcpClient:
    """A short-lived stdio MCP client for a local, read-only catalog.

    A process per request keeps chat runs isolated and means a malformed MCP
    response cannot poison another user's conversation.
    """

    async def recommend(self, query: str, *, limit: int = 3) -> tuple[RecommendedRobot, ...]:
        result = await self._call("robot_catalog_recommend", {"query": query, "limit": limit})
        items = result.get("items")
        if not isinstance(items, list):
            raise RobotCatalogMcpError("robot catalog returned no recommendation list")
        recommendations: list[RecommendedRobot] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            try:
                recommendations.append(
                    RecommendedRobot(
                        product_id=str(item["product_id"]),
                        model=str(item["model"]),
                        name=str(item["name"]),
                        price=int(item["price"]),
                        highlights=tuple(str(value) for value in item.get("highlights", [])),
                        recommended_for=tuple(str(value) for value in item.get("recommended_for", [])),
                        image_key=str(item["image_key"]),
                        score=float(item.get("score", 0)),
                        catalog_source=str(item["catalog_source"]),
                    )
                )
            except (KeyError, TypeError, ValueError) as error:
                raise RobotCatalogMcpError("robot catalog returned an invalid product") from error
        return tuple(recommendations)

    async def _call(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-m",
            "backend.app.mcp.robot_catalog_server",
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        assert process.stdin is not None and process.stdout is not None
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": tool_name, "arguments": arguments}}
        try:
            stdout, stderr = await asyncio.wait_for(
                process.communicate((json.dumps(request, ensure_ascii=False) + "\n").encode()),
                timeout=3.0,
            )
        except TimeoutError as error:
            process.kill()
            await process.wait()
            raise RobotCatalogMcpError("robot catalog MCP timed out") from error
        if process.returncode not in {0, None}:
            raise RobotCatalogMcpError("robot catalog MCP is unavailable")
        try:
            envelope = json.loads(stdout.decode("utf-8").strip())
            result = envelope["result"]
            if result.get("isError"):
                raise RobotCatalogMcpError("robot catalog MCP rejected the request")
            structured = result["structuredContent"]
        except (KeyError, TypeError, json.JSONDecodeError) as error:
            raise RobotCatalogMcpError("robot catalog MCP returned an invalid response") from error
        if not isinstance(structured, dict):
            raise RobotCatalogMcpError("robot catalog MCP returned malformed data")
        return structured


async def recommend_robots(query: str, *, limit: int = 3) -> tuple[RecommendedRobot, ...]:
    return await RobotCatalogMcpClient().recommend(query, limit=limit)
