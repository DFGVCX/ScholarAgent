# Deployment Assets

`deploy/` contains container and reverse-proxy assets.

```text
deploy/
├── Dockerfile.backend
├── Dockerfile.worker
├── Dockerfile.mcp
└── nginx.conf
```

Root compose files:

- `docker-compose.yml`: local full-stack compose.
- `docker-compose.company.yml`: company-oriented deployment profile.

Database schema changes live in `alembic/versions/`; containers use the pinned pgvector PostgreSQL image from the compose files.

