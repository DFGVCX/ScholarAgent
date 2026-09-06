# Formula-Aware PDF Parsing Design

## Objective

Improve mathematical formula extraction without regressing two-column reading order, provenance, or the ability to compare the existing parsing baselines. Formula text must remain debuggable and searchable, while a normalized Markdown/LaTeX representation may be used for display when recovery is trustworthy.

## Current failure

The current `structure_aware_v1` parser uses PyMuPDF layout blocks. In the reported paper, embedded math fonts map operators such as summation and distribution symbols to C0 control characters. PyMuPDF also emits numerator, denominator, limits, and equation labels as separate geometric blocks. The current parser joins each block independently and renders blocks as paragraphs, which produces square glyphs and vertically fragmented equations.

`pypdf` recovers more Unicode math glyphs from the same page, but its whole-page reading order is worse for two-column papers. Therefore it is a recovery source rather than the primary layout parser.

## Strategy and compatibility

- Preserve `legacy_fixed` and `structure_aware_v1` unchanged as comparison baselines.
- Add a new default strategy named `formula_aware_v2`.
- Keep PyMuPDF as the authoritative source for page geometry, columns, headings, sections, captions, and bounding boxes.
- Read the same page with pypdf only when formula-like PyMuPDF blocks contain suspicious control characters or an equation label.
- Detect a display-equation group from an equation label, short math-heavy neighboring blocks, column alignment, and vertical proximity.
- Merge the group into one atomic `equation` block. Never split a single equation across retrieval chunks.

## Dual representation

Each recovered equation is represented in the parse manifest with:

- `label`: equation number when present.
- `page_number` and `bbox`: source provenance.
- `raw_text`: sanitized PyMuPDF extraction for debugging.
- `markdown`: normalized display form using `$$` delimiters when recovery succeeds.
- `recovery_source`: `pypdf_page_text` or `pymupdf`.
- `confidence`: `high`, `medium`, or `low`.

The paper full text uses the Markdown representation for a recovered equation but does not discard the raw representation from the manifest. Low-confidence recovery remains readable plain text and is never presented as confidently reconstructed LaTeX.

## Retrieval chunking

- Equation Markdown is an atomic text unit.
- The nearest explanatory prose before and after the equation stays eligible to share the same chunk.
- If the combined context exceeds the configured size, ordinary prose can move to adjacent chunks, but the formula itself is never hard-split.
- Embedding input includes paper title, section title, formula, and nearby prose. Raw chunk content remains unchanged so retrieval debugging shows exactly what was indexed.

## Formula normalization boundary

This version performs conservative deterministic normalization only: removing invalid controls, recovering Unicode operators, joining pypdf formula lines, and emitting Markdown math delimiters. It does not claim general PDF-to-LaTeX conversion. A future optional enhancement can use the stored page crop and equation candidate to reconstruct a small set of essential LaTeX formulas with model/OCR assistance and explicit confidence review, following DeepPaperNote's evidence-first approach.

## Frontend behavior

The parsed-text view must preserve formula Markdown verbatim so users can inspect and edit it. Math rendering is progressive enhancement: unsupported or low-confidence math remains visible source text rather than disappearing. This change focuses first on correct stored content and atomic retrieval; full KaTeX/MathJax rendering can be added without reparsing because the canonical Markdown is already stored.

## Acceptance criteria

1. No non-whitespace C0 control characters reach `full_text`, page text, sections, chunks, or PostgreSQL.
2. A fragmented numbered formula is emitted as one `equation` block with page/bbox provenance.
3. When pypdf has better glyphs, the equation contains recovered symbols such as `∑` rather than square placeholders.
4. Formula Markdown is never split inside a retrieval chunk.
5. Existing `legacy_fixed` and `structure_aware_v1` remain selectable for evaluation.
6. The supplied federated-learning PDF can be reingested and inspected in the website with coherent formula text.
