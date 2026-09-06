# Web Model Configuration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Docker web console able to log in, configure/test Agent LLM and Qwen embedding credentials, and switch embedding models without mixing incompatible vectors.

**Architecture:** Nginx is the same-origin API gateway. PostgreSQL remains the deployment-wide runtime configuration store, candidate probes are non-persistent, and embedding activation marks incompatible vectors stale before durable worker-driven re-embedding. Vector retrieval always filters by the active embedding model while lexical retrieval remains available.

**Tech Stack:** FastAPI, Pydantic 2, SQLAlchemy async, PostgreSQL 17, pgvector, Redis worker, nginx, vanilla console JavaScript inside the current React iframe shell, unittest, Docker Compose.

## Global Constraints

- PostgreSQL and pgvector are the only runtime storage/retrieval backends.
- `paper_chunks.embedding` remains `vector(1024)`.
- Embedding provider remains `qwen`; model names may change only when the provider returns exactly 1024 dimensions.
- Blank secret input preserves the existing secret; API responses and logs never contain the secret.
- Runtime configuration is deployment-scoped and writable only by `tenant_admin`.
- Failed probes do not mutate persisted configuration.
- Vector retrieval never mixes chunks from different embedding models.
- Lexical retrieval remains operational during re-embedding.

---

### Task 1: Same-origin frontend API gateway

**Files:**
- Modify: `deploy/nginx.conf`
- Create: `tests/test_frontend_gateway.py`

**Interfaces:**
- Consumes: frontend relative API paths such as `/auth/login`, `/settings/runtime`, `/conversations`, `/agents`, `/tasks`, and `/knowledge`.
- Produces: nginx proxying for every backend API namespace while keeping `/`, `/index.html`, `/app.html`, and static assets local.

- [ ] **Step 1: Write the failing gateway contract test**

```python
from pathlib import Path
import unittest


class FrontendGatewayTests(unittest.TestCase):
    def test_all_console_api_namespaces_are_proxied(self) -> None:
        nginx = Path("deploy/nginx.conf").read_text(encoding="utf-8")
        for prefix in (
            "auth", "settings", "agents", "conversations", "tasks", "knowledge",
            "institutional-access", "health",
        ):
            self.assertIn(prefix, nginx)
        self.assertIn("proxy_pass http://backend:8000", nginx)
```

- [ ] **Step 2: Run the gateway test and confirm the existing config fails**

Run: `python -m unittest tests.test_frontend_gateway -v`

Expected: FAIL because `/auth`, `/settings`, `/agents`, and `/conversations` are not proxied.

- [ ] **Step 3: Replace route drift with one API namespace location**

Use this shape in `deploy/nginx.conf`:

```nginx
location ~ ^/(auth|settings|agents|conversations|tasks|knowledge|institutional-access|health)(/|$) {
    proxy_pass http://backend:8000;
    proxy_http_version 1.1;
    proxy_set_header Host $host;
    proxy_set_header X-Real-IP $remote_addr;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_buffering off;
    proxy_cache off;
}

location / {
    try_files $uri $uri/ /index.html;
}
```

- [ ] **Step 4: Verify nginx syntax contract and frontend build configuration**

Run: `python -m unittest tests.test_frontend_gateway -v && docker compose config --quiet`

Expected: PASS and exit code 0.

- [ ] **Step 5: Commit the gateway fix**

```powershell
git add deploy/nginx.conf tests/test_frontend_gateway.py
git commit -m "fix: proxy web console API routes"
```

---

### Task 2: Candidate model configuration and secret-safe probes

**Files:**
- Create: `app/services/model_configuration.py`
- Modify: `agents/factory.py`
- Modify: `app/routes/settings.py`
- Create: `tests/test_model_configuration.py`
- Create: `tests/test_settings_routes.py`

**Interfaces:**
- Produces: `ModelCandidate`, `resolve_model_candidate(values, settings)`, `ModelFactory.probe(candidate, prompt)`, and candidate-aware `POST /settings/model/probe`.
- Consumes: the existing `Settings` values and blank-secret-preserves-current semantics.

- [ ] **Step 1: Write failing candidate-resolution tests**

```python
def test_blank_candidate_key_reuses_configured_secret():
    current = SimpleNamespace(
        primary_model_provider="qwen", llm_base_url="https://dashscope.example/compatible-mode",
        llm_api_key="stored-secret", llm_model="qwen-plus",
        anthropic_base_url="https://api.anthropic.com", anthropic_api_key="", anthropic_model="",
    )
    candidate = resolve_model_candidate({"api_key": ""}, current)
    assert candidate.api_key == "stored-secret"

def test_remote_candidate_requires_model_and_key(self):
    with self.assertRaisesRegex(ValueError, "API key"):
        ModelCandidate(provider="qwen", base_url="https://example", api_key="", model="qwen-plus")
```

- [ ] **Step 2: Verify candidate tests fail because the module does not exist**

Run: `python -m unittest tests.test_model_configuration -v`

Expected: import failure for `app.services.model_configuration`.

- [ ] **Step 3: Implement the focused candidate value object**

```python
@dataclass(frozen=True)
class ModelCandidate:
    provider: str
    base_url: str
    api_key: str
    model: str
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""

    def validate(self) -> "ModelCandidate":
        if self.provider in {"", "none"}:
            raise ValueError("Select a model provider")
        if self.provider in OPENAI_COMPATIBLE_PROVIDERS and not self.model:
            raise ValueError("Model name is required")
        if self.provider not in LOCAL_OPENAI_COMPATIBLE_PROVIDERS and self.provider not in ANTHROPIC_PROVIDERS and not self.api_key:
            raise ValueError("API key is required for a remote model provider")
        return self
```

`resolve_model_candidate` must use the saved secret only when the submitted secret is blank.

- [ ] **Step 4: Add candidate-aware probe behavior to the model factory**

Refactor the provider calls so `probe` does not mutate environment or PostgreSQL:

```python
async def probe(self, candidate: ModelCandidate, prompt: str) -> ModelResponse:
    candidate.validate()
    return await self._generate_with_candidate(
        candidate, "config_probe", prompt,
        {"tenant_id": "settings-probe", "user_id": "settings-probe"},
    )
```

Reuse the existing OpenAI-compatible and Anthropic request/response validation; do not duplicate prompt tracing or expose request headers in errors.

- [ ] **Step 5: Add the candidate DTO and route test**

```python
class ModelProbeDTO(BaseModel):
    provider: str = ""
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    anthropic_base_url: str = ""
    anthropic_api_key: str = ""
    anthropic_model: str = ""
    prompt: str = Field(default="用一句中文回答：ScholarAgent 模型接入已连通。", max_length=1000)
```

Test that a non-admin receives 403, the fake factory receives the unsaved candidate, and the response contains provider/model/content but no API key.

- [ ] **Step 6: Run focused tests**

Run: `python -m unittest tests.test_model_configuration tests.test_settings_routes -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit candidate LLM probes**

```powershell
git add app/services/model_configuration.py agents/factory.py app/routes/settings.py tests/test_model_configuration.py tests/test_settings_routes.py
git commit -m "feat: probe candidate model settings safely"
```

---

### Task 3: Flexible Qwen candidate probe with fixed vector contract

**Files:**
- Modify: `app/retrieval/embedding.py`
- Modify: `app/papers/ingestion.py`
- Modify: `app/routes/settings.py`
- Modify: `tests/test_qwen_embedding.py`
- Modify: `tests/test_settings_routes.py`

**Interfaces:**
- Produces: any non-empty Qwen model name with `dimensions == 1024`; `EmbeddingProbeDTO`; `POST /settings/embedding/probe`.
- Consumes: Qwen OpenAI-compatible `/v1/embeddings` response and existing strict normalization.

- [ ] **Step 1: Write failing model-flexibility and payload tests**

```python
async def test_qwen_model_name_can_change_but_dimensions_remain_1024():
    client = QwenEmbeddingClient(
        base_url="https://embedding.example/compatible-mode",
        api_key="secret", model="Qwen3-Embedding-4B", dimensions=1024,
        session_factory=lambda **_: fake_session,
    )
    await client.embed(["probe"])
    assert fake_session.request_json["model"] == "Qwen3-Embedding-4B"
    assert fake_session.request_json["dimensions"] == 1024

def test_non_1024_dimensions_are_rejected():
    with self.assertRaisesRegex(ValueError, "1024"):
        QwenEmbeddingClient(base_url="https://embedding.example", model="x", dimensions=768)
```

- [ ] **Step 2: Run and confirm the hard-coded model assertion fails**

Run: `python -m unittest tests.test_qwen_embedding -v`

Expected: FAIL with `embedding model must be Qwen3-Embedding-0.6B`.

- [ ] **Step 3: Relax only the model-name constraint**

```python
if not model.strip():
    raise ValueError("Qwen embedding model is required")
if dimensions != self.DIMENSIONS:
    raise ValueError(f"embedding dimensions must be {self.DIMENSIONS}")
self.model = model.strip()
```

Keep the `dimensions` request property and exact response-length validation.

- [ ] **Step 4: Store the actual active model during ingestion**

Change `PaperIngestionService` to pass `self._embedding().model` to `set_embeddings` instead of `QwenEmbeddingClient.MODEL`.

- [ ] **Step 5: Add the non-persistent embedding probe route**

```python
class EmbeddingProbeDTO(BaseModel):
    base_url: str = ""
    api_key: str = ""
    model: str = ""
    dimensions: int = 1024

@router.post("/embedding/probe")
async def probe_embedding(request: EmbeddingProbeDTO, x_api_key: str | None = Header(...)):
    _require_tenant_admin(x_api_key)
    candidate = resolve_embedding_candidate(request.model_dump(), get_settings())
    vectors = await QwenEmbeddingClient(**candidate.client_kwargs()).embed(["ScholarAgent embedding probe"])
    return {"status": "ok", "provider": "qwen", "model": candidate.model, "dimensions": len(vectors[0])}
```

Map remote failures to a redacted 502 response and validation failures to 422.

- [ ] **Step 6: Run Qwen and route tests**

Run: `python -m unittest tests.test_qwen_embedding tests.test_settings_routes tests.test_paper_ingestion -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit flexible Qwen probes**

```powershell
git add app/retrieval/embedding.py app/papers/ingestion.py app/routes/settings.py tests/test_qwen_embedding.py tests/test_settings_routes.py
git commit -m "feat: probe compatible Qwen embedding models"
```

---

### Task 4: Active-model vector isolation and stale lifecycle

**Files:**
- Create: `alembic/versions/20260717_0002_embedding_model_lifecycle.py`
- Modify: `app/retrieval/repository.py`
- Modify: `app/retrieval/service.py`
- Modify: `app/papers/repository.py`
- Create: `tests/test_embedding_lifecycle.py`
- Modify: `tests/test_retrieval_service.py`

**Interfaces:**
- Produces: `embedding_status='stale'`, `PaperRepository.mark_embeddings_stale`, `PaperRepository.embedding_stats`, and active-model-filtered vector candidates.
- Consumes: active `QwenEmbeddingClient.model` from `RetrievalService`.

- [ ] **Step 1: Write failing repository-contract tests**

Assert generated vector SQL contains both:

```sql
AND c.embedding_status='ready'
AND c.embedding_model=:embedding_model
```

and parameters include the active model. Add a repository fake that records `mark_embeddings_stale(tenant_id, user_id, active_model)` and returns ready/stale/failed totals.

- [ ] **Step 2: Verify the active-model test fails**

Run: `python -m unittest tests.test_embedding_lifecycle tests.test_retrieval_service -v`

Expected: FAIL because `vector_candidates` has no model argument/filter.

- [ ] **Step 3: Add the stale status migration**

```python
def upgrade() -> None:
    op.execute("ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS paper_chunks_embedding_status_check")
    op.execute("ALTER TABLE paper_chunks ADD CONSTRAINT paper_chunks_embedding_status_check CHECK (embedding_status IN ('pending','ready','stale','failed'))")

def downgrade() -> None:
    op.execute("UPDATE paper_chunks SET embedding_status='pending', embedding=NULL WHERE embedding_status='stale'")
    op.execute("ALTER TABLE paper_chunks DROP CONSTRAINT IF EXISTS paper_chunks_embedding_status_check")
    op.execute("ALTER TABLE paper_chunks ADD CONSTRAINT paper_chunks_embedding_status_check CHECK (embedding_status IN ('pending','ready','failed'))")
```

- [ ] **Step 4: Filter vector candidates by active model**

Extend the repository protocol and implementation:

```python
async def vector_candidates(self, request, embedding, embedding_model: str): ...
```

Call it from retrieval with `self.embedding.model`.

- [ ] **Step 5: Implement stale transition and status counts**

```sql
UPDATE paper_chunks
SET embedding=NULL, embedding_status='stale', embedding_error=NULL, updated_at=now()
WHERE tenant_id=:tenant_id AND user_id=:user_id
  AND embedding_status='ready'
  AND embedding_model IS DISTINCT FROM :active_model
```

`embedding_stats` counts current-content chunks grouped as ready for active model, stale (explicit or model mismatch), failed, and pending.

- [ ] **Step 6: Run migration SQL and focused tests**

Run: `python -m alembic upgrade head --sql > $null; python -m unittest tests.test_embedding_lifecycle tests.test_retrieval_service -v`

Expected: exit code 0 and all tests PASS.

- [ ] **Step 7: Commit vector isolation**

```powershell
git add alembic/versions/20260717_0002_embedding_model_lifecycle.py app/retrieval/repository.py app/retrieval/service.py app/papers/repository.py tests/test_embedding_lifecycle.py tests/test_retrieval_service.py
git commit -m "feat: isolate vectors by active embedding model"
```

---

### Task 5: Durable re-embedding jobs and settings activation

**Files:**
- Create: `app/papers/reembedding.py`
- Modify: `app/papers/repository.py`
- Modify: `app/workers/runner.py`
- Modify: `app/routes/settings.py`
- Modify: `app/services/runtime_config.py`
- Create: `tests/test_reembedding_service.py`
- Modify: `tests/test_settings_routes.py`

**Interfaces:**
- Produces: `EmbeddingReindexService.enqueue`, `EmbeddingReindexService.process_next`, `POST /settings/embedding/reindex`, and embedding status attached to `GET /settings/runtime`.
- Consumes: `paper_ingestion_jobs`, tenant transactions, current chunks, and the active Qwen client.

- [ ] **Step 1: Write failing enqueue/idempotency tests**

```python
async def test_enqueue_creates_one_job_per_stale_paper_and_skips_existing_pending_jobs():
    result = await service.enqueue("tenant_demo", "user_demo")
    self.assertEqual(result, {"created": 2, "existing": 1})

async def test_process_next_embeds_current_chunks_with_active_model():
    processed = await service.process_next("worker-test")
    self.assertEqual(processed.status, "completed")
    self.assertEqual(repository.saved_model, "Qwen3-Embedding-4B")
```

- [ ] **Step 2: Verify tests fail because the service does not exist**

Run: `python -m unittest tests.test_reembedding_service -v`

Expected: import failure for `app.papers.reembedding`.

- [ ] **Step 3: Add repository job primitives**

Implement transaction-safe SQL methods:

- `enqueue_reembedding_jobs`: insert `job_type='reembed'` for distinct stale current papers unless a pending/running/retry reembed job already exists.
- `claim_reembedding_job`: `FOR UPDATE SKIP LOCKED`, transition to running, set lock fields and increment attempts.
- `current_embedding_batch`: return current content UUID and chunks ordered by index.
- `complete_ingestion_job` / `fail_ingestion_job`: durable terminal/retry state with a redacted 4000-character error.

- [ ] **Step 4: Implement one-job processing**

```python
class EmbeddingReindexService:
    async def process_next(self, worker_id: str) -> ReindexResult | None:
        job = await self._claim(worker_id)
        if job is None:
            return None
        client = QwenEmbeddingClient.from_settings()
        batch = await self._load_batch(job)
        vectors = await client.embed([item.content for item in batch.chunks])
        await self._save(job, batch, vectors, model=client.model)
        return ReindexResult(job_id=job.job_id, status="completed", chunk_count=len(vectors))
```

On failure, record a redacted error and retry up to `max_attempts`; never delete lexical text.

- [ ] **Step 5: Let the existing worker drain durable reembed jobs**

After each Redis reserve timeout, call `embedding_reindex_service.process_next(worker_id)`. Also call it once after a survey task acknowledgement so reindexing progresses without starving survey work.

- [ ] **Step 6: Activate embedding settings safely in the route**

When embedding endpoint/model/dimension changes:

1. Resolve the candidate with preserved secret.
2. Probe it.
3. Persist runtime settings.
4. Mark current user's mismatched embeddings stale.
5. Return status and `reindex_required`.

Add `POST /settings/embedding/reindex` to enqueue jobs and `GET /settings/runtime` embedding counts.

- [ ] **Step 7: Run reindex and route tests**

Run: `python -m unittest tests.test_reembedding_service tests.test_settings_routes tests.test_embedding_lifecycle -v`

Expected: all tests PASS.

- [ ] **Step 8: Commit durable re-embedding**

```powershell
git add app/papers/reembedding.py app/papers/repository.py app/workers/runner.py app/routes/settings.py app/services/runtime_config.py tests/test_reembedding_service.py tests/test_settings_routes.py
git commit -m "feat: queue durable embedding reindex jobs"
```

---

### Task 6: Align and complete the web configuration experience

**Files:**
- Modify: `frontend/dist/app.html`
- Create: `tests/test_model_settings_ui.py`

**Interfaces:**
- Consumes: candidate probe routes, runtime save route, embedding status, and reindex route.
- Produces: usable Agent model and Qwen embedding panels with independent probe/status actions.

- [ ] **Step 1: Write failing static UI contract tests**

```python
def test_settings_ui_uses_postgres_pgvector_and_qwen_only():
    html = Path("frontend/dist/app.html").read_text(encoding="utf-8")
    assert "PostgreSQL 17 + pgvector" in html
    assert 'value="qwen"' in html
    assert "测试 Agent 模型" in html
    assert "测试 Embedding" in html
    assert "重新生成向量" in html
    for legacy in ("MySQL URL", "JSON 文件", "Jina Embeddings", "Cohere Embeddings"):
        assert legacy not in html
```

- [ ] **Step 2: Run and confirm stale legacy UI fails**

Run: `python -m unittest tests.test_model_settings_ui -v`

Expected: FAIL on PostgreSQL/Qwen labels and legacy choices.

- [ ] **Step 3: Replace the storage and embedding controls**

Display PostgreSQL/pgvector as read-only status facts. Keep only `hybrid_rrf` and `lexical` retrieval choices. The embedding panel contains Qwen endpoint/key/model and a disabled `1024` dimensions control.

- [ ] **Step 4: Send candidate form values to each probe**

```javascript
async function probeEmbedding() {
  const data = await api('/settings/embedding/probe', {
    method: 'POST',
    body: JSON.stringify({
      base_url: $('cfgRagEmbeddingBaseUrl').value.trim(),
      api_key: $('cfgRagEmbeddingApiKey').value.trim(),
      model: $('cfgRagEmbeddingModel').value.trim(),
      dimensions: 1024,
    }),
  });
  $('embeddingProbeResult').textContent = `${data.model} / ${data.dimensions} 维 / 已连通`;
}
```

Implement the equivalent candidate body for Agent model probing. Disable only the action currently in flight.

- [ ] **Step 5: Add activation warning and reindex status**

If the model field differs from the active model, show that activation makes old vectors lexical-only until reindex completes. Render ready/stale/failed counts and wire `重新生成向量` to `POST /settings/embedding/reindex`.

- [ ] **Step 6: Run UI and existing frontend-related tests**

Run: `python -m unittest tests.test_model_settings_ui tests.test_frontend_gateway tests.test_auth -v`

Expected: all tests PASS.

- [ ] **Step 7: Commit the web configuration UI**

```powershell
git add frontend/dist/app.html tests/test_model_settings_ui.py
git commit -m "feat: add web model configuration center"
```

---

### Task 7: Docker, regression, and browser acceptance

**Files:**
- Modify: `docs/operations/STARTUP_CN.md`
- Modify: `.env.example`
- Test: all focused tests from Tasks 1-6

**Interfaces:**
- Produces: verified Docker deployment and documented browser configuration flow.

- [ ] **Step 1: Document deployment-scoped settings and browser workflow**

Document login at `http://localhost:3000`, the two configuration panels, blank-secret preservation, probe-before-save, embedding reindex behavior, and the fact that changing `.env` requires container recreation while web settings are read dynamically.

- [ ] **Step 2: Run the complete focused test suite**

Run:

```powershell
python -m unittest `
  tests.test_frontend_gateway tests.test_model_configuration tests.test_settings_routes `
  tests.test_qwen_embedding tests.test_paper_ingestion tests.test_embedding_lifecycle `
  tests.test_retrieval_service tests.test_reembedding_service tests.test_model_settings_ui `
  tests.test_browser_worker tests.test_postgres_config tests.test_postgres_health -v
```

Expected: zero failures and zero errors.

- [ ] **Step 3: Verify build, migration, and formatting**

Run:

```powershell
python -m compileall app agents
python -m alembic upgrade head --sql > $null
docker compose config --quiet
git diff --check
docker compose build frontend backend worker
docker compose up -d
```

Expected: all commands exit 0.

- [ ] **Step 4: Verify live services**

Run:

```powershell
Invoke-WebRequest -UseBasicParsing http://127.0.0.1:3000/
Invoke-RestMethod http://127.0.0.1:8000/health
Invoke-RestMethod http://127.0.0.1:8000/health/infra
docker compose ps
```

Expected: frontend HTTP 200, backend status `ok`, PostgreSQL and pgvector true, and all long-running services up.

- [ ] **Step 5: Perform browser acceptance without a real paid key**

Using the in-app browser:

1. Open `http://127.0.0.1:3000`.
2. Log in with `tenant_demo / demo / demo123`.
3. Open `个人中心 > 模型路由`; verify provider, Base URL, key, and model inputs.
4. Submit a deliberately incomplete candidate and verify an actionable redacted error with no persistence.
5. Open `知识检索`; verify Qwen-only, fixed 1024 dimensions, embedding status, and reindex button.
6. Save a harmless non-secret display setting, reload, and verify persistence.
7. Confirm browser console contains no uncaught errors and no secret values.

- [ ] **Step 6: Commit documentation and final verification changes**

```powershell
git add docs/operations/STARTUP_CN.md .env.example
git commit -m "docs: explain web model configuration"
```
