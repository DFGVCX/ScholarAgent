# Tests

`tests/` covers backend routes, workflow behavior, MCP tools, auth, conversations, citation guard, and E2E scaffolding.

```text
tests/
├── test_*.py
├── api/
└── e2e/
```

Rules:

- New backend routes need route/service tests.
- New skills need workflow tests and focused tool tests.
- New MCP tools need registry/safety tests.
- UI-heavy changes should add or update E2E checks when Playwright infrastructure is available.

Run the deterministic RAG regression (no Docker, live PostgreSQL, browser, or external model API required):

```powershell
python scripts/run_rag_regression.py
```

Live PostgreSQL knowledge-base tests and browser E2E are separate gates and must not be reported as covered by this command.

