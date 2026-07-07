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

