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

