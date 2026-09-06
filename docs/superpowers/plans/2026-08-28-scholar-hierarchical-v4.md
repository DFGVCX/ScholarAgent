# Scholar Hierarchical v4 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Docling-primary, PyMuPDF-fallback academic PDF parser and a hierarchical semantic chunker while preserving all four existing strategies.

**Architecture:** A lazy Docling adapter normalizes external document items into ScholarAgent's `ParsedPaper` contract. `parse_pdf_hierarchical()` fails over to `multimodal_aware_v3`, and `chunk_hierarchical()` derives prose and typed-object chunks with source-faithful display content plus contextual embedding content.

**Tech Stack:** Python 3.11, Docling, PyMuPDF, pypdf, SQLAlchemy 2 async, Alembic, PostgreSQL 17, pgvector, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-08-28-scholar-hierarchical-v4-design.md`

## Global Constraints

- OCR and scanned-document processing remain disabled.
- `legacy_fixed`, `structure_aware_v1`, `formula_aware_v2`, and `multimodal_aware_v3` remain selectable and unchanged.
- Docling imports stay behind `app/papers/docling_adapter.py` so a missing optional runtime can fall back cleanly.
- `content` remains source-faithful; contextual additions belong in `embedding_content`.
- New database columns are additive and tenant-safe.
- Do not make v4 the default until the real-PDF verification gate passes.

---

### Task 1: Extend the Chunk and Database Contract

**Files:**
- Modify: `app/papers/chunking.py`
- Create: `alembic/versions/20260828_0005_hierarchical_chunks.py`
- Modify: `app/papers/repository.py`
- Modify: `app/retrieval/models.py`
- Modify: `app/retrieval/repository.py`
- Test: `tests/test_paper_chunking.py`
- Test: `tests/test_paper_repository.py`
- Test: `tests/test_retrieval_service.py`

**Interfaces:**
- Produces: additive `ChunkDraft` fields `chunk_type`, `parent_section_id`, `source_block_ids`, `context_before`, `context_after`, `embedding_content`, and `metadata`.
- Produces: matching `paper_chunks` columns and retrieval-hit provenance.

- [ ] Add a failing test proving `embedding_text()` prefers `embedding_content` while `content` remains unchanged.
- [ ] Run `python -m pytest tests/test_paper_chunking.py -q` and verify the constructor/interface failure.
- [ ] Add immutable optional fields with backward-compatible defaults and implement contextual embedding selection.
- [ ] Add migration columns: `chunk_type TEXT NOT NULL DEFAULT 'prose'`, `parent_section_id TEXT`, `source_block_ids JSONB NOT NULL DEFAULT '[]'`, `context_before TEXT`, `context_after TEXT`, `embedding_content TEXT`, and `provenance JSONB NOT NULL DEFAULT '{}'`.
- [ ] Extend repository INSERT/SELECT and response models without removing existing keys.
- [ ] Run `python -m pytest tests/test_paper_chunking.py tests/test_paper_repository.py tests/test_retrieval_service.py -q` and verify all pass.
- [ ] Commit with `feat: extend hierarchical chunk provenance`.

### Task 2: Add the Lazy Docling Adapter

**Files:**
- Create: `app/papers/docling_adapter.py`
- Modify: `requirements.txt`
- Test: `tests/test_docling_adapter.py`

**Interfaces:**
- Produces: `DoclingUnavailable`, `DoclingConversionError`, and `parse_with_docling(path: Path, *, converter: object | None = None) -> ParsedPaper`.
- Consumes: ScholarAgent `ParsedPaper`, `ParsedPage`, `ParsedSection`, and `ParsedBlock`.

- [ ] Add fake-document tests for nested headings, prose, equations, Markdown tables, figure captions, algorithms, page/bbox provenance, and disabled OCR configuration.
- [ ] Run `python -m pytest tests/test_docling_adapter.py -q` and verify import failure.
- [ ] Implement a dependency-injected normalizer plus a lazy production converter factory.
- [ ] Pin Docling in `requirements.txt` and ensure imports outside the adapter do not load it.
- [ ] Run `python -m pytest tests/test_docling_adapter.py tests/test_pdf_dependency_pins.py -q` and verify all pass.
- [ ] Commit with `feat: add Docling academic PDF adapter`.

### Task 3: Add Hierarchical Parser Fallback

**Files:**
- Modify: `app/papers/parsing.py`
- Test: `tests/test_pdf_parsing.py`

**Interfaces:**
- Produces: `HIERARCHICAL_PARSER_NAME = "scholar_hierarchical_v4"`, version `4`, and `parse_pdf_hierarchical(path: Path) -> ParsedPaper`.
- Consumes: `parse_with_docling()` and `parse_pdf_multimodal()`.

- [ ] Add failing tests for successful Docling parsing, missing-Docling fallback, conversion-error fallback, and low-text fallback.
- [ ] Run the focused tests and confirm `parse_pdf_hierarchical` is missing.
- [ ] Implement explicit fallback manifest fields `requested_parser`, `actual_parser`, and `fallback_reason`, plus warning `parser_fallback`.
- [ ] Preserve `needs_ocr` if both primary and fallback lack sufficient embedded text; do not invoke OCR.
- [ ] Run `python -m pytest tests/test_pdf_parsing.py tests/test_multimodal_parsing.py tests/test_formula_parsing.py -q`.
- [ ] Commit with `feat: add hierarchical PDF parser fallback`.

### Task 4: Implement Hierarchical Semantic Chunking

**Files:**
- Modify: `app/papers/chunking.py`
- Test: `tests/test_hierarchical_chunking.py`

**Interfaces:**
- Produces: `chunk_hierarchical(parsed: ParsedPaper, max_tokens: int = 600, overlap_sentences: int = 1) -> list[ChunkDraft]`.

- [ ] Add failing tests that prose stays within section hierarchy and uses contextual embedding content.
- [ ] Add failing equation tests that source content stays atomic while nearby definition/explanation enters embedding content.
- [ ] Add failing table tests for row splitting with repeated caption/header.
- [ ] Add failing figure tests for caption plus explicit reference sentence.
- [ ] Add failing algorithm tests for step-boundary splitting with repeated input/output.
- [ ] Implement a deterministic multilingual token estimator and typed unit builders.
- [ ] Implement target/max budgets, sentence-only overlap for split prose, and stable source block IDs.
- [ ] Run `python -m pytest tests/test_hierarchical_chunking.py tests/test_paper_chunking.py -q`.
- [ ] Commit with `feat: add hierarchical semantic paper chunks`.

### Task 5: Register v4 in Configuration and Ingestion

**Files:**
- Modify: `app/config.py`
- Modify: `app/papers/ingestion.py`
- Modify: `app/evaluation/retrieval.py`
- Modify: `app/services/runtime_config.py`
- Modify: `frontend/dist/app.html`
- Modify: `.env.example`
- Test: `tests/test_postgres_config.py`
- Test: `tests/test_paper_ingestion.py`
- Test: `tests/test_retrieval_evaluation.py`
- Test: `tests/test_runtime_config.py`

**Interfaces:**
- Configuration accepts but does not yet default to `scholar_hierarchical_v4`.
- Ingestion selects `parse_pdf_hierarchical()` and `chunk_hierarchical()` for v4.

- [ ] Add failing configuration, ingestion, and strategy-selection tests.
- [ ] Run the focused tests and verify v4 is rejected or unavailable.
- [ ] Register v4 in allowed values, runtime configuration, ingestion, evaluation selection, and UI option lists.
- [ ] Keep existing defaults on `multimodal_aware_v3` until Task 7.
- [ ] Run all focused tests and commit with `feat: register hierarchical paper strategy`.

### Task 6: Verify Persistence and API Compatibility

**Files:**
- Modify: `app/papers/repository.py`
- Modify: `app/retrieval/models.py`
- Modify: `app/routes/knowledge.py`
- Test: `tests/test_paper_repository.py`
- Test: `tests/test_auth_routes_and_knowledge.py`

**Interfaces:**
- Structured paper and retrieval responses expose additive chunk type and provenance fields.

- [ ] Add failing tests for v4 chunk persistence and additive API serialization.
- [ ] Run tests and verify fields are absent.
- [ ] Implement serialization while keeping every old response key.
- [ ] Run repository/API tests and commit with `feat: expose hierarchical chunk provenance`.

### Task 7: Real-PDF Verification and Default Cutover

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `docker-compose.dev.yml`
- Modify: `docs/operations/STARTUP_CN.md`
- Modify: `RAG_TODO.md`

**Interfaces:**
- Makes v4 the default only after all gates pass.

- [ ] Build or install the pinned Docling runtime with OCR disabled.
- [ ] Run all targeted parser, chunker, repository, ingestion, retrieval, and API tests.
- [ ] Start Docker, run migrations, and re-ingest the current searchable federated-learning PDF.
- [ ] Inspect parser manifest, section hierarchy, formulas, tables, figures, algorithms, and complete Chunk output.
- [ ] If primary Docling succeeds and the structured output is usable, switch parser/chunker defaults to v4; otherwise retain v3 default and document the blocker.
- [ ] Run the full test suite, `docker compose config`, and `git diff --check`.
- [ ] Update startup/TODO documentation with verified behavior and commit with `feat: enable hierarchical paper parsing v4`.
