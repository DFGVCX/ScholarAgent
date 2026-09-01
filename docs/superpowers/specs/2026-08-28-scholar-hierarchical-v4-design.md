# Scholar Hierarchical v4 PDF Parsing and Chunking Design

## Goal

Add a production-oriented `scholar_hierarchical_v4` strategy for searchable academic PDFs. Docling provides the primary structured document model; ScholarAgent normalizes that output into its own parser contract, enriches it with existing PyMuPDF assets and formula fallbacks, and derives hierarchical retrieval chunks without removing the four historical strategies.

OCR and scanned-document support are outside this phase. The parser uses embedded PDF text and must fall back to `multimodal_aware_v3` when Docling is unavailable or fails.

## Design Principles

1. Canonical paper structure and derived retrieval chunks remain separate.
2. ScholarAgent owns the stable data contract; Docling types do not cross the adapter boundary.
3. Display text remains source-faithful while embedding text may add headings and local context.
4. Prose never crosses section boundaries.
5. Equations, figures, tables, and algorithms are typed semantic units.
6. A failed optional enrichment degrades explicitly and never discards usable text.
7. Existing parsing and chunking strategies remain selectable and behaviorally unchanged.

## Architecture

```text
PDF
 ├─ Docling adapter (primary, OCR disabled)
 │    └─ normalized ParsedPaper tree
 └─ multimodal_aware_v3 (fallback and asset enrichment)
          ↓
 Scholar ParsedPaper / Page / Section / Block
          ↓
 scholar_hierarchical_v4 chunker
          ├─ prose child chunks
          ├─ equation context chunks
          ├─ table row chunks with repeated headers
          ├─ figure caption/reference chunks
          └─ algorithm step chunks
          ↓
 paper_chunks + Qwen embeddings
```

## Parser Adapter

`app/papers/docling_adapter.py` contains the optional dependency boundary. Importing the rest of ScholarAgent must not require Docling to be installed. The adapter:

- converts a PDF with OCR disabled;
- enables embedded-text extraction, reading order, table structure, formula enrichment, picture classification, and heading hierarchy where supported;
- walks the Docling document in reading order;
- converts text, headings, lists, equations, tables, pictures, and code-like/algorithm items into `ParsedBlock` values;
- preserves page number, bounding box, reading order, labels, captions, Markdown/LaTeX, hierarchy, and source identifiers in metadata;
- constructs nested section paths and full-text rendering;
- returns structured warnings instead of swallowing partial failures.

The adapter accepts a converter dependency for tests so production code does not need to mock Docling internals.

## Fallback and Enrichment

`parse_pdf_hierarchical()` first attempts Docling. If it cannot import Docling, conversion raises, or the normalized result does not contain enough searchable text, it calls `parse_pdf_multimodal()` and records:

- requested strategy;
- actual parser;
- fallback reason;
- `parser_fallback` warning.

For a successful Docling parse, existing PyMuPDF visual extraction may enrich source crops without replacing Docling's canonical text hierarchy. Enrichment failures add warnings and leave the textual document usable.

## Hierarchical Chunk Contract

`ChunkDraft` gains optional fields:

- `chunk_type`;
- `parent_section_id`;
- `source_block_ids`;
- `context_before` and `context_after`;
- `embedding_content`;
- `metadata`.

`content` is the source-faithful material displayed to users. `embedding_content` is the contextual representation sent to the embedding model. If it is absent, the current title/section wrapper remains the fallback.

The database stores the new fields in explicit columns where they are queried frequently and JSONB for extensible provenance. Existing rows receive compatible defaults.

## Chunking Rules

### Prose

- Traverse sections in document order.
- Build units from paragraphs, list items, and complete sentences.
- Merge adjacent units only within the same heading path.
- Target 300–600 estimated tokens and cap at 800.
- Avoid overlap by default; when a single paragraph must split, carry one complete sentence.
- Do not index references, acknowledgments, repeated headers, or repeated footers as prose chunks.

### Equations

- Keep LaTeX/Markdown and equation number intact.
- Attach the nearest preceding definition sentence and following explanation sentence when they exist in the same section.
- Store source equation content unchanged and put explanatory context in embedding content.

### Tables

- Keep caption and header with every table chunk.
- Small tables remain atomic.
- Large tables split on complete rows under the token cap and repeat headers.
- Never invent cells when table structure is unavailable; use caption, recoverable text, and source crop metadata.

### Figures

- Index the figure label, caption, extractable figure text, and sentences that explicitly reference the label.
- Keep the image path and crop quality in provenance, not in the textual source body.

### Algorithms

- Keep label, caption/title, input, output, and ordered steps.
- Small algorithms remain atomic.
- Large algorithms split only between steps and repeat input/output context.

## Runtime Selection

Both `SCHOLAR_PDF_PARSE_STRATEGY` and `SCHOLAR_RAG_CHUNK_STRATEGY` accept `scholar_hierarchical_v4`. The new strategy is selectable first. It becomes the default only after adapter, persistence, ingestion, and real-PDF verification pass.

Re-ingestion creates a new content version and embeddings; it never mutates old chunks in place.

## API and UI Compatibility

Existing response keys remain. Structured content and retrieval hits may add:

- `chunk_type`;
- `parent_section_id`;
- `source_block_ids`;
- `provenance`.

Older frontends ignore these additive fields. The existing full Chunk display remains unchanged.

## Error Handling

- Missing Docling: explicit fallback, not ingestion failure.
- Docling conversion failure: explicit fallback with a sanitized reason.
- Low extracted text: fallback to v3; if v3 also has insufficient text, preserve `needs_ocr` without running OCR.
- Asset enrichment failure: keep structured text and mark affected blocks.
- Oversized atomic object: split only by type-specific boundaries; preserve the source object identity across children.
- Embedding failure: persist lexical content and existing failed-vector behavior.

## Testing

1. Adapter tests use fake Docling document objects for headings, prose, formula, table, figure, and algorithm mapping.
2. Fallback tests cover missing imports, conversion errors, and insufficient text.
3. Chunk tests cover section isolation, token budgets, equation context, repeated table headers, figure references, and algorithm step splitting.
4. Repository tests verify additive fields and tenant predicates.
5. Ingestion tests verify v4 parser/chunker selection and actual-parser provenance.
6. Regression tests prove the four existing strategies remain available and unchanged.
7. A searchable academic PDF is re-ingested and inspected through API and browser before the strategy becomes the default.

## Rollout

1. Add the optional adapter and contract tests.
2. Add hierarchical chunking and database fields.
3. Register v4 in configuration and ingestion.
4. Build the backend image with pinned Docling dependencies and cached model assets where practical.
5. Re-ingest the current test paper, inspect body and typed assets, and inspect complete retrieval chunks.
6. Switch the default parser/chunker to v4 only after the full verification gate passes.

## Non-goals

- OCR or scanned-document processing.
- LLM calls during deterministic parsing.
- Removing or rewriting historical strategies.
- Visual-language-model descriptions of figures.
- Reranking or retrieval evaluation changes in this phase.
