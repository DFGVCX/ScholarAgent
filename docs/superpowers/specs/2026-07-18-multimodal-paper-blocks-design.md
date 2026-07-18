# Multimodal Paper Blocks Design

## Goal

Extend the current formula-aware PDF pipeline into a multimodal parser that preserves paragraphs, equations, tables, figures, and algorithms as typed paper blocks. Keep every previous parser and chunking strategy available for retrieval evaluation. In the paper workbench, keep the original PDF view, render typed blocks in the body view, and replace the old edit tab with a dedicated figure/table/algorithm browser.

## Compatibility

- Add `multimodal_aware_v3`; do not change the behavior or identifiers of `legacy_fixed`, `structure_aware_v1`, or `formula_aware_v2`.
- Continue storing canonical full text, pages, sections, and chunks in PostgreSQL.
- Reuse the existing `paper_pages.blocks` JSONB column for typed block metadata; no new database table is required.
- Keep equations atomic and preserve the existing formula recovery manifest.
- Failed or uncertain visual extraction must keep textual evidence and expose a quality state instead of silently dropping the block.

## Typed Block Contract

Every page block keeps the existing fields and may add a `metadata` object. Supported block types are `body`, `equation`, `figure`, `table`, and `algorithm`.

Visual block metadata uses these stable fields:

- `label`: original paper identifier such as `Figure 2`, `Table 1`, or `Algorithm 1`.
- `caption`: source caption text.
- `markdown`: searchable/renderable representation. Tables use Markdown rows; algorithms use fenced pseudocode or preserved source lines.
- `asset_name`: safe file name under the PDF-local derived asset directory.
- `quality_status`: `usable`, `review`, or `rejected`.
- `quality_reasons`: explicit extraction warnings.
- `source_bbox`: source-region coordinates in PDF points.

The block `text` is always a useful text fallback and retrieval input. A missing crop never makes `text` empty.

## Extraction

`multimodal_aware_v3` first runs the existing layout and formula recovery logic. It then detects Figure, Table, Scheme, and Algorithm captions. It uses PyMuPDF page geometry to associate captions with nearby visual regions and renders candidate regions as PNG files at 200 DPI.

Tables prefer PyMuPDF's table detector when available. Extracted cells are normalized into Markdown while the original rendered table is retained as visual evidence. If cell extraction fails, the table remains a caption-backed block with a crop and `review` status.

Figures retain the caption and a caption-anchored page crop. Algorithms retain the complete candidate crop and source text; the parser does not claim semantic reconstruction when line extraction is damaged.

Derived files live beside the uploaded PDF in `<pdf-stem>_assets/`. The API exposes only manifest-referenced files inside that tenant-owned directory.

## Retrieval

The canonical paper and sections remain unchanged. Under the v3 chunk strategy, equations, tables, figures, and algorithms become atomic retrieval units with their label, caption, Markdown/text representation, section, page, and source provenance. Normal prose continues to use section-aware chunking. Atomic units may exceed the nominal chunk size rather than being split.

## API

Add a read-only structured-content endpoint returning the current content version's pages, blocks, sections, and parser manifest. Add a protected asset endpoint for visual block PNG files. Both endpoints use the existing tenant/user authentication and path-containment checks.

## Frontend

- `原文`: unchanged PDF/original-file viewer.
- `正文`: fetch structured content and render typed blocks in reading order. Equations use KaTeX; tables render as Markdown tables; figures and algorithms show their label, caption, extracted text, quality state, and crop when available.
- `图表算法`: replaces the old edit tab and shows only `figure`, `table`, and `algorithm` blocks, grouped by page with type filters and full source evidence.
- If v3 data is unavailable, both views fall back to the current full-text Markdown rendering.

## Verification

- Unit tests cover caption classification, table-to-Markdown conversion, typed block serialization, atomic multimodal chunks, structured-content queries, and asset-path protection.
- Existing formula, PDF, ingestion, repository, and retrieval tests remain green.
- Docker verification re-ingests the supplied federated-learning PDF and checks the API payload and browser rendering for equations plus any detected figures/tables/algorithms.
