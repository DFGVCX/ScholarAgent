from __future__ import annotations

from dataclasses import replace
import unittest

from app.retrieval.models import RetrievalCandidate
from app.retrieval.reproducibility import (
    candidate_fingerprint,
    retrieval_provenance,
    stable_fingerprint,
)


def _candidate(chunk_id: str, content: str, score: float) -> RetrievalCandidate:
    return RetrievalCandidate(
        chunk_id=chunk_id,
        chunk_index=0,
        paper_uuid="paper-uuid",
        paper_id="paper-1",
        title="Paper",
        authors=(),
        content=content,
        source="local",
        doi=None,
        arxiv_id=None,
        canonical_url=None,
        published_at=None,
        score=score,
        content_version=3,
    )


class RetrievalReproducibilityTest(unittest.TestCase):
    def test_stable_fingerprint_ignores_mapping_order(self) -> None:
        self.assertEqual(
            stable_fingerprint({"b": 2, "a": 1}),
            stable_fingerprint({"a": 1, "b": 2}),
        )

    def test_candidate_fingerprint_detects_content_and_order_changes(self) -> None:
        first = _candidate("a", "alpha", 0.9)
        second = _candidate("b", "beta", 0.8)
        baseline = candidate_fingerprint([first, second])

        self.assertNotEqual(baseline, candidate_fingerprint([second, first]))
        self.assertNotEqual(
            baseline,
            candidate_fingerprint([replace(first, content="changed"), second]),
        )

    def test_provenance_separates_query_strategy_candidates_and_result(self) -> None:
        candidate = _candidate("a", "alpha evidence", 0.9)
        hit = type(
            "Hit",
            (),
            {
                "chunk_id": "a",
                "content_version": 3,
                "final_rank": 1,
                "rrf_score": 0.1,
                "rerank_score": None,
            },
        )()
        value = retrieval_provenance(
            query="alpha",
            filters={"paper_ids": []},
            requested_mode="hybrid",
            effective_mode="hybrid",
            candidate_limit=80,
            result_limit=8,
            max_chunks_per_paper=3,
            embedding_model="embedding-v1",
            reranker_model=None,
            corpus_fingerprint="corpus-v1",
            lexical_candidates=[candidate],
            vector_candidates=[],
            hits=[hit],
        )

        self.assertEqual(value["corpus_fingerprint"], "corpus-v1")
        self.assertEqual(len(value["query_fingerprint"]), 64)
        self.assertEqual(len(value["strategy_fingerprint"]), 64)
        self.assertEqual(len(value["result_fingerprint"]), 64)
        self.assertEqual(
            value["configuration"]["embedding_model"], "embedding-v1"
        )
