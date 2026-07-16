from __future__ import annotations

import unittest

from app.retrieval.embedding import EmbeddingUnavailable
from app.retrieval.models import RetrievalRequest, RetrievalCandidate
from app.retrieval.service import RetrievalService, reciprocal_rank_fusion


def _candidate(chunk_id: str, paper_id: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
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
    async def lexical_candidates(self, request):
        return [_candidate("a", "p1", 0.9), _candidate("b", "p2", 0.8)]

    async def vector_candidates(self, request, vector):
        return [_candidate("b", "p2", 0.95), _candidate("c", "p3", 0.7)]


class _Embedding:
    async def embed(self, texts):
        return [[1.0] + [0.0] * 1023]


class _BrokenEmbedding:
    async def embed(self, texts):
        raise EmbeddingUnavailable("offline")


class RetrievalServiceTest(unittest.IsolatedAsyncioTestCase):
    def test_rrf_merges_by_id_without_recency(self) -> None:
        merged = reciprocal_rank_fusion([["a", "b"], ["b", "c"]], k=60)
        self.assertEqual(merged[0][0], "b")
        self.assertAlmostEqual(merged[0][1], 1 / 62 + 1 / 61)

    async def test_hybrid_results_are_fused_and_citeable(self) -> None:
        service = RetrievalService(_Repository(), _Embedding())
        response = await service.search(RetrievalRequest("t", "u", "retrieval", limit=3))

        self.assertEqual(response.mode, "hybrid")
        self.assertEqual(response.local_hits[0].paper_id, "p2")
        self.assertTrue(all(hit.can_cite for hit in response.local_hits))
        self.assertEqual(response.external_candidates, ())

    async def test_embedding_failure_keeps_lexical_results(self) -> None:
        service = RetrievalService(_Repository(), _BrokenEmbedding())
        response = await service.search(RetrievalRequest("t", "u", "retrieval"))

        self.assertEqual(response.mode, "lexical")
        self.assertEqual(len(response.local_hits), 2)
        self.assertTrue(response.warnings)


if __name__ == "__main__":
    unittest.main()
