from __future__ import annotations

import unittest

from app.evaluation.gate import evaluate_release_gate
from app.evaluation.production import (
    PRODUCTION_MODES,
    classify_retrieval_failure,
    evaluate_production_retrieval,
)


def _strategy_result(items: list[dict], *, latency_ms: float = 12.0) -> dict:
    return {
        "items": items,
        "retrieval_mode": "hybrid",
        "ranking_policy": {"requested_mode": "hybrid"},
        "warnings": [],
        "debug": {"timings_ms": {"total_ms": latency_ms}},
        "reproducibility": {
            "corpus_fingerprint": "corpus-v1",
            "result_fingerprint": "result-v1",
        },
    }


class ProductionRetrievalEvaluationTest(unittest.IsolatedAsyncioTestCase):
    async def test_evaluates_all_modes_and_operational_metrics(self) -> None:
        async def compare(query: str, limit: int) -> dict:
            self.assertEqual(limit, 20)
            hits = [
                {
                    "chunk_id": "chunk-1",
                    "paper_id": "paper-1",
                    "content": f"{query} target evidence",
                },
                {
                    "chunk_id": "chunk-2",
                    "paper_id": "paper-2",
                    "content": "background",
                },
            ]
            strategies = {}
            for mode in PRODUCTION_MODES:
                result = _strategy_result(hits)
                result["retrieval_mode"] = mode
                result["ranking_policy"] = {"requested_mode": mode}
                strategies[mode] = result
            return {"strategies": strategies}

        report = await evaluate_production_retrieval(
            queries=[
                {
                    "query_id": "q1",
                    "query": "federated learning",
                    "language": "en",
                    "category": "definition",
                    "relevant": [
                        {
                            "paper_id": "paper-1",
                            "evidence_terms": ["target evidence"],
                        }
                    ],
                }
            ],
            compare_search=compare,
            top_k=10,
            probe_k=20,
            runtime_stats={"embedding_model": "qwen-test", "consistency_status": "ok"},
        )

        self.assertEqual(report["strategy_order"], list(PRODUCTION_MODES))
        self.assertTrue(report["corpus_fingerprint_consistent"])
        self.assertEqual(len(report["reports"]), 4)
        for strategy in report["reports"]:
            self.assertEqual(strategy["metrics"]["recall@10"], 1.0)
            self.assertEqual(strategy["metrics"]["mrr"], 1.0)
            self.assertEqual(strategy["operations"]["failure_classes"], {"passed": 1})
            self.assertEqual(strategy["operations"]["average_context_tokens_estimated"], 11.0)
            self.assertEqual(strategy["operations"]["p95_latency_ms"], 12.0)

    async def test_marks_strategy_degradation(self) -> None:
        async def compare(_query: str, _limit: int) -> dict:
            strategies = {}
            for mode in PRODUCTION_MODES:
                result = _strategy_result(
                    [{"chunk_id": "x", "paper_id": "paper-1", "content": "evidence"}]
                )
                result["ranking_policy"] = {"requested_mode": mode}
                result["retrieval_mode"] = "lexical"
                result["warnings"] = ["semantic unavailable"] if mode != "lexical" else []
                strategies[mode] = result
            return {"strategies": strategies}

        report = await evaluate_production_retrieval(
            queries=[
                {
                    "query_id": "q1",
                    "query": "query",
                    "relevant": [{"paper_id": "paper-1", "evidence_terms": ["evidence"]}],
                }
            ],
            compare_search=compare,
        )
        by_mode = {item["strategy"]: item for item in report["reports"]}
        self.assertEqual(by_mode["lexical"]["operations"]["degraded_queries"], 0)
        self.assertEqual(by_mode["vector"]["operations"]["degraded_queries"], 1)


class RetrievalFailureClassificationTest(unittest.TestCase):
    def test_distinguishes_rank_failure_from_recall_failure(self) -> None:
        label = {"evidence_id": "e1", "paper_id": "paper-1"}
        matched = {"paper_id": "paper-1", "matched_evidence_ids": ["e1"]}
        unrelated = {"paper_id": "paper-2", "matched_evidence_ids": []}
        same_paper = {"paper_id": "paper-1", "matched_evidence_ids": []}

        self.assertEqual(classify_retrieval_failure([matched], [matched], [label]), "passed")
        self.assertEqual(
            classify_retrieval_failure([unrelated], [unrelated, matched], [label]),
            "ranked_too_low",
        )
        self.assertEqual(classify_retrieval_failure([], [], [label]), "not_retrieved")
        self.assertEqual(
            classify_retrieval_failure([unrelated], [unrelated], [label]),
            "paper_not_retrieved",
        )
        self.assertEqual(
            classify_retrieval_failure([same_paper], [same_paper], [label]),
            "evidence_not_retrieved",
        )


class RagReleaseGateTest(unittest.TestCase):
    @staticmethod
    def _report(metric: float = 0.9, *, degraded: int = 0) -> dict:
        return {
            "corpus_fingerprint": "corpus-v1",
            "query_fingerprint": "query-v1",
            "corpus_fingerprint_consistent": True,
            "runtime_stats": {"consistency_status": "ok"},
            "reports": [
                {
                    "strategy": mode,
                    "metrics": {"recall@10": metric, "mrr": metric, "ndcg@3": metric},
                    "operations": {"degraded_queries": degraded},
                }
                for mode in PRODUCTION_MODES
            ],
        }

    def test_passes_complete_non_degraded_report(self) -> None:
        result = evaluate_release_gate(self._report())
        self.assertTrue(result["passed"])
        self.assertEqual(result["failures"], [])

    def test_blocks_threshold_degradation_and_baseline_regression(self) -> None:
        result = evaluate_release_gate(
            self._report(0.70, degraded=1),
            baseline=self._report(0.90),
            maximum_metric_drop=0.05,
        )
        self.assertFalse(result["passed"])
        self.assertTrue(any("degraded queries" in item for item in result["failures"]))
        self.assertTrue(any("below" in item for item in result["failures"]))
        self.assertTrue(any("regressed" in item for item in result["failures"]))


if __name__ == "__main__":
    unittest.main()
