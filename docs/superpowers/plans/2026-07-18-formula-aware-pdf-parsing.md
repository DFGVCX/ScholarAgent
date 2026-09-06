# Formula-Aware PDF Parsing Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a comparison-safe `formula_aware_v2` parser that recovers formula glyphs, preserves formula provenance, and prevents equations from being split across RAG chunks.

**Architecture:** PyMuPDF remains the layout authority and pypdf is a targeted glyph-recovery fallback. Formula detection and rendering live in a focused parsing module, while the existing parser orchestrates page extraction and the chunker recognizes fenced display-math paragraphs as atomic units.

**Tech Stack:** Python 3.11, PyMuPDF, pypdf, dataclasses, unittest/pytest, PostgreSQL/pgvector, Docker Compose.

## Global Constraints

- Preserve `legacy_fixed` and `structure_aware_v1` for evaluation.
- Do not silently invent mathematical meaning or claim high-confidence LaTeX conversion.
- Preserve full raw chunk content and source page/bounding-box provenance.
- Do not allow non-whitespace C0 control characters into persisted text.

---

### Task 1: Formula recovery primitives

**Files:**
- Create: `app/papers/formulas.py`
- Test: `tests/test_formula_parsing.py`

**Interfaces:**
- Consumes: PyMuPDF `ParsedBlock`-like text/bbox values and optional pypdf page text.
- Produces: `FormulaCandidate`, `contains_invalid_controls(text)`, `recover_formula_text(raw_text, fallback_text, label)`, and `render_formula_markdown(candidate)`.

- [ ] **Step 1: Write failing tests** covering C0 detection, label-scoped pypdf recovery, Markdown delimiters, and conservative fallback.
- [ ] **Step 2: Run `python -m pytest tests/test_formula_parsing.py -q`** and verify failures because the module does not exist.
- [ ] **Step 3: Implement the minimal recovery primitives** with explicit dataclass fields for raw text, recovered text, label, source, and confidence.
- [ ] **Step 4: Run `python -m pytest tests/test_formula_parsing.py -q`** and expect all formula primitive tests to pass.

### Task 2: Formula-aware parser strategy

**Files:**
- Modify: `app/papers/parsing.py`
- Modify: `app/papers/ingestion.py`
- Modify: `app/config.py`
- Modify: `docker-compose.yml`
- Test: `tests/test_pdf_parsing.py`
- Test: `tests/test_postgres_config.py`
- Test: `tests/test_paper_ingestion.py`

**Interfaces:**
- Consumes: formula primitives and both PDF extractors.
- Produces: `parse_pdf_formula_aware(path: Path) -> ParsedPaper`, parser manifest `equations`, and default strategy `formula_aware_v2`.

- [ ] **Step 1: Write failing parser/config tests** proving equation blocks are grouped, controls are removed, the manifest has provenance, and older strategies remain selectable.
- [ ] **Step 2: Run the focused tests** and verify the new parser/default assertions fail.
- [ ] **Step 3: Implement page fallback extraction, equation grouping, manifest serialization, and strategy routing.** Keep `parse_pdf` as the existing v1 API and add a separate v2 entry point.
- [ ] **Step 4: Run the focused tests** and expect them to pass without modifying legacy baseline expectations.

### Task 3: Equation-atomic structured chunking

**Files:**
- Modify: `app/papers/chunking.py`
- Test: `tests/test_paper_chunking.py`

**Interfaces:**
- Consumes: section text containing display math delimited by `$$`.
- Produces: chunks in which every display-math block is wholly contained in one chunk.

- [ ] **Step 1: Write a failing chunking test** with a formula longer than the normal sentence split boundary and nearby explanatory prose.
- [ ] **Step 2: Run `python -m pytest tests/test_paper_chunking.py -q`** and confirm at least one chunk contains only a partial formula.
- [ ] **Step 3: Teach `_section_units` to recognize complete `$$...$$` spans as atomic units** and permit an atomic unit to exceed `max_chars` rather than splitting it.
- [ ] **Step 4: Run chunking tests** and verify formula delimiters are balanced in every chunk.

### Task 4: Evaluation strategy and end-to-end verification

**Files:**
- Modify: `app/evaluation/retrieval.py`
- Test: `tests/test_retrieval_evaluation.py`
- Modify: `docs/operations/POSTGRES_PGVECTOR_MIGRATION.md` if the existing operations guide contains parser strategy instructions.

**Interfaces:**
- Consumes: all three parser strategies.
- Produces: evaluation reports that can compare `legacy_fixed`, `structure_aware_v1`, and `formula_aware_v2`.

- [ ] **Step 1: Add a failing evaluation test** asserting all three strategies are accepted and reported.
- [ ] **Step 2: Add v2 evaluation routing** while preserving previous metrics and report schema.
- [ ] **Step 3: Run the complete focused Python test suite** for parsing, chunking, ingestion, configuration, repository, and retrieval evaluation.
- [ ] **Step 4: Rebuild backend/worker containers, reingest the supplied PDF, and inspect equation (2), parser manifest, chunks, and RAG results.**
- [ ] **Step 5: Open the local website and verify the parsed body shows coherent formula Markdown and RAG returns a complete formula-containing chunk with paper/section/page provenance.**

## Self-review

- Spec coverage: every acceptance criterion maps to Tasks 1–4.
- Placeholder scan: no TBD/TODO placeholders are present.
- Type consistency: v2 routing consistently uses `formula_aware_v2`; equations are manifest records and display math is represented by balanced `$$` delimiters.
