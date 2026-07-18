from __future__ import annotations

import asyncio
from dataclasses import dataclass
from dataclasses import replace
import hashlib
from pathlib import Path
from typing import Any, Callable

from app.config import get_settings
from app.db.session import tenant_transaction
from app.papers.chunking import chunk_sections, chunk_text
from app.papers.models import PaperInput, PaperRecord
from app.papers.parsing import (
    LEGACY_PARSER_NAME,
    STRUCTURED_PARSER_NAME,
    ParsedBlock,
    ParsedPage,
    ParsedPaper,
    ParsedSection,
    parse_pdf,
    parse_pdf_legacy,
)
from app.papers.repository import PaperRepository
from app.retrieval.embedding import EmbeddingUnavailable, QwenEmbeddingClient


@dataclass(frozen=True)
class IngestionResult:
    paper: PaperRecord
    chunk_count: int
    embedding_status: str
    warning: str | None = None
    parse_status: str = "manual"
    parser_strategy: str = "manual_text"
    chunk_strategy: str = "legacy_fixed"


class PaperIngestionService:
    def __init__(
        self,
        embedding: QwenEmbeddingClient | None = None,
        *,
        parser: Callable[[Path], ParsedPaper] | None = None,
        transaction_factory: Callable[..., Any] = tenant_transaction,
        repository_factory: Callable[..., PaperRepository] = PaperRepository,
    ) -> None:
        self.embedding = embedding
        self.parser = parser
        self.transaction_factory = transaction_factory
        self.repository_factory = repository_factory

    def _embedding(self) -> QwenEmbeddingClient:
        if self.embedding is None:
            self.embedding = QwenEmbeddingClient.from_settings()
        return self.embedding

    async def ingest(self, tenant_id: str, user_id: str, paper: PaperInput) -> IngestionResult:
        settings = get_settings()
        is_pdf = bool(
            paper.file_uri
            and (
                (paper.mime_type or "").lower() == "application/pdf"
                or Path(paper.file_uri).suffix.lower() == ".pdf"
            )
        )
        updated_from = str(paper.metadata.get("updated_from") or "")
        manual_edit = updated_from.startswith("inline_")
        parser_name = "manual_text"
        parser_version = "1"
        prepared = paper

        if is_pdf and not manual_edit:
            parser = self.parser
            if parser is None:
                parser = parse_pdf_legacy if settings.pdf_parse_strategy == LEGACY_PARSER_NAME else parse_pdf
            parsed = await asyncio.to_thread(parser, Path(str(paper.file_uri)))
            parser_info = dict(parsed.manifest.get("parser") or {})
            parser_name = str(parser_info.get("name") or settings.pdf_parse_strategy)
            parser_version = str(parser_info.get("version") or "1")
            parse_metadata = {
                **dict(paper.metadata),
                "parsing": {
                    "status": parsed.status,
                    "parser_name": parser_name,
                    "parser_version": parser_version,
                    "quality_score": parsed.quality_score,
                    "warnings": list(parsed.warnings),
                },
            }
            prepared = replace(
                paper,
                full_text=parsed.full_text,
                doi=paper.doi or parsed.metadata.get("doi"),
                arxiv_id=paper.arxiv_id or parsed.metadata.get("arxiv_id"),
                metadata=parse_metadata,
            )
        else:
            parsed = _manual_parsed_paper(paper.full_text)

        if parsed.status not in {"ready", "manual"}:
            warning = "; ".join(parsed.warnings) or parsed.error or "PDF parsing failed"
            async with self.transaction_factory(tenant_id, user_id) as session:
                repository = self.repository_factory(session)
                record = await repository.save(tenant_id, user_id, prepared)
                await repository.save_asset(tenant_id, user_id, record.paper_uuid, prepared)
                await repository.mark_parsing_failed(tenant_id, user_id, record.paper_uuid, warning)
            return IngestionResult(
                paper=record,
                chunk_count=0,
                embedding_status="not_indexed",
                warning=warning,
                parse_status=parsed.status,
                parser_strategy=parser_name,
                chunk_strategy=settings.rag_chunk_strategy,
            )

        if settings.rag_chunk_strategy == STRUCTURED_PARSER_NAME:
            chunks = chunk_sections(parsed.sections, settings.rag_chunk_size, settings.rag_chunk_overlap)
        else:
            chunks = chunk_text(prepared.full_text, settings.rag_chunk_size, settings.rag_chunk_overlap)
        content_version = None
        async with self.transaction_factory(tenant_id, user_id) as session:
            repository = self.repository_factory(session)
            record = await repository.save(tenant_id, user_id, prepared)
            await repository.save_asset(tenant_id, user_id, record.paper_uuid, prepared)
            if chunks:
                content_hash = hashlib.sha256(prepared.full_text.encode("utf-8")).hexdigest()
                content_version = await repository.replace_content(
                    tenant_id,
                    user_id,
                    record.paper_uuid,
                    prepared.full_text,
                    content_hash,
                    chunks,
                    extraction_method=(
                        parsed.pages[0].extraction_method if parsed.pages else "manual_text"
                    ),
                    parsed=parsed,
                    parser_name=parser_name,
                    parser_version=parser_version,
                    chunk_strategy=settings.rag_chunk_strategy,
                    chunker_version="1",
                )

        if content_version is None:
            return IngestionResult(
                record,
                0,
                "metadata_only",
                parse_status=parsed.status,
                parser_strategy=parser_name,
                chunk_strategy=settings.rag_chunk_strategy,
            )

        try:
            embedding_client = self._embedding()
            vectors = await embedding_client.embed(
                [chunk.embedding_text(prepared.title) for chunk in chunks]
            )
            async with self.transaction_factory(tenant_id, user_id) as session:
                await self.repository_factory(session).set_embeddings(
                    tenant_id,
                    user_id,
                    record.paper_uuid,
                    content_version.content_uuid,
                    vectors,
                    model=embedding_client.model,
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
        return IngestionResult(
            refreshed or record,
            len(chunks),
            embedding_status,
            warning,
            parsed.status,
            parser_name,
            settings.rag_chunk_strategy,
        )


def _manual_parsed_paper(text: str) -> ParsedPaper:
    full_text = (text or "").strip()
    text_hash = hashlib.sha256(full_text.encode("utf-8")).hexdigest()
    if not full_text:
        return ParsedPaper(
            full_text="",
            pages=(),
            sections=(),
            metadata={},
            manifest={"parser": {"name": "manual_text", "version": "1"}},
            status="manual",
            quality_score=1.0,
        )
    block = ParsedBlock(1, "body", full_text, (0.0, 0.0, 0.0, 0.0), 0)
    page = ParsedPage(1, full_text, text_hash, len(full_text), "manual_text", "usable", (block,))
    section = ParsedSection(
        "document", 0, "document", "Document", 1, 1,
        full_text, 0, len(full_text), text_hash,
    )
    return ParsedPaper(
        full_text=full_text,
        pages=(page,),
        sections=(section,),
        metadata={"language": "zh" if any("\u4e00" <= char <= "\u9fff" for char in full_text) else "en"},
        manifest={
            "parser": {"name": "manual_text", "version": "1"},
            "coverage": {"total_pages": 1, "pages_extracted": 1, "text_truncated": False},
            "text_hash": text_hash,
        },
        status="manual",
        quality_score=1.0,
    )


paper_ingestion_service = PaperIngestionService()
