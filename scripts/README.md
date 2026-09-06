# Operational Scripts

`scripts/` contains one-off setup and maintenance commands. Scripts can import application services, but they should not become long-running daemons.

Current scripts:

- `init_infra.py`: validate the migrated PostgreSQL/pgvector database, seed demo auth data, and add a baseline paper.
- `run_rag_regression.py`: run the deterministic RAG parser/chunker/retrieval regression set without Docker, live PostgreSQL, browser, or external model APIs.
- `evaluate_production_retrieval.py`: evaluate lexical, vector, RRF and reranker strategies through the live PostgreSQL retrieval chain.
- `check_rag_release_gate.py`: enforce metric thresholds, vector consistency and baseline regression limits.
- `benchmark_hnsw.py`: benchmark pgvector HNSW parameters with `EXPLAIN ANALYZE` and buffer statistics.

Rules:

- Make scripts idempotent where possible.
- Read configuration from the same environment variables as `app/config.py`.
- Keep destructive operations explicit and documented.

