"""Tests for tool registration."""

import asyncio

from fastmcp import Client

from plane_mcp.server import get_stdio_mcp
from tests.test_integration import EXPECTED_TOOLS


async def _list_tool_names() -> set[str]:
    async with Client(get_stdio_mcp()) as client:
        tools = await client.list_tools()
    return {tool.name for tool in tools}


def test_expected_tools_registered(monkeypatch):
    monkeypatch.setenv("PLANE_API_KEY", "test-api-key")
    monkeypatch.setenv("PLANE_WORKSPACE_SLUG", "test-workspace")

    tool_names = asyncio.run(_list_tool_names())
    assert tool_names == set(EXPECTED_TOOLS)
