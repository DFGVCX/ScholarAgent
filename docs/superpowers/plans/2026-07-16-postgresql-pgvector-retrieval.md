# PostgreSQL + pgvector Paper Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace ScholarAgent's SQLite/JSON/Chroma paper storage with a fresh PostgreSQL + pgvector database and expose one tenant-safe hybrid retrieval service using Qwen3-Embedding-0.6B.

**Architecture:** Existing relational callers keep the `mysql_store` compatibility API while its connection implementation moves to PostgreSQL. New paper code uses focused domain models, repository, ingestion, embedding, and retrieval modules; API, MCP, conversation Agent, and writing workflow consume the same retrieval contract.

**Tech Stack:** Python 3.11+, FastAPI, SQLAlchemy 2 async, psycopg 3, Alembic, PostgreSQL 17, pgvector 0.8.5, Qwen3-Embedding-0.6B (OpenAI-compatible embedding endpoint), pytest/unittest.

## Global Constraints

- Start from a fresh PostgreSQL database; do not migrate SQLite, JSON, or Chroma data.
- PostgreSQL is the only relational and retrieval source of truth; there is no runtime fallback.
- Store normalized 1024-dimensional Qwen embeddings in `VECTOR(1024)` and use cosine distance.
- Store paper files on the persistent volume; store URI, SHA-256, size, MIME type, and processing state in PostgreSQL.
- Enforce tenant and user predicates in repositories and RLS policies.
- Fuse lexical and vector ranks with RRF using `1 / (60 + rank)` and do not apply recency scoring.
- Keep external candidates separate and non-citeable until acquired, parsed, and indexed.
- Pin the database image to `pgvector/pgvector:0.8.5-pg17-bookworm`.

---

### Task 1: PostgreSQL runtime foundation

**Files:**
- Modify: `requirements.txt`
- Modify: `app/config.py`
- Create: `app/db/__init__.py`
- Create: `app/db/session.py`
- Create: `alembic.ini`
- Create: `alembic/env.py`
- Create: `alembic/versions/20260716_0001_postgres_foundation.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Test: `tests/test_postgres_config.py`

**Interfaces:**
- Produces: `Settings.database_url: str`, `Settings.embedding_model == "Qwen3-Embedding-0.6B"`, `get_async_session() -> AsyncIterator[AsyncSession]`, and `tenant_transaction(tenant_id, user_id)`.

- [ ] **Step 1: Write failing configuration tests**

```python
def test_postgres_and_qwen_defaults(monkeypatch):
    monkeypatch.setenv("SCHOLAR_DATABASE_URL", "postgresql+psycopg://u:p@db/scholar")
    settings = get_settings()
    assert settings.database_url.startswith("postgresql+psycopg://")
    assert settings.rag_embedding_model == "Qwen3-Embedding-0.6B"
    assert settings.rag_embedding_dimensions == 1024
```

- [ ] **Step 2: Run the test and verify the missing settings failure**

Run: `python -m pytest tests/test_postgres_config.py -q`
Expected: FAIL because `Settings.database_url` does not exist.

- [ ] **Step 3: Add dependencies, settings, session helpers, Alembic bootstrap, and PostgreSQL compose service**

```python
engine = create_async_engine(settings.database_url, pool_pre_ping=True)
session_factory = async_sessionmaker(engine, expire_on_commit=False)

@asynccontextmanager
async def tenant_transaction(tenant_id: str, user_id: str):
    async with session_factory() as session, session.begin():
        await session.execute(text("SET LOCAL app.tenant_id = :value"), {"value": tenant_id})
        await session.execute(text("SET LOCAL app.user_id = :value"), {"value": user_id})
        yield session
```

- [ ] **Step 4: Run configuration tests**

Run: `python -m pytest tests/test_postgres_config.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt app/config.py app/db alembic.ini alembic .env.example docker-compose.yml tests/test_postgres_config.py
git commit -m "feat: add PostgreSQL pgvector runtime foundation"
```

### Task 2: PostgreSQL compatibility store for existing services

**Files:**
- Modify: `app/services/mysql_store.py`
- Modify: `app/main.py`
- Modify: `app/routes/health.py`
- Test: `tests/test_postgres_store.py`
- Delete: `tests/test_sqlite_store.py`

**Interfaces:**
- Produces: existing `execute`, `fetch_one`, `fetch_all`, `connection`, `encode_json`, `decode_json`, annotation, translation, and runtime-setting APIs backed only by psycopg.

- [ ] **Step 1: Test placeholder conversion and PostgreSQL availability behavior**

```python
def test_qmark_placeholders_are_converted_without_touching_literals():
    assert _translate_sql("SELECT '?' AS literal, id FROM x WHERE a = ?") == (
        "SELECT '?' AS literal, id FROM x WHERE a = %s"
    )

def test_unreachable_database_is_not_available(monkeypatch):
    monkeypatch.setattr(mysql_store, "_connect", Mock(side_effect=OperationalError("down")))
    mysql_store.reset_availability_cache()
    assert mysql_store.is_available() is False
```

- [ ] **Step 2: Run and observe SQLite-specific failures**

Run: `python -m pytest tests/test_postgres_store.py -q`
Expected: FAIL because the store imports sqlite3 and translates toward SQLite.

- [ ] **Step 3: Replace SQLite connections with psycopg pooled connections and dict rows**

```python
def execute(sql: str, params=None) -> int:
    with connection() as conn, conn.cursor() as cursor:
        cursor.execute(_translate_sql(sql), _adapt_params(params))
        affected = cursor.rowcount
        conn.commit()
        return affected
```

`initialize_database()` must only validate connectivity and migration state; it must not create tables at application startup.

- [ ] **Step 4: Update health output to report PostgreSQL and pgvector**

```python
row = mysql_store.fetch_one(
    "SELECT current_database() AS database, current_setting('server_version') AS version, "
    "EXISTS(SELECT 1 FROM pg_extension WHERE extname = 'vector') AS vector_enabled"
)
```

- [ ] **Step 5: Run store tests**

Run: `python -m pytest tests/test_postgres_store.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add app/services/mysql_store.py app/main.py app/routes/health.py tests/test_postgres_store.py tests/test_sqlite_store.py
git commit -m "refactor: back relational compatibility API with PostgreSQL"
```

### Task 3: Paper domain schema and repository

**Files:**
- Create: `app/papers/__init__.py`
- Create: `app/papers/models.py`
- Create: `app/papers/repository.py`
- Create: `app/papers/chunking.py`
- Modify: `alembic/versions/20260716_0001_postgres_foundation.py`
- Test: `tests/test_paper_repository.py`
- Test: `tests/test_paper_chunking.py`

**Interfaces:**
- Produces: `PaperInput`, `PaperRecord`, `PaperChunk`, `PaperRepository.save()`, `.get()`, `.list()`, `.set_kb()`, `.soft_delete()`, `.replace_content()`.

- [ ] **Step 1: Test deterministic chunking and repository tenant predicates**

```python
def test_chunks_are_stable_and_nonempty():
    chunks = chunk_text("First paragraph.\n\nSecond paragraph.", 30, 5)
    assert [chunk.position for chunk in chunks] == list(range(len(chunks)))
    assert all(chunk.content.strip() for chunk in chunks)

async def test_get_requires_tenant_and_user(fake_session):
    await PaperRepository(fake_session).get("tenant-a", "user-a", "paper-1")
    sql = str(fake_session.last_statement)
    assert "tenant_id" in sql and "user_id" in sql and "deleted_at" in sql
```

- [ ] **Step 2: Run tests to verify modules are missing**

Run: `python -m pytest tests/test_paper_chunking.py tests/test_paper_repository.py -q`
Expected: FAIL on import.

- [ ] **Step 3: Define immutable DTOs, paragraph chunking, repository SQL, constraints, indexes, and RLS**

```python
@dataclass(frozen=True)
class PaperInput:
    paper_id: str
    source: str
    title: str
    authors: tuple[str, ...] = ()
    abstract: str = ""
    full_text: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    file_uri: str | None = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
```

The migration creates `papers`, `paper_assets`, `paper_contents`, `paper_chunks`, `paper_ingestion_jobs`, annotation and translation tables; `paper_chunks.embedding` is `vector(1024)`, `search_vector` is generated `tsvector`, with GIN and HNSW indexes and tenant/user RLS policies.

- [ ] **Step 4: Run paper tests**

Run: `python -m pytest tests/test_paper_chunking.py tests/test_paper_repository.py -q`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/papers alembic/versions/20260716_0001_postgres_foundation.py tests/test_paper_chunking.py tests/test_paper_repository.py
git commit -m "feat: add consistent paper domain model and repository"
```

### Task 4: Qwen embedding client

**Files:**
- Create: `app/retrieval/__init__.py`
- Create: `app/retrieval/embedding.py`
- Test: `tests/test_qwen_embedding.py`

**Interfaces:**
- Produces: `QwenEmbeddingClient.embed(texts: Sequence[str]) -> list[list[float]]`; every vector is finite, normalized, and length 1024.

- [ ] **Step 1: Test validation and normalization**

```python
async def test_embedding_is_normalized(httpx_mock):
    httpx_mock.add_response(json={"data": [{"index": 0, "embedding": [2.0] + [0.0] * 1023}]})
    vectors = await client.embed(["paper query"])
    assert len(vectors[0]) == 1024
    assert sum(x * x for x in vectors[0]) == pytest.approx(1.0)
```

- [ ] **Step 2: Run and verify import failure**

Run: `python -m pytest tests/test_qwen_embedding.py -q`
Expected: FAIL on import.

- [ ] **Step 3: Implement the OpenAI-compatible Qwen client**

```python
payload = {"model": self.model, "input": list(texts), "dimensions": 1024}
async with session.post(f"{self.base_url}/v1/embeddings", json=payload, headers=headers) as response:
    response.raise_for_status()
    return self._validate_and_normalize(await response.json(), len(texts))
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_qwen_embedding.py -q`
Expected: PASS.

```bash
git add app/retrieval tests/test_qwen_embedding.py
git commit -m "feat: add validated Qwen embedding client"
```

### Task 5: Unified hybrid RetrievalService

**Files:**
- Create: `app/retrieval/models.py`
- Create: `app/retrieval/service.py`
- Test: `tests/test_retrieval_service.py`

**Interfaces:**
- Produces: `RetrievalRequest`, `LocalHit`, `ExternalCandidate`, `RetrievalResponse`, and `RetrievalService.search(request)`.

- [ ] **Step 1: Test RRF, citeability, and lexical degradation**

```python
def test_rrf_merges_by_chunk_without_recency():
    merged = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
    assert merged[0][0] == "b"
    assert merged[0][1] == pytest.approx(1 / 62 + 1 / 61)

async def test_embedding_failure_keeps_lexical_results(service):
    service.embedding.embed.side_effect = RuntimeError("offline")
    response = await service.search(RetrievalRequest("t", "u", "query"))
    assert response.mode == "lexical"
    assert all(hit.can_cite for hit in response.local_hits)
```

- [ ] **Step 2: Run and verify failures**

Run: `python -m pytest tests/test_retrieval_service.py -q`
Expected: FAIL on import.

- [ ] **Step 3: Implement lexical SQL, vector SQL, RRF fusion, paper aggregation, and separate external candidates**

```python
async def search(self, request: RetrievalRequest) -> RetrievalResponse:
    lexical = await self.repository.lexical_candidates(request)
    try:
        vector = await self.repository.vector_candidates(request, (await self.embedding.embed([request.query]))[0])
        mode = "hybrid"
    except EmbeddingUnavailable:
        vector, mode = [], "lexical"
    return self._assemble(request, lexical, vector, mode)
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_retrieval_service.py -q`
Expected: PASS.

```bash
git add app/retrieval tests/test_retrieval_service.py
git commit -m "feat: add unified PostgreSQL hybrid retrieval"
```

### Task 6: Ingestion and knowledge API/MCP cutover

**Files:**
- Create: `app/papers/ingestion.py`
- Modify: `mcp_server/scholar_mcp/store.py`
- Modify: `mcp_server/scholar_mcp/tools.py`
- Modify: `app/services/rag_service.py`
- Modify: `app/routes/knowledge.py`
- Delete: `app/services/chroma_store.py`
- Test: `tests/test_paper_ingestion.py`
- Modify: `tests/test_auth_routes_and_knowledge.py`

**Interfaces:**
- Consumes: `PaperRepository`, `QwenEmbeddingClient`, `RetrievalService`.
- Produces: one save/index transaction workflow and backward-compatible knowledge API response keys.

- [ ] **Step 1: Test save, version replacement, failure state, and deletion**

```python
async def test_save_indexes_current_version_only(ingestion):
    first = await ingestion.ingest(tenant, user, paper_v1)
    second = await ingestion.ingest(tenant, user, paper_v2)
    assert second.content_version == first.content_version + 1
    assert await repository.searchable_versions(second.paper_uuid) == [second.content_version]
```

- [ ] **Step 2: Run targeted tests and observe old Chroma/JSON calls**

Run: `python -m pytest tests/test_paper_ingestion.py tests/test_auth_routes_and_knowledge.py -q`
Expected: FAIL because old services call JSON and Chroma.

- [ ] **Step 3: Route save/list/get/toggle/delete/search/stats through PostgreSQL services**

```python
async def search(self, tenant_id: str, user_id: str, query: str, limit: int):
    response = await self.retrieval.search(RetrievalRequest(tenant_id, user_id, query, limit))
    return response.to_legacy_dict()
```

- [ ] **Step 4: Run targeted tests and commit**

Run: `python -m pytest tests/test_paper_ingestion.py tests/test_auth_routes_and_knowledge.py -q`
Expected: PASS.

```bash
git add app/papers app/routes/knowledge.py app/services/rag_service.py app/services/chroma_store.py mcp_server tests
git commit -m "refactor: cut knowledge ingestion and MCP over to PostgreSQL"
```

### Task 7: Agent and writing workflow retrieval cutover

**Files:**
- Modify: `agents/conversation_tool_loop.py`
- Modify: `agents/specialized/writing_agent.py`
- Modify: `mcp_server/scholar_mcp/tools.py`
- Test: `tests/test_conversation_tool_loop.py`
- Test: `tests/test_retrieval_strategy.py`

**Interfaces:**
- Consumes: `RetrievalService.search()` through MCP `search_papers`.
- Produces: local citeable hits and separately labeled external candidates for both Agent runtimes.

- [ ] **Step 1: Test both consumers preserve `can_cite`**

```python
async def test_search_tool_marks_only_local_ready_hits_citeable(tool_loop):
    result = await tool_loop.call("search_papers", {"query": "RAG"})
    assert all(item["can_cite"] for item in result["local_hits"])
    assert not any(item["can_cite"] for item in result["external_candidates"])
```

- [ ] **Step 2: Run and verify old flat-result contract fails**

Run: `python -m pytest tests/test_conversation_tool_loop.py tests/test_retrieval_strategy.py -q`
Expected: FAIL on the missing separated result contract.

- [ ] **Step 3: Replace direct/flat searches with the unified response contract**

```python
return {
    "local_hits": [hit.to_dict() for hit in response.local_hits],
    "external_candidates": [candidate.to_dict() for candidate in response.external_candidates],
    "retrieval_mode": response.mode,
}
```

- [ ] **Step 4: Run tests and commit**

Run: `python -m pytest tests/test_conversation_tool_loop.py tests/test_retrieval_strategy.py -q`
Expected: PASS.

```bash
git add agents mcp_server/scholar_mcp/tools.py tests
git commit -m "refactor: unify Agent and writing paper retrieval"
```

### Task 8: Operational cleanup and full verification

**Files:**
- Modify: `app/routes/health.py`
- Modify: `app/services/runtime_config.py`
- Modify: `README.md`
- Modify: `app/README.md`
- Modify: `mcp_server/README.md`
- Modify: `deploy/README.md`
- Delete: `deploy/mysql/init.sql`
- Delete: `scripts/bootstrap_mysql.py`
- Modify: `scripts/init_infra.py`
- Test: `tests/test_postgres_health.py`

**Interfaces:**
- Produces: deployment and health contract showing PostgreSQL migration revision, pgvector availability, Qwen model, chunk count, and failed ingestion jobs.

- [ ] **Step 1: Test health contract and scan for forbidden runtime backends**

```python
def test_health_reports_postgres_pgvector_and_qwen(client):
    data = client.get("/health").json()
    assert data["database"]["engine"] == "postgresql"
    assert data["database"]["pgvector"] is True
    assert data["retrieval"]["embedding_model"] == "Qwen3-Embedding-0.6B"
```

- [ ] **Step 2: Remove obsolete configuration, scripts, dependencies, and documentation**

Run: `rg -n "chromadb|knowledge\.json|rag_chunks\.json|mysql:8|sqlite_master" app mcp_server docker-compose.yml deploy scripts requirements.txt`
Expected: no runtime references; historical migration/design documentation may still mention them.

- [ ] **Step 3: Run focused and full verification**

Run: `python -m pytest tests/test_postgres_config.py tests/test_postgres_store.py tests/test_paper_chunking.py tests/test_paper_repository.py tests/test_qwen_embedding.py tests/test_retrieval_service.py tests/test_paper_ingestion.py tests/test_postgres_health.py -q`
Expected: PASS.

Run: `python -m unittest discover -s tests -v`
Expected: PASS, or only explicitly documented failures that require unavailable external services.

Run: `docker compose config`
Expected: exit 0 and database image `pgvector/pgvector:0.8.5-pg17-bookworm`.

- [ ] **Step 4: Commit**

```bash
git add app mcp_server deploy scripts README.md requirements.txt tests docker-compose.yml
git commit -m "chore: remove legacy paper storage backends"
```
