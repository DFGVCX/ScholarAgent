from __future__ import annotations

import asyncio
from collections.abc import Mapping
from contextlib import suppress
from dataclasses import dataclass
import hashlib
import hmac
from pathlib import Path
import re
from typing import Any, Callable

from app.config import get_settings
from app.db.session import tenant_transaction, worker_transaction
from app.papers.models import PaperInput, PaperRecord
from app.papers.repository import PaperRepository


@dataclass(frozen=True)
class PdfIngestionJobResult:
    job_id: str
    status: str
    paper: PaperRecord | None = None
    chunk_count: int = 0
    parse_status: str | None = None
    embedding_status: str | None = None
    error: str | None = None


def verify_asset_sha256(path: Path, expected_sha256: str) -> bool:
    expected = str(expected_sha256 or "").strip().lower()
    if len(expected) != 64 or not path.is_file():
        return False
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return hmac.compare_digest(digest.hexdigest(), expected)


def paper_input_from_row(payload: Mapping[str, Any]) -> PaperInput:
    metadata = payload.get("metadata")
    return PaperInput(
        paper_id=str(payload.get("paper_id") or ""),
        source=str(payload.get("source") or ""),
        title=str(payload.get("title") or ""),
        authors=tuple(str(item) for item in (payload.get("authors") or ())),
        abstract=str(payload.get("abstract") or ""),
        full_text=str(payload.get("full_text") or ""),
        published_at=payload.get("published_at"),
        doi=str(payload["doi"]) if payload.get("doi") else None,
        arxiv_id=str(payload["arxiv_id"]) if payload.get("arxiv_id") else None,
        url=str(payload["url"]) if payload.get("url") else None,
        file_uri=str(payload["file_uri"]) if payload.get("file_uri") else None,
        file_name=str(payload["file_name"]) if payload.get("file_name") else None,
        mime_type=str(payload["mime_type"]) if payload.get("mime_type") else None,
        file_sha256=str(payload["file_sha256"]) if payload.get("file_sha256") else None,
        file_size=int(payload["file_size"]) if payload.get("file_size") is not None else None,
        in_knowledge_base=bool(payload.get("in_knowledge_base", True)),
        metadata=dict(metadata) if isinstance(metadata, Mapping) else {},
    )


def _redact_error(error: Exception) -> str:
    detail = str(error)
    secret = get_settings().rag_embedding_api_key
    if secret:
        detail = detail.replace(secret, "***")
    detail = re.sub(r"(?i)sk-[a-z0-9_-]{4,}", "sk-***", detail)
    return detail[:4000]


class PdfIngestionQueueService:
    def __init__(
        self,
        *,
        tenant_transaction_factory: Callable[..., Any] = tenant_transaction,
        worker_transaction_factory: Callable[..., Any] = worker_transaction,
        repository_factory: Callable[..., PaperRepository] = PaperRepository,
        ingestion_service: Any | None = None,
        lease_heartbeat_seconds: float = 60.0,
        asset_hash_verifier: Callable[[Path, str], bool] = verify_asset_sha256,
    ) -> None:
        self.tenant_transaction_factory = tenant_transaction_factory
        self.worker_transaction_factory = worker_transaction_factory
        self.repository_factory = repository_factory
        self.ingestion_service = ingestion_service
        self.lease_heartbeat_seconds = max(0.01, float(lease_heartbeat_seconds))
        self.asset_hash_verifier = asset_hash_verifier
        self._scope_cursor = 0

    @staticmethod
    def _require_pdf(paper: PaperInput) -> None:
        suffix = Path(paper.file_uri or "").suffix.lower()
        if not paper.file_uri or not (
            (paper.mime_type or "").lower() == "application/pdf" or suffix == ".pdf"
        ):
            raise ValueError("PDF ingestion queue requires a PDF file asset")

    def _ingestion(self) -> Any:
        if self.ingestion_service is not None:
            return self.ingestion_service
        # Keep the MCP image slim: only the worker needs to import and execute
        # the Docling-capable ingestion service.
        from app.papers.ingestion import paper_ingestion_service

        return paper_ingestion_service

    async def enqueue(
        self, tenant_id: str, user_id: str, paper: PaperInput
    ) -> PdfIngestionJobResult:
        self._require_pdf(paper)
        payload = {"paper_id": paper.paper_id}
        async with self.tenant_transaction_factory(tenant_id, user_id) as session:
            repository = self.repository_factory(session)
            record = await repository.save(tenant_id, user_id, paper)
            await repository.save_asset(tenant_id, user_id, record.paper_uuid, paper)
            job = await repository.enqueue_pdf_ingestion_job(
                tenant_id,
                user_id,
                record.paper_uuid,
                payload,
                asset_sha256=paper.file_sha256,
            )
        return PdfIngestionJobResult(
            job_id=str(job["job_uuid"]),
            status=str(job.get("status") or "pending"),
            paper=record,
        )

    async def process_next(self, worker_id: str) -> PdfIngestionJobResult | None:
        async with self.worker_transaction_factory() as session:
            scopes = await self.repository_factory(session).list_worker_scopes()
        job = None
        if scopes:
            start = self._scope_cursor % len(scopes)
            ordered_scopes = scopes[start:] + scopes[:start]
        else:
            start = 0
            ordered_scopes = []
        claimed_offset = None
        for offset, (tenant_id, user_id) in enumerate(ordered_scopes):
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                job = await self.repository_factory(session).claim_pdf_ingestion_job(
                    worker_id, tenant_id, user_id
                )
            if job is not None:
                claimed_offset = offset
                break
        if scopes:
            advance = (claimed_offset + 1) if claimed_offset is not None else 1
            self._scope_cursor = (start + advance) % len(scopes)
        if job is None:
            return None

        job_id = str(job["job_uuid"])
        tenant_id = str(job["tenant_id"])
        user_id = str(job["user_id"])
        stop_heartbeat = asyncio.Event()
        heartbeat = asyncio.create_task(
            self._maintain_lease(tenant_id, user_id, job, stop_heartbeat)
        )
        try:
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                row = await self.repository_factory(session).load_pdf_ingestion_input(
                    tenant_id, user_id, job
                )
            if row is None:
                raise RuntimeError("PDF ingestion generation or worker lease is no longer current")
            asset_path = Path(str(row.get("file_uri") or ""))
            expected_sha256 = str(row.get("file_sha256") or "")
            verified = await asyncio.to_thread(
                self.asset_hash_verifier, asset_path, expected_sha256
            )
            if not verified:
                raise RuntimeError(
                    "PDF asset bytes no longer match the queued SHA-256 generation"
                )
            paper = paper_input_from_row(row)
            self._require_pdf(paper)

            async def claim_guard(repository: PaperRepository) -> None:
                await repository.assert_pdf_ingestion_claim(
                    tenant_id, user_id, job
                )

            result = await self._ingestion().ingest_existing(
                tenant_id,
                user_id,
                paper,
                job["paper_uuid"],
                job["generation_uuid"],
                claim_guard,
            )
            if result.parse_status not in {"ready", "manual"}:
                raise RuntimeError(result.warning or "PDF parsing did not produce ready content")
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                completed = await self.repository_factory(session).complete_ingestion_job(
                    tenant_id, user_id, job
                )
            if not completed:
                return PdfIngestionJobResult(job_id=job_id, status="superseded")
            return PdfIngestionJobResult(
                job_id=job_id,
                status="completed",
                paper=result.paper,
                chunk_count=int(result.chunk_count),
                parse_status=str(result.parse_status),
                embedding_status=str(result.embedding_status),
            )
        except Exception as exc:
            error = _redact_error(exc)
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                repository = self.repository_factory(session)
                recorded_status = await repository.fail_ingestion_job(
                    tenant_id, user_id, job, error
                )
                if recorded_status == "failed":
                    await repository.mark_parsing_failed(
                        tenant_id,
                        user_id,
                        job["paper_uuid"],
                        error,
                        expected_generation=job.get("generation_uuid"),
                    )
            if recorded_status is None:
                return PdfIngestionJobResult(job_id=job_id, status="superseded")
            return PdfIngestionJobResult(
                job_id=job_id, status=recorded_status, error=error
            )
        finally:
            stop_heartbeat.set()
            with suppress(Exception):
                await heartbeat

    async def _maintain_lease(
        self,
        tenant_id: str,
        user_id: str,
        job: Mapping[str, Any],
        stop: asyncio.Event,
    ) -> None:
        while not stop.is_set():
            try:
                await asyncio.wait_for(stop.wait(), timeout=self.lease_heartbeat_seconds)
                return
            except TimeoutError:
                pass
            async with self.tenant_transaction_factory(tenant_id, user_id) as session:
                refreshed = await self.repository_factory(
                    session
                ).refresh_ingestion_job_lease(tenant_id, user_id, job)
            if not refreshed:
                return


pdf_ingestion_queue_service = PdfIngestionQueueService()
