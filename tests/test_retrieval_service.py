from __future__ import annotations

from dataclasses import replace
import unittest

from app.retrieval.embedding import EmbeddingUnavailable
from app.retrieval.models import (
    ContextChunk,
    ContextWindowRequest,
    ParentContextRequest,
    ParentSectionContext,
    RetrievalCandidate,
    RetrievalRequest,
)
from app.retrieval.service import RetrievalService, reciprocal_rank_fusion


def _candidate(
    chunk_id: str, paper_id: str, score: float, chunk_index: int = 0
) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        chunk_index=chunk_index,
        paper_uuid=f"uuid-{paper_id}",
        paper_id=paper_id,
        title=f"Title {paper_id}",
        authors=("Author",),
        content=f"Evidence from {paper_id}",
        source="local",
        doi=None,
        arxiv_id=None,
        canonical_url=None,
        published_at=None,
        score=score,
    )


class _Repository:
    def __init__(self):
        self.embedding_model = None

    async def lexical_candidates(self, request):
        return [_candidate("a", "p1", 0.9), _candidate("b", "p2", 0.8)]

    async def vector_candidates(self, request, vector, embedding_model):
        self.embedding_model = embedding_model
        return [_candidate("b", "p2", 0.95), _candidate("c", "p3", 0.7)]


class _Embedding:
    model = "Qwen3-Embedding-4B"

    async def embed(self, texts):
        return [[1.0] + [0.0] * 1023]


class _BrokenEmbedding:
    model = "Qwen3-Embedding-4B"

    async def embed(self, texts):
        raise EmbeddingUnavailable("offline")


class RetrievalServiceTest(unittest.IsolatedAsyncioTestCase):
    async def test_parent_context_returns_complete_section(self) -> None:
        parent = ParentSectionContext(
            center_chunk_id="center",
            section_id="method",
            title="2 Method",
            kind="method",
            section_path="2 Method > 2.1 Setup",
            page_start=2,
            page_end=4,
            content="full parent section" * 50,
            character_count=950,
            estimated_tokens=238,
            paper_id="paper-1",
            content_version=4,
        )

        class _ParentRepository(_Repository):
            async def parent_context(self, request):
                return parent

        result = await RetrievalService(_ParentRepository(), _Embedding()).parent_context(
            ParentContextRequest("tenant", "user", "center")
        )

        self.assertEqual(result.content, parent.content)
        self.assertEqual(result.to_dict()["center_chunk_id"], "center")
        self.assertEqual(result.to_dict()["paper_id"], "paper-1")

    def test_context_window_keeps_whole_chunks_within_budget(self) -> None:
        chunks = [
            ContextChunk("before-2", 0, "older", 3, "method"),
            ContextChunk("before-1", 1, "before", 4, "method"),
            ContextChunk("center", 2, "center evidence", 8, "method"),
            ContextChunk("after-1", 3, "after", 5, "method"),
            ContextChunk("after-2", 4, "newer", 6, "method"),
        ]

        response = RetrievalService._budget_context(
            ContextWindowRequest(
                "tenant", "user", "center", before=2, after=2, token_budget=17
            ),
            chunks,
        )

        self.assertEqual(
            [chunk.chunk_id for chunk in response.chunks],
            ["before-1", "center", "after-1"],
        )
        self.assertEqual(response.total_tokens, 17)
        self.assertFalse(response.budget_exceeded)
        self.assertTrue(response.truncated)
        self.assertEqual(response.center_chunk_id, "center")

    def test_context_window_never_truncates_center_chunk(self) -> None:
        chunks = [ContextChunk("center", 7, "x" * 500, 120, "results")]

        response = RetrievalService._budget_context(
            ContextWindowRequest("tenant", "user", "center", token_budget=32),
            chunks,
        )

        self.assertEqual(response.chunks[0].content, "x" * 500)
        self.assertEqual(response.total_tokens, 120)
        self.assertTrue(response.budget_exceeded)
        self.assertFalse(response.chunks[0].truncated)

    def test_context_window_can_request_center_only(self) -> None:
        chunks = [
            ContextChunk("before", 0, "before", 3),
            ContextChunk("center", 1, "center", 4),
            ContextChunk("after", 2, "after", 3),
        ]

        response = RetrievalService._budget_context(
            ContextWindowRequest("tenant", "user", "center", before=0, after=0),
            chunks,
        )

        self.assertEqual([chunk.chunk_id for chunk in response.chunks], ["center"])

    def test_context_window_never_skips_an_oversized_immediate_neighbor(self) -> None:
        chunks = [
            ContextChunk("far-before", 0, "far", 2),
            ContextChunk("near-before", 1, "near" * 20, 40),
            ContextChunk("center", 2, "center", 8),
            ContextChunk("near-after", 3, "after", 3),
        ]

        response = RetrievalService._budget_context(
            ContextWindowRequest(
                "tenant", "user", "center", before=2, after=1, token_budget=16
            ),
            chunks,
        )

        self.assertEqual(
            [chunk.chunk_id for chunk in response.chunks], ["center", "near-after"]
        )

    def test_hit_snippet_preserves_complete_chunk(self) -> None:
        long_content = "x" * 1500
        candidate = replace(_candidate("a", "p1", 1.0), content=long_content)

        hit = RetrievalService._fuse([candidate], [], 1)[0]

        self.assertEqual(hit.snippet, long_content)

    def test_top_k_keeps_multiple_chunks_from_the_same_paper(self) -> None:
        hits = RetrievalService._fuse(
            [
                replace(
                    _candidate("a", "p1", 0.9, chunk_index=0),
                    content="First independent evidence.",
                ),
                replace(
                    _candidate("b", "p1", 0.8, chunk_index=1),
                    content="Second independent evidence.",
                ),
            ],
            [],
            2,
        )

        self.assertEqual([hit.chunk_id for hit in hits], ["a", "b"])
        self.assertEqual([hit.chunk_index for hit in hits], [0, 1])

    def test_top_k_suppresses_exact_normalized_duplicates_within_paper(self) -> None:
        first = replace(_candidate("a", "p1", 0.9, 0), content="Same\n\nevidence")
        duplicate = replace(_candidate("b", "p1", 0.8, 1), content=" same EVIDENCE ")
        distinct = replace(_candidate("c", "p1", 0.7, 2), content="Different evidence")

        hits = RetrievalService._fuse([first, duplicate, distinct], [], 3)

        self.assertEqual([hit.chunk_id for hit in hits], ["a", "c"])

    def test_identical_text_from_different_papers_remains_citeable(self) -> None:
        first = replace(_candidate("a", "p1", 0.9), content="Shared evidence")
        second = replace(_candidate("b", "p2", 0.8), content="Shared evidence")

        hits = RetrievalService._fuse([first, second], [], 2)

        self.assertEqual([hit.chunk_id for hit in hits], ["a", "b"])

    def test_hit_exposes_section_and_page_provenance(self) -> None:
        candidate = replace(
            _candidate("method-chunk", "p1", 1.0, chunk_index=3),
            section_id="method",
            section_path="3 Method",
            page_start=4,
            page_end=5,
            chunk_type="equation",
            parent_section_id="method",
            source_block_ids=("eq-3",),
            chunk_metadata={"provenance": {"page_number": 4}},
            context_before="The objective is defined below.",
            context_after="Here x denotes the model.",
            previous_chunk_id="previous-chunk",
            next_chunk_id="next-chunk",
        )

        hit = RetrievalService._fuse([candidate], [], 1)[0]
        payload = hit.to_dict()

        self.assertEqual(hit.section_id, "method")
        self.assertEqual(payload["section_path"], "3 Method")
        self.assertEqual(payload["page_start"], 4)
        self.assertEqual(payload["page_end"], 5)
        self.assertEqual(payload["snippet"], candidate.content)
        self.assertEqual(payload["chunk_type"], "equation")
        self.assertEqual(payload["parent_section_id"], "method")
        self.assertEqual(payload["source_block_ids"], ["eq-3"])
        self.assertEqual(payload["chunk_metadata"]["provenance"]["page_number"], 4)
        self.assertEqual(payload["provenance"]["page_number"], 4)
        self.assertEqual(payload["context_before"], "The objective is defined below.")
        self.assertEqual(payload["context_after"], "Here x denotes the model.")
        self.assertEqual(payload["previous_chunk_id"], "previous-chunk")
        self.assertEqual(payload["next_chunk_id"], "next-chunk")

    def test_rrf_merges_by_id_without_recency(self) -> None:
        merged = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
        self.assertEqual(merged[0][0], "b")
        self.assertAlmostEqual(merged[0][1], 1 / 62 + 1 / 61)

    async def test_hybrid_results_are_fused_and_citeable(self) -> None:
        repository = _Repository()
        service = RetrievalService(repository, _Embedding())
        response = await service.search(RetrievalRequest("t", "u", "retrieval", limit=3))

        self.assertEqual(response.mode, "hybrid")
        self.assertEqual(response.local_hits[0].paper_id, "p2")
        self.assertTrue(all(hit.can_cite for hit in response.local_hits))
        self.assertEqual(response.external_candidates, ())
        self.assertEqual(repository.embedding_model, "Qwen3-Embedding-4B")

    async def test_hits_expose_chunk_index(self) -> None:
        response = await RetrievalService(_Repository(), _Embedding()).search(
            RetrievalRequest("t", "u", "retrieval", limit=1)
        )

        self.assertIn("chunk_index", response.to_dict()["local_hits"][0])

    async def test_embedding_failure_keeps_lexical_results(self) -> None:
        service = RetrievalService(_Repository(), _BrokenEmbedding())
        response = await service.search(RetrievalRequest("t", "u", "retrieval"))

        self.assertEqual(response.mode, "lexical")
        self.assertEqual(len(response.local_hits), 2)
        self.assertTrue(response.warnings)


if __name__ == "__main__":
    unittest.main()
