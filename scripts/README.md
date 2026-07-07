# Operational Scripts

`scripts/` contains one-off setup and maintenance commands. Scripts can import application services, but they should not become long-running daemons.

Current scripts:

- `bootstrap_mysql.py`: initialize MySQL-backed demo data, auth, and RAG records.
- `init_infra.py`: initialize local infrastructure and baseline knowledge records.

Rules:

- Make scripts idempotent where possible.
- Read configuration from the same environment variables as `app/config.py`.
- Keep destructive operations explicit and documented.

