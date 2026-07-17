# Top-K Chunk Retrieval Design

## Goal

Return the globally highest-ranked `K` chunks from local RAG retrieval. A paper is source metadata for a chunk, not the unit of result deduplication.

## Selected approach

Rank and fuse lexical and vector candidates by `chunk_id`, then take the first `K` fused chunks without paper-level deduplication. Multiple chunks from the same paper are allowed when they independently rank in the global Top K.

Alternatives rejected:

- Paper-diversified retrieval keeps only one chunk per paper and hides useful supporting passages.
- Two-stage paper-then-chunk retrieval adds complexity and changes the meaning of Top K.

## API contract

Each local hit contains:

- `chunk_id` and `chunk_index`
- `snippet`, containing the matched chunk text
- `score`, `lexical_rank`, and `vector_rank`
- `paper_id`, `title`, authors, source, DOI/arXiv ID, URL, and publication date

`limit=K` means at most K chunks, not K distinct papers.

## UI

The RAG verification table displays chunk identity, matched text, fused score, lexical/vector rank, and the source paper title. It reads the canonical API fields rather than obsolete legacy names.

## Tests

- Fusion retains two ranked chunks from the same paper.
- Results stop at the requested chunk limit.
- Repository queries return `chunk_index`.
- The web console reads `snippet`, `lexical_rank`, and `vector_rank`.
