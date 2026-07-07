# Backend Package

`app/` is the FastAPI backend. It owns HTTP routes, tenant auth, task lifecycle, persistence services, and background workers.

```text
app/
├── main.py              # FastAPI app, middleware, router registration, static frontend mount
├── config.py            # Environment/runtime configuration
├── dependencies.py      # Auth and shared request dependencies
├── schemas.py           # Shared DTOs and task/event records
├── routes/              # HTTP API layer
├── services/            # Business logic, storage, RAG, rate limit, tracing
└── workers/             # Background runners
```

Rules:

- Routes translate HTTP to service calls.
- Services hold business behavior and tenant isolation rules.
- Persistence code stays in repository/store modules.
- Long-running generation goes through `TaskService` and the event bus.
- New routers must be registered in `main.py` and covered by tests.

