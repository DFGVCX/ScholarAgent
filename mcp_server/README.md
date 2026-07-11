# MCP Tool Boundary

`mcp_server/` owns paper ingestion, external source search, knowledge-store tools, and safety registration. Backend services and skills should call these capabilities through the MCP client boundary rather than duplicating source-specific logic.

```text
mcp_server/
├── server.py
└── scholar_mcp/
    ├── client.py
    ├── tools.py
    ├── registry.py
    ├── safety.py
    ├── store.py
    ├── external_sources.py
    └── models.py
```

Rules:

- Every tool must receive `tenant_id` and `user_id` when it touches tenant data.
- External source failures should return structured errors where possible.
- New tools must be registered with safety metadata and tests.
- PDF/download behavior belongs here, not in frontend code.

## Standard MCP transport

The server uses the official Python MCP SDK `FastMCP` implementation and exposes
Streamable HTTP at `http://127.0.0.1:8001/mcp/`.

```powershell
.\.venv\Scripts\python.exe mcp_server\server.py --transport streamable-http --host 127.0.0.1 --port 8001
```

Set `SCHOLAR_MCP_URL=http://127.0.0.1:8001/mcp/` on the backend. Production
deployments can set `SCHOLAR_MCP_TOKEN`; the client then sends it as a Bearer token.

Conversation tools use the complete loop: discovery, planning, invocation,
observation, persistent user confirmation for high-risk calls, and resumed execution.
Call state is stored in `scholar_conversation_tool_calls` for tenant-scoped audit and
restart recovery.

