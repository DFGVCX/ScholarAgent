from __future__ import annotations

import unittest

from app.evaluation.performance import (
    percentile,
    plan_buffer_totals,
    plan_uses_index,
    summarize_query_plans,
)


class RetrievalPerformanceTest(unittest.TestCase):
    def test_percentile_uses_nearest_rank(self) -> None:
        self.assertIsNone(percentile([], 0.95))
        self.assertEqual(percentile([4, 1, 3, 2], 0.5), 2.0)
        self.assertEqual(percentile([4, 1, 3, 2], 0.95), 4.0)

    def test_finds_nested_index_and_sums_buffers(self) -> None:
        plan = {
            "Node Type": "Limit",
            "Shared Hit Blocks": 1,
            "Plans": [
                {
                    "Node Type": "Index Scan",
                    "Index Name": "idx_paper_chunks_embedding",
                    "Shared Hit Blocks": 3,
                    "Shared Read Blocks": 2,
                }
            ],
        }
        self.assertTrue(plan_uses_index(plan, "idx_paper_chunks_embedding"))
        self.assertFalse(plan_uses_index(plan, "another_index"))
        self.assertEqual(
            plan_buffer_totals(plan),
            {
                "shared_hit_blocks": 4,
                "shared_read_blocks": 2,
                "temp_read_blocks": 0,
                "temp_written_blocks": 0,
            },
        )

    def test_summarizes_latency_and_index_usage(self) -> None:
        plans = [
            {
                "Execution Time": 10.0,
                "Planning Time": 1.0,
                "Plan": {"Index Name": "idx", "Shared Hit Blocks": 2},
            },
            {
                "Execution Time": 20.0,
                "Planning Time": 2.0,
                "Plan": {"Node Type": "Seq Scan", "Shared Read Blocks": 1},
            },
        ]
        summary = summarize_query_plans(plans, index_name="idx")
        self.assertEqual(summary["index_usage_rate"], 0.5)
        self.assertEqual(summary["execution_ms"]["p50"], 10.0)
        self.assertEqual(summary["execution_ms"]["p95"], 20.0)
        self.assertEqual(summary["buffers"]["shared_hit_blocks"], 2)


if __name__ == "__main__":
    unittest.main()
