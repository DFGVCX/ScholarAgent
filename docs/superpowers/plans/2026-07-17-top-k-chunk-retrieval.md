# Top-K Chunk Retrieval Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Return the globally highest-ranked K chunks and label every chunk with its source paper.

**Architecture:** PostgreSQL continues to produce lexical and vector candidates per chunk, and RRF continues to fuse by `chunk_id`. The service removes paper-level deduplication, carries `chunk_index` through the API, and the web console renders canonical hit fields.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, PostgreSQL/pgvector, unittest, static HTML/JavaScript.

## Global Constraints

- `limit=K` means at most K chunks, not K distinct papers.
- Multiple chunks from the same paper are allowed.
- Every hit preserves paper source metadata and citation identifiers.
- No database migration is required.

---

### Task 1: Return global Top-K chunks

**Files:**
- Modify: `app/retrieval/models.py`
- Modify: `app/retrieval/repository.py`
- Modify: `app/retrieval/service.py`
- Test: `tests/test_retrieval_service.py`

**Interfaces:**
- Consumes: `RetrievalCandidate` rows produced by lexical/vector repository queries.
- Produces: `LocalHit` objects containing `chunk_id`, `chunk_index`, `snippet`, ranks, and paper metadata.

- [x] **Step 1: Write the failing fusion test**

Add two candidates with different `chunk_id` values but the same `paper_uuid`, then assert both appear when `limit=2`.

```python
hits = RetrievalService._fuse(
    [_candidate("a", "p1", 0.9, chunk_index=0), _candidate("b", "p1", 0.8, chunk_index=1)],
    [],
    2,
)
self.assertEqual([hit.chunk_id for hit in hits], ["a", "b"])
self.assertEqual([hit.chunk_index for hit in hits], [0, 1])
```

- [x] **Step 2: Run the test and confirm the old paper deduplication fails it**

Run: `python -m unittest tests.test_retrieval_service -v`

Expected: FAIL because only one hit survives for paper `p1`.

- [x] **Step 3: Carry chunk indexes and remove paper-level deduplication**

Add `chunk_index: int` to `RetrievalCandidate` and `LocalHit`, select `c.chunk_index` in both repository queries, map it in `_candidate`, and remove the `seen_papers` guard from `_fuse`.

- [x] **Step 4: Run backend retrieval tests**

Run: `python -m unittest tests.test_retrieval_service tests.test_embedding_lifecycle -v`

Expected: PASS, with Top K measured in chunks.

### Task 2: Render chunk-level diagnostics

**Files:**
- Modify: `frontend/dist/app.html`
- Test: `tests/test_model_settings_ui.py`

**Interfaces:**
- Consumes: `items[]` containing `chunk_id`, `chunk_index`, `snippet`, `score`, `lexical_rank`, `vector_rank`, and paper metadata.
- Produces: a verification table with one row per chunk and an explicit source-paper column.

- [x] **Step 1: Write the failing UI contract test**

```python
self.assertIn("item.snippet", self.html)
self.assertIn("item.chunk_id", self.html)
self.assertIn("item.chunk_index", self.html)
self.assertIn("item.lexical_rank", self.html)
self.assertIn("item.vector_rank", self.html)
```

- [x] **Step 2: Run the test and confirm obsolete field names fail it**

Run: `python -m unittest tests.test_model_settings_ui -v`

Expected: FAIL because the page reads `content`, `lexical_score`, and `vector_score`.

- [x] **Step 3: Update both RAG result renderers**

Read `snippet`, `chunk_id`, `chunk_index`, `lexical_rank`, and `vector_rank`; display the paper title as source metadata rather than the result unit.

- [x] **Step 4: Run focused and live verification**

Run: `python -m unittest tests.test_retrieval_service tests.test_model_settings_ui -v`

Then rebuild Backend and Frontend and query `联邦学习是什么` with `limit=6`. Expected: up to six chunk rows, including multiple rows from the same paper when ranked in the Top K.
