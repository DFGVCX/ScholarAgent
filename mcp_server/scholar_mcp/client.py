from __future__ import annotations

from typing import Any

from mcp_server.scholar_mcp import tools as _tools  # noqa: F401 - registers tools
from mcp_server.scholar_mcp.tools import call_tool_with_safety


class ScholarMCPClient:
    """In-process MCP client used by local workflow and tests."""

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return await call_tool_with_safety(name, arguments)

