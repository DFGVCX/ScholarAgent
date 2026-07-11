from __future__ import annotations

import json
import os
from typing import Any

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from mcp_server.scholar_mcp import tools as _tools  # noqa: F401 - registers tools
from mcp_server.scholar_mcp.registry import tool_registry
from mcp_server.scholar_mcp.tools import call_tool_with_safety


class ScholarMCPClient:
    """MCP client with standard HTTP transport and in-process fallback."""

    def __init__(self, url: str | None = None, token: str | None = None) -> None:
        self.url = (url if url is not None else os.getenv("SCHOLAR_MCP_URL", "")).strip()
        if self.url and not self.url.endswith("/"):
            self.url = f"{self.url}/"
        self.token = (token if token is not None else os.getenv("SCHOLAR_MCP_TOKEN", "")).strip()

    async def list_tools(self) -> list[dict[str, Any]]:
        if not self.url:
            return [tool_registry.get_spec(name).to_dict() for name in tool_registry.names()]
        async with _MCPHttpSession(self.url, self.token) as session:
            result = await session.list_tools()
            return [
                {
                    "name": tool.name,
                    "description": tool.description or "",
                    "input_schema": tool.inputSchema,
                    "meta": tool.meta or {},
                }
                for tool in result.tools
            ]

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not self.url:
            return await call_tool_with_safety(name, arguments)
        async with _MCPHttpSession(self.url, self.token) as session:
            result = await session.call_tool(name, arguments=arguments)
            if result.isError:
                message = next(
                    (str(getattr(block, "text", "")) for block in result.content if getattr(block, "text", "")),
                    "MCP tool execution failed",
                )
                return {"status": "ERROR", "error": message}
            if result.structuredContent is not None:
                return dict(result.structuredContent)
            for block in result.content:
                text = getattr(block, "text", None)
                if text:
                    try:
                        decoded = json.loads(text)
                    except json.JSONDecodeError:
                        return {"status": "OK", "content": text}
                    if isinstance(decoded, dict):
                        return decoded
            return {"status": "ERROR", "error": "MCP tool returned no structured content"}


class _MCPHttpSession:
    def __init__(self, url: str, token: str) -> None:
        self.url = url
        self.token = token
        self._http_context = None
        self._transport_context = None
        self._session_context = None

    async def __aenter__(self) -> ClientSession:
        headers = {"Authorization": f"Bearer {self.token}"} if self.token else {}
        self._http_context = httpx.AsyncClient(headers=headers, timeout=60)
        http_client = await self._http_context.__aenter__()
        self._transport_context = streamable_http_client(self.url, http_client=http_client)
        read_stream, write_stream, _ = await self._transport_context.__aenter__()
        self._session_context = ClientSession(read_stream, write_stream)
        session = await self._session_context.__aenter__()
        await session.initialize()
        return session

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if self._session_context is not None:
            await self._session_context.__aexit__(exc_type, exc, traceback)
        if self._transport_context is not None:
            await self._transport_context.__aexit__(exc_type, exc, traceback)
        if self._http_context is not None:
            await self._http_context.__aexit__(exc_type, exc, traceback)

