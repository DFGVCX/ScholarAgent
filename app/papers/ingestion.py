from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Callable

from app.config import get_settings
from app.db.session import tenant_transaction
from app.papers.chunking import chunk_text
from app.papers.models import PaperInput, PaperRecord
from app.papers.repository import PaperRepository
from app.retrieval.embedding import EmbeddingUnavailable, QwenEmbeddingClient


@dataclass(frozen=True)
class IngestionResult:
    paper: PaperRecord
    chunk_count: int
    embedding_status: str
    warning: str | None = None


class PaperIngestionService:
    def __init__(
        self,
        embedding: QwenEmbeddingClient | None = None,
        *,
        transaction_factory: Callable[..., Any] = tenant_transaction,
        repository_factory: Callable[..., PaperRepository] = PaperRepository,
    ) -> None:
        self.embedding = embedding
        self.transaction_factory = transaction_factory
        self.repository_factory = repository_factory

    def _embedding(self) -> QwenEmbeddingClient:
        if self.embedding is None:
            self.embedding = QwenEmbeddingClient.from_settings()
        return self.embedding

    async def ingest(self, tenant_id: str, user_id: str, paper: PaperInput) -> IngestionResult:
        settings = get_settings()
        chunks = chunk_text(paper.full_text, settings.rag_chunk_size, settings.rag_chunk_overlap)
        content_version = None
        async with self.transaction_factory(tenant_id, user_id) as session:
            repository = self.repository_factory(session)
            record = await repository.save(tenant_id, user_id, paper)
            await repository.save_asset(tenant_id, user_id, record.paper_uuid, paper)
            if chunks:
                content_hash = hashlib.sha256(paper.full_text.encode("utf-8")).hexdigest()
                content_version = await repository.replace_content(
                    tenant_id,
                    user_id,
                    record.paper_uuid,
                    paper.full_text,
                    content_hash,
                    chunks,
                    extraction_method=str(paper.metadata.get("extraction_method") or "provided_text"),
                )

        if content_version is None:
            return IngestionResult(record, 0, "metadata_only")

        try:
            vectors = await self._embedding().embed([chunk.content for chunk in chunks])
            async with self.transaction_factory(tenant_id, user_id) as session:
                await self.repository_factory(session).set_embeddings(
                    tenant_id,
                    user_id,
                    record.paper_uuid,
                    content_version.content_uuid,
                    vectors,
                    model=QwenEmbeddingClient.MODEL,
                )
            embedding_status = "ready"
            warning = None
        except EmbeddingUnavailable as exc:
            warning = str(exc)
            async with self.transaction_factory(tenant_id, user_id) as session:
                await self.repository_factory(session).mark_embedding_failed(
                    tenant_id,
                    user_id,
                    record.paper_uuid,
                    content_version.content_uuid,
                    warning,
                )
            embedding_status = "failed"

        async with self.transaction_factory(tenant_id, user_id) as session:
            refreshed = await self.repository_factory(session).get(tenant_id, user_id, paper.paper_id)
        return IngestionResult(refreshed or record, len(chunks), embedding_status, warning)


paper_ingestion_service = PaperIngestionService()
