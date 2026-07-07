# Deployment Assets

`deploy/` contains container and reverse-proxy assets.

```text
deploy/
├── Dockerfile.backend
├── Dockerfile.worker
├── Dockerfile.mcp
├── nginx.conf
└── mysql/
    └── init.sql
```

Root compose files:

- `docker-compose.yml`: local full-stack compose.
- `docker-compose.company.yml`: company-oriented deployment profile.

Update this directory whenever runtime entrypoints, ports, environment variables, or database initialization SQL changes.

