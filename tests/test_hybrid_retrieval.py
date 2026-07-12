from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.services.rag_service import (
    _bm25_scores,
    _preference_score,
    _temporal_decay,
    _tokens,
)


class HybridRetrievalTest(unittest.TestCase):
    def test_bm25_rewards_query_frequency_and_document_specificity(self) -> None:
        query = _tokens("anomaly detection")
        documents = [
            _tokens("anomaly anomaly detection in data streams"),
            _tokens("database repair and data quality"),
        ]
        scores = _bm25_scores(query, documents)
        self.assertGreater(scores[0], scores[1])
        self.assertEqual(scores[1], 0.0)

    def test_temporal_decay_uses_configurable_half_life(self) -> None:
        now = datetime(2026, 1, 1, tzinfo=timezone.utc)
        recent = _temporal_decay(now.isoformat(), 100, now)
        old = _temporal_decay((now - timedelta(days=100)).isoformat(), 100, now)
        self.assertAlmostEqual(recent, 1.0)
        self.assertAlmostEqual(old, 0.5)

    def test_preference_recall_signal_rewards_matching_research_direction(self) -> None:
        preferences = set(_tokens("time series anomaly detection"))
        matching = _preference_score(_tokens("deep anomaly detection for time series"), preferences)
        unrelated = _preference_score(_tokens("citation formatting standards"), preferences)
        self.assertGreater(matching, unrelated)


if __name__ == "__main__":
    unittest.main()
