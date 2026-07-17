from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

from app.papers.ingestion import PaperIngestionService
from app.papers.models import ContentVersion, PaperInput, PaperRecord
from app.retrieval.embedding import EmbeddingUnavailable


def _record() -> PaperRecord:
    now = datetime.now(timezone.utc)
    return PaperRecord(
        paper_uuid=uuid4(), tenant_id="t", user_id="u", paper_id="paper-1",
        source="manual", title="Paper", authors=(), abstract="", published_at=None,
        doi=None, arxiv_id=None, url=None, in_knowledge_base=True,
        ingestion_status="embedding", current_content_version=1, metadata={},
        created_at=now, updated_at=now,
    )


class _Repository:
    def __init__(self, record: PaperRecord) -> None:
        self.record = record
        self.failed = None
        self.vectors = None
        self.embedding_model = None

    async def save(self, *args):
        return self.record

    async def save_asset(self, *args):
        return None

    async def replace_content(self, *args, **kwargs):
        return ContentVersion(self.record.paper_uuid, uuid4(), 1, len(args[5]))

    async def set_embeddings(self, *args, **kwargs):
        self.vectors = args[4]
        self.embedding_model = kwargs["model"]

    async def mark_embedding_failed(self, *args):
        self.failed = args[-1]

    async def get(self, *args):
        return self.record


class _Embedding:
    model = "Qwen3-Embedding-4B"

    async def embed(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]


class _BrokenEmbedding:
    async def embed(self, texts):
        raise EmbeddingUnavailable("qwen offline")


class PaperIngestionTest(unittest.IsolatedAsyncioTestCase):
    async def _run(self, embedding):
        repository = _Repository(_record())

        @asynccontextmanager
        async def transaction(*_):
            yield object()

        service = PaperIngestionService(
            embedding,
            transaction_factory=transaction,
            repository_factory=lambda _: repository,
        )
        paper = PaperInput(
            paper_id="paper-1", source="manual", title="Paper",
            full_text="A paragraph with enough text to index for retrieval.",
        )
        with patch(
            "app.papers.ingestion.get_settings",
            return_value=SimpleNamespace(rag_chunk_size=900, rag_chunk_overlap=120),
        ):
            result = await service.ingest("t", "u", paper)
        return result, repository

    async def test_successful_ingestion_writes_embeddings(self) -> None:
        result, repository = await self._run(_Embedding())
        self.assertEqual(result.embedding_status, "ready")
        self.assertEqual(len(repository.vectors), 1)
        self.assertEqual(repository.embedding_model, "Qwen3-Embedding-4B")

    async def test_embedding_failure_preserves_lexical_content(self) -> None:
        result, repository = await self._run(_BrokenEmbedding())
        self.assertEqual(result.embedding_status, "failed")
        self.assertIn("offline", repository.failed)
        self.assertEqual(result.chunk_count, 1)


if __name__ == "__main__":
    unittest.main()
