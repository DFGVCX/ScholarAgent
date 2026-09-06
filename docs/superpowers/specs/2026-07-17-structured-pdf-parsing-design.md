# Structured PDF Parsing and Structure-Aware Chunking Design

## Goal

Replace the current lossy PDF ingestion path with an auditable, page- and section-aware parser while preserving the existing parser and chunker as a measurable baseline. Parsed full text remains available to the paper reader, and both strategies can be evaluated on the same corpus and labeled queries.

## Scope

This change covers backend PDF parsing, PostgreSQL persistence, chunk derivation, embedding input, retrieval provenance, and offline comparison metrics. It does not redesign the paper information card or add a mandatory OCR runtime. The existing reader remains compatible because the active parse still publishes `paper_contents.full_text`.

## Design Influences

The design adopts these DeepPaperNote ideas:

- canonical raw page and section records rather than an untraceable text blob;
- an explicit source manifest containing coverage, hashes, language, captions, and failure state;
- complete sections as the source of truth and chunks as derived retrieval artifacts;
- fail-closed behavior when a PDF has insufficient searchable text;
- persistent intermediate records that make parsing and retrieval debuggable.

It does not copy DeepPaperNote's parser verbatim. Its canonical text path still relies on `page.get_text("text")` and simple heading aliases. ScholarAgent will retain layout blocks and use their geometry to improve reading order and provenance.

## Considered Approaches

### Extend the existing pypdf path

This minimizes code changes, but cannot reliably preserve layout blocks, coordinates, section provenance, or scanned-page quality. It remains only as the legacy baseline.

### Local PyMuPDF structured parser

This is the selected approach. It provides page geometry, text blocks, font signals, images, and stable local execution without another service. It fits the current Docker deployment and can later feed an OCR fallback.

### External Docling or GROBID service

This may eventually improve difficult academic layouts and bibliography extraction, but introduces another large runtime and operational dependency. The parser interface will allow a future adapter without changing storage or chunking contracts.

## Strategy Model

Two named strategies remain available:

- `legacy_fixed`: the existing `pypdf` extraction and existing character/paragraph chunking behavior;
- `structure_aware_v1`: PyMuPDF layout parsing followed by section- and paragraph-aware chunking.

`SCHOLAR_PDF_PARSE_STRATEGY` selects the active parser. `SCHOLAR_RAG_CHUNK_STRATEGY` selects the active chunker. The defaults become `structure_aware_v1`, while `legacy_fixed` stays callable in code and evaluation tooling. Strategy names, parser version, and chunker version are persisted with each content version so results are reproducible.

The comparison runner executes both strategies directly. It does not require changing the active production strategy or overwriting a paper's current content version.

## Canonical Parser Contract

The new parser returns a `ParsedPaper` value containing:

- `full_text`: complete normalized text without a fixed character cap;
- `pages`: ordered `ParsedPage` records;
- `sections`: ordered `ParsedSection` records;
- `metadata`: deterministically extracted DOI, arXiv identifier, code/project URLs, title candidate, language hint, and PDF metadata;
- `manifest`: parser name/version, page coverage, section coverage, hashes, removed repeated margins, caption index, and warnings;
- `status`: `ready`, `needs_ocr`, or `failed`;
- `quality_score`: a number from 0 to 1.

Each page stores page number, normalized text, searchable character count, extraction method, quality status, and retained layout blocks. Each block stores type, bounding box, reading order, and text. Each section stores stable section ID, kind, title, page range, full text, character count, and text hash.

## Text Reconstruction

The parser uses `page.get_text("dict", sort=True)` and reconstructs text from text blocks rather than immediately flattening the page.

Processing order:

1. collect page dimensions, spans, font sizes, and text block bounding boxes;
2. normalize whitespace while retaining paragraph boundaries;
3. detect repeated short blocks in the top and bottom page bands and classify them as headers or footers;
4. exclude those repeated margin blocks from canonical body text while recording them in the manifest;
5. order full-width blocks and column blocks using their geometry;
6. repair line-end hyphenation when the next line continues a word;
7. recognize section headings using normalized aliases plus font, length, numbering, and isolation signals;
8. build complete page and section records;
9. detect captions, DOI, arXiv identifiers, and repository/project URLs;
10. compute hashes, coverage, warnings, and quality status.

The first version does not claim perfect formula or table reconstruction. Captions remain separate identifiable blocks, and their page/section provenance is retained.

## Quality and Failure Behavior

A PDF becomes `needs_ocr` when a material share of pages has too little searchable text. It becomes `failed` when it cannot be opened or produces no usable body text. These states preserve the paper record and original asset but do not create embeddings from empty or obviously incomplete content.

No parser exception is silently converted into an empty successful document. The ingestion result and paper record retain a bounded error message and parse manifest warning.

OCR is an adapter boundary, not a required dependency in this change. A later Tesseract, PaddleOCR, or remote OCR adapter can replace low-text page records and re-run sectioning without changing the database schema.

## PostgreSQL Model

Add the following persistence:

### `paper_contents`

- `parser_name`
- `parser_version`
- `chunk_strategy`
- `chunker_version`
- `parse_status`
- `parse_manifest JSONB`

Existing `language`, `extraction_method`, and `extraction_quality` columns are populated.

### `paper_pages`

- tenant/user/paper/content identity;
- page number;
- page text and text hash;
- extraction method and quality status;
- searchable character count;
- layout blocks as JSONB.

### `paper_sections`

- tenant/user/paper/content identity;
- stable section ID, section index, kind, and title;
- page range;
- complete text, character count, and hash.

### `paper_chunks`

Add `section_id`, `char_start`, and `char_end`. Populate existing `section_path`, `page_start`, and `page_end`. `content` always stores the unmodified retrieval excerpt shown to users.

Content versions remain immutable. Re-parsing creates a new version and atomically advances `papers.current_content_version` only after page, section, and chunk records have been written consistently.

## Structure-Aware Hierarchical Chunking

The name used in code and UI is **structure-aware chunking**. The algorithm is hierarchical because it observes this order:

1. document;
2. section;
3. paragraph;
4. sentence;
5. conservative hard split as the final fallback.

Rules:

- a chunk never crosses a section boundary;
- adjacent complete paragraphs in the same section are packed up to the configured maximum;
- an oversized paragraph is split on Chinese or English sentence boundaries;
- an oversized sentence is split on whitespace or punctuation before a hard character fallback;
- overlap consists of complete trailing sentences up to the overlap budget;
- references and repeated headers/footers are excluded from the default retrieval corpus but remain in canonical section/page storage;
- each chunk carries section ID/path, page range, source character offsets, content hash, position, and estimated token count;
- raw `content` is not prefixed or rewritten.

For vector generation only, the embedding input is contextualized as:

```text
Paper: <title>
Section: <section path>

<raw chunk content>
```

This improves semantic recall without changing the original chunk displayed in retrieval debugging.

## Legacy Compatibility

The existing `chunk_text(text, max_chars, overlap_chars)` behavior remains available under `legacy_fixed`. Tests that assert its current output remain intact. The new chunker has a separate entry point and data contract.

Existing uploaded and institutionally downloaded PDFs enter the new parser centrally during ingestion. Manually edited text is not silently replaced by re-parsing the attached PDF; it is recorded as a manual content version and can still use legacy or structure-aware plain-text section fallback.

The paper reader continues reading `paper_contents.full_text`, so a successfully parsed paper appears in the existing `正文` tab without waiting for the later UI redesign.

## Retrieval Evaluation

Add an offline comparison command that accepts a JSONL dataset. Each record contains:

```json
{"query":"联邦学习是什么","relevant":{"paper_id":"paper:pdf:...","section_ids":["introduction"],"page_ranges":[[1,2]]}}
```

For each strategy the runner parses and chunks the same PDFs, embeds with the same Qwen embedding model, and evaluates the same queries. It records:

- Recall@1, Recall@3, Recall@5, and Recall@10;
- Precision@K;
- MRR;
- nDCG@K when graded relevance is supplied;
- chunk count and average chunk length;
- relevant page/section coverage;
- parse failures and `needs_ocr` counts;
- per-query returned chunk text and provenance for manual error analysis.

The report includes strategy, parser/chunker versions, embedding model, configuration, corpus hash, and query-set hash. Comparisons are invalidated if the corpus, query set, or embedding model differs between strategies.

Because retrieval relevance must be labeled, the runner does not present unlabeled similarity scores as recall or accuracy. When no labels are available it emits a diagnostic ranking report only.

## API and Observability

Knowledge responses expose active parse status, parser/chunker strategy, quality score, coverage, section count, page count, and warnings. Retrieval rows already return complete chunk content and will additionally return section ID/path and page range.

Logs and errors must not include full paper text. They may include paper ID, content version, parser version, page counts, section counts, hashes, warnings, and bounded exception messages.

## Testing

Tests use generated PDFs and deterministic fixtures to cover:

- all pages are preserved without the old 50,000-character cap;
- section IDs and page ranges survive parsing, persistence, and retrieval;
- repeated headers and footers are removed from body text but recorded;
- two-column blocks have deterministic reading order;
- low-text PDFs become `needs_ocr` and are not embedded;
- invalid PDFs fail visibly rather than becoming metadata-only successes;
- structure-aware chunks do not cross sections;
- long paragraphs split at sentence boundaries;
- overlap contains complete sentences;
- raw chunk text remains unchanged while embedding input contains context;
- manual edits are not overwritten by attached-file parsing;
- legacy extraction and chunking remain callable and retain their established behavior;
- the comparison runner computes known Recall@K, Precision@K, MRR, and nDCG values from a small fixture.

Repository integration tests verify that a content version cannot expose partial pages, sections, or chunks after a failed transaction.

## Rollout

The database migration is additive. New ingestion defaults to `structure_aware_v1`; `legacy_fixed` remains selectable for rollback and experiments. Existing papers are not mutated automatically. A controlled re-parse operation can create new content versions after the new pipeline is verified on a small representative corpus.

The later frontend iteration will add the complete metadata card, structured section navigation, parse diagnostics, and an all-chunks inspection tab. This design already supplies the backend provenance required for that work.
