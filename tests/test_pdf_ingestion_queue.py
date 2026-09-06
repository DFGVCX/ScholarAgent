from __future__ import annotations

from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from uuid import UUID

from app.papers.models import PaperInput, PaperRecord
from app.papers.queued_ingestion import PdfIngestionQueueService, verify_asset_sha256
from mcp_server.scholar_mcp.models import PaperRecord as McpPaperRecord
from mcp_server.scholar_mcp.store import KnowledgeStore


def _record() -> PaperRecord:
    now = datetime.now(timezone.utc)
    return PaperRecord(
        paper_uuid=UUID("00000000-0000-0000-0000-000000000111"),
        tenant_id="tenant",
        user_id="user",
        paper_id="paper:pdf:one",
        source="pdf",
        title="Queued paper",
        authors=("Alice",),
        abstract="",
        published_at=None,
        doi=None,
        arxiv_id=None,
        url=None,
        in_knowledge_base=True,
        ingestion_status="parsing",
        current_content_version=0,
        metadata={},
        created_at=now,
        updated_at=now,
    )


class _Repository:
    def __init__(self) -> None:
        self.record = _record()
        self.saved = None
        self.asset = None
        self.enqueued = None
        self.completed = None
        self.failed = None
        self.parsing_failure = None
        self.job = {
            "job_uuid": "job-1",
            "tenant_id": "tenant",
            "user_id": "user",
            "paper_uuid": str(self.record.paper_uuid),
            "attempt_count": 1,
            "max_attempts": 3,
            "locked_by": "worker-1",
            "lease_token": "00000000-0000-0000-0000-000000000999",
            "generation_uuid": "00000000-0000-0000-0000-000000000888",
            "asset_sha256": "abc",
            "payload": {"paper_id": "paper:pdf:one"},
        }
        self.claim_scopes = []
        self.lease_refreshes = 0

    async def save(self, tenant_id, user_id, paper):
        self.saved = paper
        return self.record

    async def save_asset(self, tenant_id, user_id, paper_uuid, paper):
        self.asset = (paper_uuid, paper.file_uri)

    async def enqueue_pdf_ingestion_job(
        self, tenant_id, user_id, paper_uuid, payload, *, asset_sha256=None
    ):
        self.enqueued = (paper_uuid, payload, asset_sha256)
        return {"job_uuid": "job-1", "status": "pending"}

    async def list_worker_scopes(self):
        return [("tenant", "user")]

    async def claim_pdf_ingestion_job(self, worker_id, tenant_id, user_id):
        self.claim_scopes.append((tenant_id, user_id))
        return dict(self.job) if self.job is not None else None

    async def load_pdf_ingestion_input(self, tenant_id, user_id, job):
        return {
            "paper_id": "paper:pdf:one",
            "source": "pdf",
            "title": "Latest user title",
            "authors": ["Latest Author"],
            "abstract": "",
            "published_at": None,
            "doi": None,
            "arxiv_id": None,
            "url": None,
            "in_knowledge_base": True,
            "file_uri": "/app/storage/runtime/uploads/paper.pdf",
            "file_name": "paper.pdf",
            "mime_type": "application/pdf",
            "file_sha256": "abc",
            "file_size": 100,
            "metadata": {"created_from": "web_upload"},
        }

    async def refresh_ingestion_job_lease(self, tenant_id, user_id, job):
        self.lease_refreshes += 1
        return True

    async def complete_ingestion_job(self, tenant_id, user_id, job):
        self.completed = job
        return True

    async def fail_ingestion_job(self, tenant_id, user_id, job, error):
        self.failed = (job, error)
        return (
            "retry"
            if int(job.get("attempt_count") or 0) < int(job.get("max_attempts") or 1)
            else "failed"
        )

    async def mark_parsing_failed(
        self, tenant_id, user_id, paper_uuid, error, *, expected_generation=None
    ):
        self.parsing_failure = (paper_uuid, error)
        return True


class _Ingestion:
    def __init__(self, *, error: Exception | None = None) -> None:
        self.error = error
        self.calls = []

    async def ingest_existing(
        self, tenant_id, user_id, paper, paper_uuid, generation_uuid, claim_guard
    ):
        self.calls.append(
            (tenant_id, user_id, paper, paper_uuid, generation_uuid, claim_guard)
        )
        if self.error:
            raise self.error
        return SimpleNamespace(
            paper=_record(), chunk_count=12, embedding_status="ready",
            parse_status="ready", warning=None,
        )


class PdfIngestionQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.repository = _Repository()

        @asynccontextmanager
        async def tenant_transaction(*_):
            yield object()

        @asynccontextmanager
        async def worker_transaction():
            yield object()

        self.tenant_transaction = tenant_transaction
        self.worker_transaction = worker_transaction

    def _service(self, ingestion=None) -> PdfIngestionQueueService:
        return PdfIngestionQueueService(
            tenant_transaction_factory=self.tenant_transaction,
            worker_transaction_factory=self.worker_transaction,
            repository_factory=lambda _: self.repository,
            ingestion_service=ingestion or _Ingestion(),
            asset_hash_verifier=lambda *_: True,
        )

    def test_asset_hash_verifier_rejects_changed_file_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "paper.pdf"
            path.write_bytes(b"original-pdf-bytes")
            import hashlib

            expected = hashlib.sha256(b"original-pdf-bytes").hexdigest()
            self.assertTrue(verify_asset_sha256(path, expected))
            path.write_bytes(b"changed-pdf-bytes")
            self.assertFalse(verify_asset_sha256(path, expected))

    async def test_enqueue_saves_pdf_asset_and_one_worker_job(self) -> None:
        paper = PaperInput(
            paper_id="paper:pdf:one", source="pdf", title="Queued paper",
            authors=("Alice",), file_uri="/app/storage/runtime/uploads/paper.pdf",
            file_name="paper.pdf", mime_type="application/pdf",
            file_sha256="abc", file_size=100,
            metadata={"created_from": "web_upload"},
        )

        result = await self._service().enqueue("tenant", "user", paper)

        self.assertEqual(result.job_id, "job-1")
        self.assertEqual(result.status, "pending")
        self.assertEqual(self.repository.asset[1], paper.file_uri)
        payload = self.repository.enqueued[1]
        self.assertEqual(payload, {"paper_id": "paper:pdf:one"})
        self.assertEqual(self.repository.enqueued[2], "abc")
        self.assertNotIn("tenant_id", payload)
        self.assertNotIn("user_id", payload)

    async def test_worker_claims_deserializes_and_completes_pdf_job(self) -> None:
        ingestion = _Ingestion()

        result = await self._service(ingestion).process_next("worker-1")

        self.assertEqual(result.status, "completed")
        self.assertEqual(result.chunk_count, 12)
        self.assertEqual(self.repository.completed["job_uuid"], "job-1")
        paper = ingestion.calls[0][2]
        self.assertIsInstance(paper, PaperInput)
        self.assertEqual(paper.authors, ("Latest Author",))
        self.assertEqual(paper.file_uri, "/app/storage/runtime/uploads/paper.pdf")

    async def test_worker_failure_is_retried_without_leaking_api_key(self) -> None:
        ingestion = _Ingestion(error=RuntimeError("failed with sk-secret-value"))

        with patch(
            "app.papers.queued_ingestion.get_settings",
            return_value=SimpleNamespace(rag_embedding_api_key="sk-secret-value"),
        ):
            result = await self._service(ingestion).process_next("worker-1")

        self.assertEqual(result.status, "retry")
        self.assertIsNone(self.repository.completed)
        self.assertNotIn("sk-secret-value", self.repository.failed[1])
        self.assertIsNone(self.repository.parsing_failure)

    async def test_scope_start_rotates_between_polls(self) -> None:
        self.repository.job = None
        self.repository.list_worker_scopes = AsyncMock(
            return_value=[("tenant-a", "user-a"), ("tenant-b", "user-b")]
        )
        service = self._service()

        await service.process_next("worker-1")
        await service.process_next("worker-1")

        self.assertEqual(
            self.repository.claim_scopes,
            [
                ("tenant-a", "user-a"),
                ("tenant-b", "user-b"),
                ("tenant-b", "user-b"),
                ("tenant-a", "user-a"),
            ],
        )

    async def test_final_worker_failure_marks_paper_failed(self) -> None:
        self.repository.job["attempt_count"] = 3
        ingestion = _Ingestion(error=RuntimeError("parser crashed"))

        result = await self._service(ingestion).process_next("worker-1")

        self.assertEqual(result.status, "failed")
        self.assertEqual(self.repository.parsing_failure[0], str(self.repository.record.paper_uuid))
        self.assertIn("parser crashed", self.repository.parsing_failure[1])

    async def test_pdf_store_enqueues_instead_of_running_sync_ingestion(self) -> None:
        queue = SimpleNamespace(enqueue=AsyncMock(return_value=SimpleNamespace(job_id="job-1")))
        rag = SimpleNamespace(index_paper=AsyncMock())
        store = KnowledgeStore(ingestion_queue=queue, rag=rag)
        store.get = AsyncMock(return_value={"paper_id": "paper:pdf:one", "ingestion_status": "parsing"})
        paper = McpPaperRecord(
            paper_id="paper:pdf:one", tenant_id="tenant", user_id="user",
            source="pdf", title="Queued paper",
            metadata={
                "content_type": "application/pdf",
                "file_path": "/app/storage/runtime/uploads/paper.pdf",
            },
        )

        saved = await store.save_paper(paper)

        queue.enqueue.assert_awaited_once()
        rag.index_paper.assert_not_awaited()
        self.assertEqual(saved["ingestion_status"], "parsing")

    async def test_manual_store_keeps_synchronous_ingestion(self) -> None:
        queue = SimpleNamespace(enqueue=AsyncMock())
        rag = SimpleNamespace(index_paper=AsyncMock())
        store = KnowledgeStore(ingestion_queue=queue, rag=rag)
        store.get = AsyncMock(return_value={"paper_id": "paper:manual:one"})
        paper = McpPaperRecord(
            paper_id="paper:manual:one", tenant_id="tenant", user_id="user",
            source="manual", title="Manual paper", full_text="content",
        )

        await store.save_paper(paper)

        rag.index_paper.assert_awaited_once()
        queue.enqueue.assert_not_awaited()

    async def test_worker_runner_polls_pdf_queue(self) -> None:
        from app.workers.runner import _process_one_pdf_ingestion

        queued = SimpleNamespace(job_id="job-1", status="completed", chunk_count=12)
        with patch(
            "app.workers.runner.pdf_ingestion_queue_service.process_next",
            new=AsyncMock(return_value=queued),
        ) as process_next:
            processed = await _process_one_pdf_ingestion("worker-1")

        self.assertTrue(processed)
        process_next.assert_awaited_once_with("worker-1")


if __name__ == "__main__":
    unittest.main()
