from __future__ import annotations

import argparse
import functools
import os
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Awaitable, Callable

import uvicorn
from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from mcp_server.scholar_mcp import tools as _tools  # noqa: E402,F401
from mcp_server.scholar_mcp.registry import tool_registry  # noqa: E402
from mcp_server.scholar_mcp.tools import call_tool_with_safety  # noqa: E402


def _build_protocol_tool(name: str) -> Callable[..., Awaitable[dict[str, Any]]]:
    domain_tool = tool_registry.get_callable(name)

    @functools.wraps(domain_tool)
    async def protocol_tool(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return await call_tool_with_safety(name, kwargs)

    return protocol_tool


def create_mcp_server() -> FastMCP:
    allowed_hosts = [
        value.strip()
        for value in os.getenv(
            "SCHOLAR_MCP_ALLOWED_HOSTS",
            "127.0.0.1:*,localhost:*,[::1]:*,mcp_server:*",
        ).split(",")
        if value.strip()
    ]
    server = FastMCP(
        name="ScholarAgent Paper Tools",
        instructions=(
            "Tenant-scoped paper search, ingestion, knowledge operations and citation audit. "
            "Every tenant-data tool requires tenant_id and user_id."
        ),
        stateless_http=True,
        json_response=True,
        streamable_http_path="/",
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=allowed_hosts,
            allowed_origins=[
                "http://127.0.0.1:*",
                "http://localhost:*",
                "http://[::1]:*",
            ],
        ),
    )
    for name in tool_registry.names():
        spec = tool_registry.get_spec(name)
        server.add_tool(
            _build_protocol_tool(name),
            name=name,
            description=spec.description,
            structured_output=True,
            meta={
                "category": spec.category,
                "safety_level": spec.safety_level.value,
                "requires_user_id": spec.requires_user_id,
            },
        )
    return server


class SharedTokenMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        expected = os.getenv("SCHOLAR_MCP_TOKEN", "").strip()
        if expected:
            authorization = request.headers.get("authorization", "")
            if authorization != f"Bearer {expected}":
                return JSONResponse({"error": "unauthorized MCP request"}, status_code=401)
        return await call_next(request)


mcp = create_mcp_server()


async def health(_: Request) -> JSONResponse:
    return JSONResponse(
        {
            "status": "ok",
            "service": "scholar-mcp",
            "transport": "streamable-http",
            "tool_count": len(tool_registry.names()),
        }
    )


@asynccontextmanager
async def lifespan(_: Starlette):
    async with mcp.session_manager.run():
        yield


app = Starlette(
    routes=[
        Route("/health", health, methods=["GET"]),
        Mount("/mcp", app=mcp.streamable_http_app()),
    ],
    lifespan=lifespan,
)
app.add_middleware(SharedTokenMiddleware)


def main() -> None:
    parser = argparse.ArgumentParser(description="ScholarAgent standard MCP server")
    parser.add_argument(
        "--transport",
        choices=("stdio", "streamable-http"),
        default=os.getenv("SCHOLAR_MCP_TRANSPORT", "streamable-http"),
    )
    parser.add_argument("--host", default=os.getenv("SCHOLAR_MCP_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.getenv("SCHOLAR_MCP_PORT", "8001")))
    args = parser.parse_args()
    if args.transport == "stdio":
        mcp.run(transport="stdio")
        return
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
