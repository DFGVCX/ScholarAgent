# Structured PDF Parsing and Structure-Aware Chunking Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an auditable PyMuPDF PDF parser and structure-aware hierarchical chunker while retaining the current pypdf and chunking behavior as a reproducible evaluation baseline.

**Architecture:** A parser module produces immutable page/section records plus a parse manifest. Ingestion chooses a named parser/chunker strategy, persists the canonical content graph transactionally, and embeds contextualized text while preserving raw chunk content. An offline evaluator runs both strategies on identical labeled examples and reports retrieval metrics without mutating production content.

**Tech Stack:** Python 3.12, PyMuPDF, pypdf, FastAPI, SQLAlchemy async, PostgreSQL 17, pgvector, Alembic, Qwen embeddings, unittest/pytest.

## Global Constraints

- Preserve `legacy_fixed` parser and chunker behavior as callable production code.
- Default new PDF ingestion to `structure_aware_v1`.
- Never truncate canonical PDF text at 50,000 characters.
- Do not embed documents whose parse status is `needs_ocr` or `failed`.
- Store raw chunk text unchanged; contextual prefixes are used only for embeddings.
- Persist parser/chunker names and versions with every content version.
- Do not overwrite manually edited text by automatically re-reading an attached PDF.
- Retrieval comparisons must use the same corpus, query labels, embedding model, and configuration.

---

### Task 1: Parser domain model and PyMuPDF structured extraction

**Files:**
- Create: `app/papers/parsing.py`
- Create: `tests/test_pdf_parsing.py`
- Modify: `requirements.txt`

**Interfaces:**
- Produces: `ParsedBlock`, `ParsedPage`, `ParsedSection`, `ParsedPaper`, `parse_pdf(path: Path) -> ParsedPaper`, and `parse_pdf_legacy(path: Path) -> ParsedPaper`.
- Consumes: local PDF paths already validated by upload/acquisition code.

- [ ] **Step 1: Write failing parser tests**

Create generated PDF fixtures with PyMuPDF and assert complete page coverage, stable sections, repeated margin removal, metadata extraction, and visible failure states.

```python
def test_parse_pdf_preserves_pages_sections_and_provenance(tmp_path: Path) -> None:
    path = write_pdf(tmp_path / "paper.pdf", [
        ["Shared Header", "Abstract", "First page abstract.", "1"],
        ["Shared Header", "1 Introduction", "Federated learning text.", "2"],
        ["Shared Header", "2 Method", "Training method text.", "3"],
    ])
    parsed = parse_pdf(path)
    assert parsed.status == "ready"
    assert len(parsed.pages) == 3
    assert [section.kind for section in parsed.sections] == ["preamble", "abstract", "introduction", "method"]
    assert parsed.sections[-1].page_start == 3
    assert "Shared Header" not in parsed.full_text
    assert parsed.manifest["coverage"]["pages_extracted"] == 3

def test_parse_pdf_marks_image_only_document_needs_ocr(tmp_path: Path) -> None:
    parsed = parse_pdf(write_image_only_pdf(tmp_path / "scan.pdf"))
    assert parsed.status == "needs_ocr"
    assert parsed.full_text == ""
    assert "searchable_text_insufficient" in parsed.warnings

def test_parse_pdf_failure_is_not_silent(tmp_path: Path) -> None:
    path = tmp_path / "broken.pdf"
    path.write_bytes(b"not a pdf")
    parsed = parse_pdf(path)
    assert parsed.status == "failed"
    assert parsed.error
```

- [ ] **Step 2: Run parser tests and verify RED**

Run: `python -m pytest tests/test_pdf_parsing.py -q`

Expected: collection failure because `app.papers.parsing` does not exist.

- [ ] **Step 3: Add PyMuPDF dependency and immutable parser records**

Add `PyMuPDF>=1.24,<2` to `requirements.txt`. Define frozen dataclasses with serialization methods:

```python
@dataclass(frozen=True)
class ParsedBlock:
    block_type: str
    text: str
    bbox: tuple[float, float, float, float]
    reading_order: int

@dataclass(frozen=True)
class ParsedSection:
    section_id: str
    index: int
    kind: str
    title: str
    page_start: int
    page_end: int
    text: str
    char_start: int
    char_end: int
    text_hash: str

@dataclass(frozen=True)
class ParsedPaper:
    full_text: str
    pages: tuple[ParsedPage, ...]
    sections: tuple[ParsedSection, ...]
    metadata: Mapping[str, Any]
    manifest: Mapping[str, Any]
    status: str
    quality_score: float
    warnings: tuple[str, ...] = ()
    error: str | None = None
```

- [ ] **Step 4: Implement layout extraction and section reconstruction**

Use `page.get_text("dict", sort=True)`, normalize spans into blocks, detect repeated normalized top/bottom margin strings across pages, restore paragraph text with dehyphenation, detect headings using aliases plus numbered-heading/font signals, and compute stable page/section hashes. Return `needs_ocr` if fewer than 100 searchable characters occur on at least half of a multi-page document or if the whole document has fewer than 100 searchable characters.

Implement `parse_pdf_legacy` with the existing pypdf page concatenation and no fixed length cap. Tag its manifest as `legacy_fixed` version `1`.

- [ ] **Step 5: Run parser tests and verify GREEN**

Run: `python -m pytest tests/test_pdf_parsing.py -q`

Expected: all parser tests pass.

- [ ] **Step 6: Commit parser unit**

```bash
git add requirements.txt app/papers/parsing.py tests/test_pdf_parsing.py
git commit -m "feat: add structured PDF parser"
```

---

### Task 2: Structure-aware hierarchical chunking with legacy compatibility

**Files:**
- Modify: `app/papers/chunking.py`
- Modify: `tests/test_paper_chunking.py`

**Interfaces:**
- Consumes: `Sequence[ParsedSection]`, paper title, maximum characters, and overlap characters.
- Produces: `chunk_sections(...) -> list[ChunkDraft]` with raw content, section/page provenance, offsets, and `embedding_text(title)`.
- Preserves: `chunk_text(...)` output and semantics under `legacy_fixed`.

- [ ] **Step 1: Write failing chunking tests**

```python
def test_structure_aware_chunks_never_cross_sections() -> None:
    chunks = chunk_sections((intro_section(), method_section()), max_chars=120, overlap_chars=30)
    assert {chunk.section_id for chunk in chunks} == {"introduction", "method"}
    assert all("Introduction body" not in c.content or c.section_id == "introduction" for c in chunks)

def test_long_paragraph_splits_on_complete_sentences() -> None:
    chunks = chunk_sections((section("First sentence. Second sentence. Third sentence."),), 32, 16)
    assert chunks[0].content.endswith(".")
    assert all(not chunk.content.startswith("entence") for chunk in chunks)

def test_embedding_context_does_not_change_raw_content() -> None:
    chunk = chunk_sections((section("Raw original text."),), 100, 0)[0]
    assert chunk.content == "Raw original text."
    assert chunk.embedding_text("Paper title").startswith("Paper: Paper title\nSection:")
```

Keep the existing stability tests for `chunk_text` unchanged.

- [ ] **Step 2: Run chunk tests and verify RED**

Run: `python -m pytest tests/test_paper_chunking.py -q`

Expected: failure because `chunk_sections` and provenance fields do not exist.

- [ ] **Step 3: Extend `ChunkDraft` and implement hierarchical splitting**

Add optional `section_id`, `section_path`, `page_start`, `page_end`, `char_start`, and `char_end` fields with backward-compatible defaults. Implement paragraph packing, multilingual sentence splitting, punctuation/whitespace fallback, and whole-sentence overlap. Exclude sections with kinds `references`, `acknowledgments`, `header`, and `footer` from the default retrieval corpus.

```python
def embedding_text(self, paper_title: str) -> str:
    section = self.section_path or self.section_id or "Document"
    return f"Paper: {paper_title}\nSection: {section}\n\n{self.content}"
```

- [ ] **Step 4: Run chunk tests and verify GREEN**

Run: `python -m pytest tests/test_paper_chunking.py -q`

Expected: new structure-aware tests and unchanged legacy tests pass.

- [ ] **Step 5: Commit chunking unit**

```bash
git add app/papers/chunking.py tests/test_paper_chunking.py
git commit -m "feat: add structure-aware paper chunking"
```

---

### Task 3: PostgreSQL content graph migration and repository persistence

**Files:**
- Create: `alembic/versions/20260717_0002_structured_pdf_content.py`
- Modify: `app/papers/models.py`
- Modify: `app/papers/repository.py`
- Modify: `tests/test_paper_repository.py`

**Interfaces:**
- Consumes: `ParsedPaper`, `Sequence[ChunkDraft]`, strategy/version fields.
- Produces: atomic `replace_content(..., parsed=..., parser_name=..., parser_version=..., chunk_strategy=..., chunker_version=...)`.

- [ ] **Step 1: Write failing repository contract tests**

Add a recording session test that asserts SQL and parameters include parser manifest, page rows, section rows, and chunk provenance. Assert `list_documents` returns parse status/quality/coverage.

```python
assert params["parse_status"] == "ready"
assert page_insert["page_number"] == 1
assert section_insert["section_id"] == "introduction"
assert chunk_insert["section_id"] == "introduction"
assert chunk_insert["page_start"] == 1
```

- [ ] **Step 2: Run repository tests and verify RED**

Run: `python -m pytest tests/test_paper_repository.py -q`

Expected: repository signature and persistence assertions fail.

- [ ] **Step 3: Add additive Alembic migration**

Alter `paper_contents` with parser/chunker fields, parse status, and manifest JSONB. Create tenant-scoped `paper_pages` and `paper_sections`, indexes, RLS policies, and foreign keys to `paper_contents`. Alter `paper_chunks` with `section_id`, `char_start`, and `char_end`. Downgrade drops new columns/tables/policies without touching existing content tables.

- [ ] **Step 4: Extend content domain model and repository writes**

Add parse strategy/version/status fields to `ContentVersion`. In one transaction insert `paper_contents`, all pages, all sections, and all chunks, then advance `papers.current_content_version`. Populate `language`, `extraction_quality`, existing chunk page/path fields, and new provenance fields.

- [ ] **Step 5: Expose parse diagnostics in document reads**

Select `pc.parse_status`, strategy/version fields, `pc.extraction_quality`, `pc.parse_manifest`, and counts for pages/sections. Return them under a bounded `parsing` object without returning layout blocks in the list endpoint.

- [ ] **Step 6: Run repository tests and verify GREEN**

Run: `python -m pytest tests/test_paper_repository.py -q`

Expected: repository unit tests pass.

- [ ] **Step 7: Commit persistence unit**

```bash
git add alembic/versions/20260717_0002_structured_pdf_content.py app/papers/models.py app/papers/repository.py tests/test_paper_repository.py
git commit -m "feat: persist structured paper content"
```

---

### Task 4: Strategy configuration and central ingestion integration

**Files:**
- Modify: `app/config.py`
- Modify: `.env.example`
- Modify: `docker-compose.yml`
- Modify: `app/papers/ingestion.py`
- Modify: `app/services/rag_service.py`
- Modify: `app/routes/knowledge.py`
- Modify: `app/services/institutional_access/service.py`
- Modify: `tests/test_paper_ingestion.py`
- Modify: `tests/test_postgres_config.py`

**Interfaces:**
- Produces: settings `pdf_parse_strategy` and normalized `rag_chunk_strategy`.
- Consumes: PDF assets from web upload and institutional acquisition.

- [ ] **Step 1: Write failing ingestion and configuration tests**

Test that a PDF file uses `structure_aware_v1`, a manual edit does not reparse its file, `needs_ocr` produces no embedding call, raw chunks are persisted while contextualized strings are embedded, and `legacy_fixed` remains selectable.

```python
result = await service.ingest("t", "u", pdf_paper)
assert result.parse_status == "ready"
assert embedding.texts[0].startswith("Paper: Paper\nSection:")
assert repository.saved_chunks[0].content == "Raw PDF paragraph."

result = await service.ingest("t", "u", scanned_pdf)
assert result.embedding_status == "not_indexed"
assert embedding.calls == 0
```

- [ ] **Step 2: Run ingestion/config tests and verify RED**

Run: `python -m pytest tests/test_paper_ingestion.py tests/test_postgres_config.py -q`

Expected: missing settings, parse result, and strategy behavior fail.

- [ ] **Step 3: Add strategy settings**

Add `SCHOLAR_PDF_PARSE_STRATEGY=structure_aware_v1` and set `SCHOLAR_RAG_CHUNK_STRATEGY=structure_aware_v1` in defaults, `.env.example`, and backend/worker/mcp Docker environments. Validate against `legacy_fixed` and `structure_aware_v1`, falling back to the recommended default for invalid values.

- [ ] **Step 4: Centralize PDF parsing in ingestion**

When the paper has a PDF asset and is not marked `updated_from=inline_text_editor`, parse it using the selected strategy, replace `full_text` and deterministic missing DOI/arXiv metadata, derive chunks using the selected chunker, and persist the parsed graph. Manual content uses a synthetic one-section parsed document so structure-aware chunking remains available without re-reading the PDF.

Return `parse_status`, warnings, parser strategy, and chunk strategy in `IngestionResult`. For `needs_ocr` or `failed`, save paper/asset status and do not call Qwen embedding.

- [ ] **Step 5: Remove lossy route-level PDF parsing**

The upload and institutional acquisition paths pass the original file plus any user-supplied text into central ingestion. Remove the `[:50000]` PDF limit and the route DTO `max_length=50000` restriction. Keep legacy helper functions only where the selected legacy strategy calls them; do not maintain three different PDF extractors.

- [ ] **Step 6: Embed contextualized text only**

Pass `[chunk.embedding_text(paper.title) for chunk in chunks]` to Qwen. Persist and return `chunk.content` unchanged.

- [ ] **Step 7: Run ingestion/config tests and verify GREEN**

Run: `python -m pytest tests/test_paper_ingestion.py tests/test_postgres_config.py tests/test_auth_routes_and_knowledge.py tests/test_institutional_access.py -q`

Expected: all selected tests pass.

- [ ] **Step 8: Commit ingestion unit**

```bash
git add app/config.py .env.example docker-compose.yml app/papers/ingestion.py app/services/rag_service.py app/routes/knowledge.py app/services/institutional_access/service.py tests/test_paper_ingestion.py tests/test_postgres_config.py tests/test_auth_routes_and_knowledge.py tests/test_institutional_access.py
git commit -m "feat: integrate structured PDF ingestion"
```

---

### Task 5: Retrieval provenance and paper parse diagnostics

**Files:**
- Modify: `app/retrieval/models.py`
- Modify: `app/retrieval/repository.py`
- Modify: `app/retrieval/service.py`
- Modify: `tests/test_retrieval_service.py`
- Modify: `tests/test_hybrid_retrieval.py`

**Interfaces:**
- Produces: retrieval hits containing section ID/path and page range alongside complete raw chunk content.
- Preserves: current legacy response keys and complete chunk display behavior.

- [ ] **Step 1: Write failing provenance tests**

```python
assert response.items[0].section_id == "method"
assert response.items[0].section_path == "3 Method"
assert response.items[0].page_start == 4
assert response.items[0].page_end == 5
assert response.to_legacy_dict()["items"][0]["chunk"] == COMPLETE_RAW_TEXT
```

- [ ] **Step 2: Run retrieval tests and verify RED**

Run: `python -m pytest tests/test_retrieval_service.py tests/test_hybrid_retrieval.py -q`

Expected: missing provenance fields fail.

- [ ] **Step 3: Select and map provenance**

Select `section_id`, `section_path`, `page_start`, and `page_end` in lexical/vector candidates, preserve them through fusion, and expose them in typed and legacy responses.

- [ ] **Step 4: Run retrieval tests and verify GREEN**

Run: `python -m pytest tests/test_retrieval_service.py tests/test_hybrid_retrieval.py -q`

Expected: retrieval tests pass and chunk content remains complete.

- [ ] **Step 5: Commit retrieval unit**

```bash
git add app/retrieval/models.py app/retrieval/repository.py app/retrieval/service.py tests/test_retrieval_service.py tests/test_hybrid_retrieval.py
git commit -m "feat: expose retrieval chunk provenance"
```

---

### Task 6: Reproducible legacy-versus-structured evaluation

**Files:**
- Create: `app/evaluation/__init__.py`
- Create: `app/evaluation/retrieval.py`
- Create: `scripts/compare_chunk_strategies.py`
- Create: `tests/test_retrieval_evaluation.py`
- Modify: `README.md`

**Interfaces:**
- Consumes: JSONL labeled queries, local PDF corpus mapping, named strategy functions, and one embedding/search adapter.
- Produces: versioned JSON report with per-query rankings and aggregate metrics.

- [ ] **Step 1: Write failing metric tests**

Use a small deterministic ranked fixture and assert exact metrics:

```python
ranked = ["irrelevant", "relevant-a", "relevant-b"]
relevant = {"relevant-a", "relevant-b"}
metrics = ranking_metrics(ranked, relevant, k=3)
assert metrics.recall == 1.0
assert metrics.precision == pytest.approx(2 / 3)
assert metrics.reciprocal_rank == 0.5
```

Test that comparison rejects mismatched corpus/query/embedding fingerprints and that unlabeled input produces `diagnostic_only: true` without recall/precision fields.

- [ ] **Step 2: Run evaluation tests and verify RED**

Run: `python -m pytest tests/test_retrieval_evaluation.py -q`

Expected: evaluation module does not exist.

- [ ] **Step 3: Implement metrics and report schema**

Implement Recall@K, Precision@K, MRR, and nDCG@K with explicit zero-relevance behavior. Hash normalized corpus IDs, query records, embedding model, parser version, and chunk configuration. Store complete returned chunk text plus paper/section/page provenance per query.

- [ ] **Step 4: Implement comparison CLI**

Support:

```powershell
python scripts/compare_chunk_strategies.py `
  --corpus-jsonl evaluation/corpus.jsonl `
  --queries-jsonl evaluation/queries.jsonl `
  --output evaluation/reports/chunk-comparison.json
```

Run both strategies in one process without writing content versions. Use the configured Qwen embedding client and the same ranking implementation for each strategy.

- [ ] **Step 5: Document label and report formats**

Add concise README examples for corpus JSONL, relevant paper/section/page labels, CLI invocation, and the rule that unlabeled rankings are not accuracy measurements.

- [ ] **Step 6: Run evaluation tests and verify GREEN**

Run: `python -m pytest tests/test_retrieval_evaluation.py -q`

Expected: all evaluation tests pass.

- [ ] **Step 7: Commit evaluation unit**

```bash
git add app/evaluation scripts/compare_chunk_strategies.py tests/test_retrieval_evaluation.py README.md
git commit -m "feat: compare paper chunking strategies"
```

---

### Task 7: Migration, regression, and runtime verification

**Files:**
- Modify only files required by failures discovered during verification.

**Interfaces:**
- Verifies the complete parser-to-retrieval pipeline and Docker runtime.

- [ ] **Step 1: Run focused test suite**

Run:

```powershell
python -m pytest `
  tests/test_pdf_parsing.py `
  tests/test_paper_chunking.py `
  tests/test_paper_repository.py `
  tests/test_paper_ingestion.py `
  tests/test_retrieval_service.py `
  tests/test_hybrid_retrieval.py `
  tests/test_retrieval_evaluation.py -q
```

Expected: all focused tests pass with no warnings caused by the new code.

- [ ] **Step 2: Run full backend regression suite**

Run: `python -m pytest -q`

Expected: all tests pass, or pre-existing unrelated failures are recorded with exact test names and evidence.

- [ ] **Step 3: Build affected Docker images**

Run: `docker compose build backend worker mcp_server migrate`

Expected: all images build successfully with PyMuPDF installed.

- [ ] **Step 4: Apply migration and start affected services**

Run: `docker compose up -d --build migrate backend worker mcp_server frontend`

Expected: migration exits 0; backend, worker, mcp server, and frontend become healthy/running.

- [ ] **Step 5: Perform API smoke test**

Upload a representative selectable-text PDF, fetch the knowledge record, and run a RAG query. Verify parse status `ready`, page/section counts are non-zero, `正文` receives complete `full_text`, and returned chunks contain section/page provenance. Upload an image-only fixture and verify `needs_ocr` with no indexed chunks.

- [ ] **Step 6: Inspect final diff and commit verification fixes**

Run: `git diff --check` and `git status --short`.

If verification required code fixes, stage each parser, ingestion, retrieval, or test file named by `git status --short`, verify the staged diff, and commit them with `git commit -m "fix: stabilize structured PDF ingestion"`. Do not stage unrelated user changes.
