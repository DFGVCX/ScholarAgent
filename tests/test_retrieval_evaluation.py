from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from app.evaluation.retrieval import (
    _label_matches,
    _chunks_for_strategy,
    build_evaluation_report,
    comparison_rows,
    evidence_ranking_metrics,
    fingerprint_records,
    ranking_metrics,
    render_comparison_csv,
    render_comparison_markdown,
    validate_corpus_files,
    validate_fingerprints,
)
from app.papers.parsing import ParsedPaper


class RetrievalEvaluationTest(unittest.TestCase):
    def test_multimodal_strategy_is_available_for_comparison(self) -> None:
        parsed = ParsedPaper(
            full_text="",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": "multimodal_aware_v3", "version": "3"}},
            status="ready",
            quality_score=1.0,
        )
        with patch(
            "app.evaluation.retrieval.parse_pdf_multimodal",
            return_value=parsed,
        ) as parser:
            result, chunks = _chunks_for_strategy(
                "multimodal_aware_v3",
                {"path": str(Path("paper.pdf"))},
                chunk_size=900,
                chunk_overlap=120,
            )

        parser.assert_called_once()
        self.assertIs(result, parsed)
        self.assertEqual(chunks, [])

    def test_formula_aware_strategy_is_available_for_comparison(self) -> None:
        parsed = ParsedPaper(
            full_text="",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": "formula_aware_v2", "version": "2"}},
            status="ready",
            quality_score=1.0,
        )
        with patch(
            "app.evaluation.retrieval.parse_pdf_formula_aware",
            return_value=parsed,
        ) as parser:
            result, chunks = _chunks_for_strategy(
                "formula_aware_v2",
                {"path": str(Path("paper.pdf"))},
                chunk_size=900,
                chunk_overlap=120,
            )

        parser.assert_called_once()
        self.assertIs(result, parsed)
        self.assertEqual(chunks, [])

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

    def test_evidence_recall_counts_unique_labels_not_matching_chunks(self) -> None:
        metrics = evidence_ranking_metrics(
            [
                {"chunk_id": "chunk-a", "matched_evidence_ids": ["evidence-1"]},
                {"chunk_id": "chunk-b", "matched_evidence_ids": ["evidence-1"]},
            ],
            {"evidence-1", "evidence-2"},
            k=2,
        )

        self.assertEqual(metrics.recall, 0.5)
        self.assertEqual(metrics.precision, 1.0)
        self.assertEqual(metrics.reciprocal_rank, 1.0)
        self.assertLess(metrics.ndcg, 1.0)

    def test_evidence_terms_match_legacy_chunks_without_page_metadata(self) -> None:
        chunk = {
            "paper_id": "paper-fedavg",
            "content": "The server performs federated averaging of local model updates.",
            "page_start": None,
            "page_end": None,
        }
        label = {
            "paper_id": "paper-fedavg",
            "page_ranges": [[3, 4]],
            "evidence_terms": ["federated averaging", "local model updates"],
        }

        self.assertTrue(_label_matches(chunk, label))

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

    def test_report_prefers_evidence_coverage_over_strategy_chunk_ids(self) -> None:
        report = build_evaluation_report(
            strategy="legacy_fixed",
            parser_version="1",
            chunker_version="1",
            embedding_model="Qwen3-Embedding-0.6B",
            corpus_fingerprint="corpus",
            query_fingerprint="queries",
            query_results=[
                {
                    "query": "联邦平均如何聚合客户端更新？",
                    "ranked": [
                        {"chunk_id": "a", "matched_evidence_ids": ["evidence-1"]},
                        {"chunk_id": "b", "matched_evidence_ids": ["evidence-1"]},
                    ],
                    "evidence_ids": ["evidence-1", "evidence-2"],
                }
            ],
            k_values=(1, 2),
        )

        self.assertEqual(report["metrics"]["recall@2"], 0.5)
        self.assertEqual(report["metrics"]["precision@2"], 1.0)

    def test_fingerprints_are_order_stable_and_mismatches_are_rejected(self) -> None:
        left = fingerprint_records([{"paper_id": "b"}, {"paper_id": "a"}])
        right = fingerprint_records([{"paper_id": "a"}, {"paper_id": "b"}])

        self.assertEqual(left, right)
        with self.assertRaisesRegex(ValueError, "corpus fingerprint"):
            validate_fingerprints(
                {"corpus_fingerprint": "one", "query_fingerprint": "same", "embedding_model": "qwen"},
                {"corpus_fingerprint": "two", "query_fingerprint": "same", "embedding_model": "qwen"},
            )

    def test_corpus_validation_rejects_a_pdf_with_the_wrong_hash(self) -> None:
        with TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(b"abc")
            corpus = [{"paper_id": "paper-1", "path": str(path), "sha256": "wrong"}]

            with self.assertRaisesRegex(ValueError, "SHA-256 mismatch.*paper-1"):
                validate_corpus_files(corpus)

    def test_comparison_summary_exports_metrics_and_corpus_diagnostics(self) -> None:
        comparison = {
            "embedding_model": "Qwen3-Embedding-0.6B",
            "reports": [
                {
                    "strategy": "legacy_fixed",
                    "metrics": {"recall@5": 0.5, "precision@5": 0.2, "mrr": 0.75},
                    "corpus": {
                        "paper_count": 7,
                        "chunk_count": 396,
                        "average_chunk_chars": 812.5,
                        "parse_failures": [],
                    },
                }
            ],
        }

        rows = comparison_rows(comparison)
        csv_text = render_comparison_csv(comparison)
        markdown = render_comparison_markdown(comparison)

        self.assertEqual(rows[0]["recall@5"], 0.5)
        self.assertEqual(rows[0]["chunk_count"], 396)
        self.assertIn("strategy,paper_count,chunk_count", csv_text)
        self.assertIn("legacy_fixed", markdown)
        self.assertIn("Recall@5", markdown)


if __name__ == "__main__":
    unittest.main()
