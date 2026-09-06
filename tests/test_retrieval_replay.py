from __future__ import annotations

import unittest

from app.retrieval.replay import retrieval_attribution


class RetrievalReplayAttributionTest(unittest.TestCase):
    def test_distinguishes_rag_miss_from_agent_non_adoption(self) -> None:
        self.assertEqual(retrieval_attribution(set(), set()), "rag_not_retrieved")
        self.assertEqual(
            retrieval_attribution({"chunk-1"}, set()), "agent_not_adopted"
        )
        self.assertEqual(
            retrieval_attribution({"chunk-1"}, {"chunk-1"}), "evidence_adopted"
        )


if __name__ == "__main__":
    unittest.main()
