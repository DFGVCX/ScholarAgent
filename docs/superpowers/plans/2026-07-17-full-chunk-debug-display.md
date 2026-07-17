# Full Chunk Debug Display Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Show every stored character of each retrieved chunk in the RAG verification UI.

**Architecture:** Preserve the existing chunk-level retrieval contract, but remove preview truncation at both boundaries. `RetrievalService` returns the complete candidate content as `snippet`, and the verification table escapes and renders that entire value inside its scrollable result panel.

**Tech Stack:** Python 3.12, unittest, static HTML/JavaScript, Docker Compose.

## Global Constraints

- Do not change chunk creation, overlap, ranking, or Top-K behavior.
- Do not truncate `snippet` on the server or client.
- Continue HTML-escaping chunk content before rendering.

---

### Task 1: Return complete chunk content

**Files:**
- Modify: `app/retrieval/service.py`
- Test: `tests/test_retrieval_service.py`

**Interfaces:**
- Consumes: `RetrievalCandidate.content: str`
- Produces: `LocalHit.snippet: str` containing the complete candidate content

- [ ] **Step 1: Write the failing test**

Create a candidate whose content is longer than 1200 characters and assert the result snippet equals the entire content.

```python
long_content = "x" * 1500
candidate = replace(_candidate("a", "p1", 1.0), content=long_content)
hit = RetrievalService._fuse([candidate], [], 1)[0]
self.assertEqual(hit.snippet, long_content)
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m unittest tests.test_retrieval_service.RetrievalServiceTest.test_hit_snippet_preserves_complete_chunk -v`

Expected: FAIL because the current service returns 1200 of 1500 characters.

- [ ] **Step 3: Remove server-side truncation**

Set `snippet=candidate.content` when constructing `LocalHit`.

- [ ] **Step 4: Run the backend test and verify it passes**

Run: `python -m unittest tests.test_retrieval_service -v`

Expected: PASS.

### Task 2: Render complete snippet text

**Files:**
- Modify: `frontend/dist/app.html`
- Test: `tests/test_model_settings_ui.py`

**Interfaces:**
- Consumes: `item.snippet` from `/knowledge/rag/search`
- Produces: an escaped, untruncated chunk cell in the RAG verification table

- [ ] **Step 1: Write the failing UI contract test**

```python
self.assertIn("<div class=\"rag-verify-snippet\">${escapeHtml(item.snippet || '')}</div>", self.html)
self.assertNotIn("escapeHtml(item.snippet || '').slice(", self.html)
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m unittest tests.test_model_settings_ui.ModelSettingsUiTests.test_rag_console_renders_complete_chunk_text -v`

Expected: FAIL because the current UI calls `.slice(0, 600)`.

- [ ] **Step 3: Remove client-side truncation**

Render `${escapeHtml(item.snippet || '')}` directly in `.rag-verify-snippet`.

- [ ] **Step 4: Run focused regression tests**

Run: `python -m unittest tests.test_model_settings_ui tests.test_retrieval_service tests.test_embedding_lifecycle -v`

Expected: PASS, then rebuild `backend` and `frontend` and verify the served page contains no snippet slicing.
