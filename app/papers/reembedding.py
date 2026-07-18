from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable

from app.config import get_settings
from app.db.session import tenant_transaction, worker_transaction
from app.papers.repository import PaperRepository
from app.retrieval.embedding import QwenEmbeddingClient


@dataclass(frozen=True)
class ReindexResult:
    job_id: str
    status: str
    chunk_count: int = 0
    error: str | None = None


def _redact_error(error: Exception) -> str:
    detail = str(error)
    secret = get_settings().rag_embedding_api_key
    if secret:
        detail = detail.replace(secret, "***")
    detail = re.sub(r"(?i)sk-[a-z0-9_-]{4,}", "sk-***", detail)
    return detail[:4000]


class EmbeddingReindexService:
    def __init__(
        self,
        *,
        tenant_transaction_factory: Callable[..., Any] = tenant_transaction,
        worker_transaction_factory: Callable[..., Any] = worker_transaction,
        repository_factory: Callable[..., PaperRepository] = PaperRepository,
        embedding_factory: Callable[[], QwenEmbeddingClient] = QwenEmbeddingClient.from_settings,
    ) -> None:
        self.tenant_transaction_factory = tenant_transaction_factory
        self.worker_transaction_factory = worker_transaction_factory
        self.repository_factory = repository_factory
        self.embedding_factory = embedding_factory

    async def enqueue(self, tenant_id: str, user_id: str) -> dict[str, int]:
        async with self.tenant_transaction_factory(tenant_id, user_id) as session:
            return await self.repository_factory(session).enqueue_reembedding_jobs(
                tenant_id, user_id
            )

    async def process_next(self, worker_id: str) -> ReindexResult | None:
        async with self.worker_transaction_factory() as session:
            scopes = await self.repository_factory(session).list_worker_scopes()
        job = None
        for tenant_id, user_id in scopes:
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                job = await self.repository_factory(session).claim_reembedding_job(
                    worker_id, tenant_id, user_id
                )
            if job is not None:
                break
        if job is None:
            return None

        job_id = str(job["job_uuid"])
        tenant_id = str(job["tenant_id"])
        user_id = str(job["user_id"])
        try:
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                batch = await self.repository_factory(session).current_embedding_batch(
                    tenant_id, user_id, job["paper_uuid"]
                )
            if batch is None or not batch["chunks"]:
                raise RuntimeError("No current paper chunks are available for re-embedding")
            embedding = self.embedding_factory()
            vectors = await embedding.embed([item["content"] for item in batch["chunks"]])
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                repository = self.repository_factory(session)
                await repository.set_embeddings(
                    tenant_id,
                    user_id,
                    job["paper_uuid"],
                    batch["content_uuid"],
                    vectors,
                    model=embedding.model,
                )
                await repository.complete_ingestion_job(tenant_id, user_id, job["job_uuid"])
            return ReindexResult(job_id, "completed", len(vectors))
        except Exception as exc:
            error = _redact_error(exc)
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                await self.repository_factory(session).fail_ingestion_job(
                    tenant_id, user_id, job, error
                )
            status = (
                "retry"
                if int(job.get("attempt_count") or 0) < int(job.get("max_attempts") or 1)
                else "failed"
            )
            return ReindexResult(job_id, status, error=error)


embedding_reindex_service = EmbeddingReindexService()
