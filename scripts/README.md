# Operational Scripts

`scripts/` contains one-off setup and maintenance commands. Scripts can import application services, but they should not become long-running daemons.

Current scripts:

- `init_infra.py`: validate the migrated PostgreSQL/pgvector database, seed demo auth data, and add a baseline paper.
- `run_rag_regression.py`: run the deterministic RAG parser/chunker/retrieval regression set without Docker, live PostgreSQL, browser, or external model APIs.

Rules:

- Make scripts idempotent where possible.
- Read configuration from the same environment variables as `app/config.py`.
- Keep destructive operations explicit and documented.

