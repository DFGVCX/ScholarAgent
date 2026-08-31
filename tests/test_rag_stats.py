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
        }

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        repository = AsyncMock()
        repository.stats.return_value = counts
        with patch("app.services.rag_service.tenant_transaction", transaction), patch(
            "app.services.rag_service.PaperRepository", return_value=repository
        ):
            result = await rag_service.stats("tenant", "user")

        self.assertEqual(result["vector_count"], 8)
        self.assertEqual(result["consistency_status"], "degraded")
        self.assertEqual(result["consistency_error_count"], 6)
        repository.stats.assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
