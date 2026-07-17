from __future__ import annotations

from contextlib import asynccontextmanager
import unittest

from app.papers.reembedding import EmbeddingReindexService


class _Repository:
    def __init__(self) -> None:
        self.saved_model = None
        self.completed_job = None
        self.failed_job = None

    async def enqueue_reembedding_jobs(self, tenant_id, user_id):
        return {"created": 2, "existing": 1}

    async def list_worker_scopes(self):
        return [("tenant", "user")]

    async def claim_reembedding_job(self, worker_id, tenant_id, user_id):
        return {
            "job_uuid": "job-1",
            "tenant_id": "tenant",
            "user_id": "user",
            "paper_uuid": "paper-1",
            "attempt_count": 1,
            "max_attempts": 3,
        }

    async def current_embedding_batch(self, tenant_id, user_id, paper_uuid):
        return {
            "content_uuid": "content-1",
            "chunks": [
                {"chunk_index": 0, "content": "first"},
                {"chunk_index": 1, "content": "second"},
            ],
        }

    async def set_embeddings(self, tenant_id, user_id, paper_uuid, content_uuid, vectors, *, model):
        self.saved_model = model
        self.vectors = vectors

    async def complete_ingestion_job(self, tenant_id, user_id, job_uuid):
        self.completed_job = job_uuid

    async def fail_ingestion_job(self, tenant_id, user_id, job, error):
        self.failed_job = (job, error)


class _Embedding:
    model = "Qwen3-Embedding-4B"

    async def embed(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]


class ReembeddingServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = _Repository()

        @asynccontextmanager
        async def tenant_transaction(*_):
            yield object()

        @asynccontextmanager
        async def worker_transaction():
            yield object()

        self.service = EmbeddingReindexService(
            tenant_transaction_factory=tenant_transaction,
            worker_transaction_factory=worker_transaction,
            repository_factory=lambda _: self.repository,
            embedding_factory=lambda: _Embedding(),
        )

    async def test_enqueue_creates_one_job_per_stale_paper_and_skips_existing(self) -> None:
        result = await self.service.enqueue("tenant", "user")

        self.assertEqual(result, {"created": 2, "existing": 1})

    async def test_process_next_embeds_current_chunks_with_active_model(self) -> None:
        processed = await self.service.process_next("worker-test")

        self.assertEqual(processed.status, "completed")
        self.assertEqual(processed.chunk_count, 2)
        self.assertEqual(self.repository.saved_model, "Qwen3-Embedding-4B")
        self.assertEqual(self.repository.completed_job, "job-1")

    async def test_process_failure_is_recorded_without_secret(self) -> None:
        class BrokenEmbedding:
            model = "broken"

            async def embed(self, texts):
                raise RuntimeError("request failed with sk-secret-value")

        self.service.embedding_factory = lambda: BrokenEmbedding()
        processed = await self.service.process_next("worker-test")

        self.assertEqual(processed.status, "retry")
        self.assertNotIn("sk-secret-value", self.repository.failed_job[1])


if __name__ == "__main__":
    unittest.main()
