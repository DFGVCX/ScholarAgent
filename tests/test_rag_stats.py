from __future__ import annotations

from contextlib import asynccontextmanager
import unittest
from unittest.mock import AsyncMock, patch

from app.services.rag_service import rag_service


class RagStatsTest(unittest.IsolatedAsyncioTestCase):
    async def test_stats_degrade_when_ready_vector_consistency_is_broken(self) -> None:
        counts = {
            "paper_count": 2,
            "chunk_count": 10,
            "vector_count": 8,
            "failed_papers": 0,
            "failed_jobs": 1,
            "pending_jobs": 2,
            "ready_noncurrent_chunks": 1,
            "ready_missing_vectors": 2,
            "ready_wrong_model": 3,
            "chunk_table_bytes": 1024,
            "chunk_index_bytes": 512,
            "embedding_call_count": 10,
            "embedding_success_count": 8,
            "embedding_failed_count": 2,
            "embedding_request_count": 13,
            "embedding_successful_request_count": 10,
            "embedding_failed_request_count": 3,
            "embedding_cancelled_request_count": 1,
            "embedding_reported_tokens": 2_500_000,
            "embedding_usage_reported_requests": 9,
            "embedding_successful_usage_reported_requests": 8,
        }

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        repository = AsyncMock()
        repository.stats.return_value = counts
        with patch("app.services.rag_service.tenant_transaction", transaction), patch(
            "app.services.rag_service.PaperRepository", return_value=repository
        ), patch(
            "app.services.rag_service.get_settings"
        ) as settings:
            settings.return_value.rag_embedding_model = "Qwen3-Embedding-4B"
            settings.return_value.rag_chunk_size = 800
            settings.return_value.rag_chunk_overlap = 80
            settings.return_value.rag_top_k = 8
            settings.return_value.rag_candidate_limit = 80
            settings.return_value.rag_max_chunks_per_paper = 3
            settings.return_value.rag_semantic_timeout_seconds = 8
            settings.return_value.rag_embedding_cost_cny_per_million_tokens = 0.7
            result = await rag_service.stats("tenant", "user")

        self.assertEqual(result["vector_count"], 8)
        self.assertEqual(result["consistency_status"], "degraded")
        self.assertEqual(result["consistency_error_count"], 6)
        self.assertEqual(result["embedding_model"], "Qwen3-Embedding-4B")
        self.assertEqual(result["embedding_usage"]["call_count"], 10)
        self.assertEqual(result["embedding_usage"]["failure_rate"], 0.2)
        self.assertEqual(result["embedding_usage"]["reported_tokens"], 2_500_000)
        self.assertEqual(result["embedding_usage"]["unreported_successful_requests"], 2)
        self.assertEqual(result["embedding_usage"]["cancelled_provider_requests"], 1)
        self.assertEqual(result["embedding_usage"]["estimated_cost_cny"], 1.75)
        self.assertTrue(result["embedding_usage"]["pricing_configured"])
        repository.stats.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
