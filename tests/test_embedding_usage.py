from __future__ import annotations

from contextlib import asynccontextmanager
import asyncio
import unittest
from unittest.mock import AsyncMock, patch

from app.retrieval.embedding import EmbeddingUsage
from app.retrieval.usage import persist_embedding_usage


class _Client:
    last_usage = EmbeddingUsage(
        status="succeeded",
        model="qwen3.7-text-embedding",
        input_count=3,
        request_count=2,
        successful_request_count=1,
        failed_request_count=1,
        cancelled_request_count=0,
        reported_tokens=42,
        usage_reported_requests=1,
        successful_usage_reported_requests=1,
        duration_ms=25,
        error_type=None,
    )


class EmbeddingUsagePersistenceTest(unittest.IsolatedAsyncioTestCase):
    async def test_persists_sanitized_usage_in_an_independent_tenant_transaction(self) -> None:
        @asynccontextmanager
        async def transaction(tenant_id, user_id):
            self.assertEqual((tenant_id, user_id), ("tenant-a", "user-a"))
            yield object()

        repository = AsyncMock()
        with patch("app.retrieval.usage.tenant_transaction", transaction), patch(
            "app.retrieval.usage.PaperRepository", return_value=repository
        ):
            persisted = await persist_embedding_usage(
                "tenant-a", "user-a", _Client(), operation="ingestion"
            )

        self.assertTrue(persisted)
        repository.record_embedding_usage.assert_awaited_once_with(
            "tenant-a",
            "user-a",
            operation="ingestion",
            model="qwen3.7-text-embedding",
            status="succeeded",
            input_count=3,
            request_count=2,
            successful_request_count=1,
            failed_request_count=1,
            cancelled_request_count=0,
            reported_tokens=42,
            usage_reported_requests=1,
            successful_usage_reported_requests=1,
            duration_ms=25,
            error_type=None,
        )

    async def test_telemetry_failure_never_replaces_the_rag_result(self) -> None:
        @asynccontextmanager
        async def broken_transaction(*_):
            raise RuntimeError("database unavailable")
            yield  # pragma: no cover

        with patch("app.retrieval.usage.tenant_transaction", broken_transaction), self.assertLogs(
            "app.retrieval.usage", level="WARNING"
        ) as logs:
            persisted = await persist_embedding_usage(
                "tenant-a", "user-a", _Client(), operation="retrieval"
            )

        self.assertFalse(persisted)
        self.assertIn("RuntimeError", "\n".join(logs.output))

    async def test_stalled_telemetry_returns_within_a_bounded_wait(self) -> None:
        @asynccontextmanager
        async def stalled_transaction(*_):
            await asyncio.sleep(60)
            yield object()

        started = asyncio.get_running_loop().time()
        with patch("app.retrieval.usage.tenant_transaction", stalled_transaction), self.assertLogs(
            "app.retrieval.usage", level="WARNING"
        ):
            persisted = await persist_embedding_usage(
                "tenant-a",
                "user-a",
                _Client(),
                operation="retrieval",
                timeout_seconds=0.01,
            )
        elapsed = asyncio.get_running_loop().time() - started

        self.assertFalse(persisted)
        self.assertLess(elapsed, 0.2)


if __name__ == "__main__":
    unittest.main()
