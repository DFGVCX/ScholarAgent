# Multimodal Paper Blocks Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a backward-compatible multimodal PDF parser and expose typed paper blocks in retrieval, the API, and the paper workbench.

**Architecture:** `multimodal_aware_v3` extends the existing formula-aware page pipeline and stores typed metadata in `paper_pages.blocks`. A structured-content API reads the current PostgreSQL page/section rows, while a protected asset endpoint serves PDF-local crops. The legacy single-file frontend renders these blocks in the body view and a dedicated visual-assets tab.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy async, PostgreSQL JSONB, PyMuPDF, pytest/unittest, HTML/CSS/vanilla JavaScript, KaTeX, Docker Compose.

## Global Constraints

- Preserve `legacy_fixed`, `structure_aware_v1`, and `formula_aware_v2` unchanged and selectable.
- Store canonical text and structure in PostgreSQL; derived PNG files remain tenant-scoped filesystem assets.
- Use Qwen embeddings through the existing embedding client.
- Visual extraction fails closed with text fallback and an explicit quality status.
- Equations, tables, figures, and algorithms are atomic retrieval units.

---

### Task 1: Typed visual blocks and PDF asset extraction

**Files:**
- Create: `app/papers/visuals.py`
- Modify: `app/papers/parsing.py`
- Modify: `app/config.py`
- Test: `tests/test_multimodal_parsing.py`

**Interfaces:**
- Produces `parse_pdf_multimodal(path: Path) -> ParsedPaper`.
- Produces typed `ParsedBlock.metadata` dictionaries and PDF-local PNG crops.

- [ ] Write tests for caption classification, Markdown table normalization, metadata serialization, asset naming, and an actual small PyMuPDF fixture.
- [ ] Run `pytest tests/test_multimodal_parsing.py -q` and verify the missing v3 interfaces fail.
- [ ] Implement the smallest extraction module and parser integration that satisfies those tests.
- [ ] Run the multimodal, formula, and PDF parsing tests.
- [ ] Commit the parser slice.

### Task 2: Atomic multimodal retrieval chunks and ingestion selection

**Files:**
- Modify: `app/papers/chunking.py`
- Modify: `app/papers/ingestion.py`
- Modify: `app/evaluation/retrieval.py`
- Test: `tests/test_paper_chunking.py`
- Test: `tests/test_paper_ingestion.py`
- Test: `tests/test_retrieval_evaluation.py`

**Interfaces:**
- Produces `chunk_multimodal(parsed: ParsedPaper, max_chars: int, overlap_chars: int) -> list[ChunkDraft]`.
- Consumes `parse_pdf_multimodal` when parser or chunk strategy is `multimodal_aware_v3`.

- [ ] Add failing tests showing tables, figures, algorithms, and equations are not split and include provenance text.
- [ ] Run targeted tests and verify failures are caused by the missing v3 path.
- [ ] Implement block-aware atomic chunks while retaining section prose chunks.
- [ ] Register v3 in configuration and retrieval evaluation without removing old strategies.
- [ ] Run targeted parsing/chunking/ingestion/retrieval tests and commit.

### Task 3: Structured paper and protected asset API

**Files:**
- Modify: `app/papers/repository.py`
- Modify: `app/routes/knowledge.py`
- Test: `tests/test_paper_repository.py`
- Test: `tests/test_auth_routes_and_knowledge.py`

**Interfaces:**
- Produces `PaperRepository.get_structure(tenant_id, user_id, paper_id)`.
- Produces `GET /knowledge/{paper_id}/structure`.
- Produces `GET /knowledge/files/{paper_id}/assets/{asset_name}`.

- [ ] Add failing repository and route tests for ordered blocks/sections and tenant-safe asset resolution.
- [ ] Verify tests fail on missing methods/routes.
- [ ] Implement current-version queries and strict manifest/path validation.
- [ ] Run repository, route, auth, and ingestion tests and commit.

### Task 4: Structured body and figure/table/algorithm workbench

**Files:**
- Modify: `frontend/dist/app.html`
- Test: `tests/test_frontend_gateway.py`
- Test: `tests/test_formula_parsing.py`

**Interfaces:**
- Consumes the structured-content and asset endpoints.
- Renders `body`, `equation`, `table`, `figure`, and `algorithm` cards with text fallback.

- [ ] Add failing static frontend contract tests for the renamed tab, structured fetch, type filters, Markdown table rendering, asset URL, and fallback.
- [ ] Run the frontend tests and verify the new UI contracts are absent.
- [ ] Add focused CSS/JavaScript renderers and replace the edit tab with `图表算法`.
- [ ] Run frontend and formula rendering tests and commit.

### Task 5: Docker and browser verification

**Files:**
- Update tests only if verification exposes a reproducible regression.

**Interfaces:**
- Uses the existing Docker Compose services and the supplied federated-learning PDF.

- [ ] Run all targeted tests in the backend container.
- [ ] Rebuild/restart affected backend and frontend services.
- [ ] Re-ingest the supplied PDF with v3 and inspect PostgreSQL/API block counts and asset paths.
- [ ] Open the local site, select the paper, verify `原文`, `正文`, and `图表算法`, and inspect equations plus every detected visual type.
- [ ] Run the full test suite, record unrelated baseline failures separately, and commit any final verified fixes.
