from __future__ import annotations

import unittest

from app.evaluation.retrieval import (
    build_evaluation_report,
    fingerprint_records,
    ranking_metrics,
    validate_fingerprints,
)


class RetrievalEvaluationTest(unittest.TestCase):
    def test_ranking_metrics_compute_recall_precision_mrr_and_ndcg(self) -> None:
        ranked = ["irrelevant", "relevant-a", "relevant-b"]
        relevant = {"relevant-a", "relevant-b"}

        metrics = ranking_metrics(ranked, relevant, k=3)

        self.assertEqual(metrics.recall, 1.0)
        self.assertAlmostEqual(metrics.precision, 2 / 3)
        self.assertEqual(metrics.reciprocal_rank, 0.5)
        self.assertGreater(metrics.ndcg, 0.0)
        self.assertLessEqual(metrics.ndcg, 1.0)

    def test_zero_relevance_is_explicit_not_division_by_zero(self) -> None:
        metrics = ranking_metrics(["a", "b"], set(), k=2)

        self.assertEqual(metrics.recall, 0.0)
        self.assertEqual(metrics.precision, 0.0)
        self.assertEqual(metrics.reciprocal_rank, 0.0)
        self.assertEqual(metrics.ndcg, 0.0)

    def test_unlabeled_report_is_diagnostic_only(self) -> None:
        report = build_evaluation_report(
            strategy="legacy_fixed",
            parser_version="1",
            chunker_version="1",
            embedding_model="Qwen3-Embedding-0.6B",
            corpus_fingerprint="corpus",
            query_fingerprint="queries",
            query_results=[
                {
                    "query": "federated learning",
                    "ranked": [{"chunk_id": "paper-1:0", "content": "complete chunk"}],
                    "relevant_ids": None,
                }
            ],
            k_values=(1, 3),
        )

        self.assertTrue(report["diagnostic_only"])
        self.assertNotIn("metrics", report)
        self.assertEqual(report["queries"][0]["ranked"][0]["content"], "complete chunk")

    def test_labeled_report_aggregates_metrics_at_each_k(self) -> None:
        report = build_evaluation_report(
            strategy="structure_aware_v1",
            parser_version="1",
            chunker_version="1",
            embedding_model="Qwen3-Embedding-0.6B",
            corpus_fingerprint="corpus",
            query_fingerprint="queries",
            query_results=[
                {
                    "query": "federated learning",
                    "ranked": [{"chunk_id": "a"}, {"chunk_id": "b"}],
                    "relevant_ids": ["b"],
                }
            ],
            k_values=(1, 2),
        )

        self.assertFalse(report["diagnostic_only"])
        self.assertEqual(report["metrics"]["recall@1"], 0.0)
        self.assertEqual(report["metrics"]["recall@2"], 1.0)
        self.assertEqual(report["metrics"]["mrr"], 0.5)

    def test_fingerprints_are_order_stable_and_mismatches_are_rejected(self) -> None:
        left = fingerprint_records([{"paper_id": "b"}, {"paper_id": "a"}])
        right = fingerprint_records([{"paper_id": "a"}, {"paper_id": "b"}])

        self.assertEqual(left, right)
        with self.assertRaisesRegex(ValueError, "corpus fingerprint"):
            validate_fingerprints(
                {"corpus_fingerprint": "one", "query_fingerprint": "same", "embedding_model": "qwen"},
                {"corpus_fingerprint": "two", "query_fingerprint": "same", "embedding_model": "qwen"},
            )


if __name__ == "__main__":
    unittest.main()
