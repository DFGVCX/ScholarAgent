from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
import json
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.papers.chunking import ChunkDraft
from app.papers.models import ContentVersion, PaperInput, PaperRecord, normalize_arxiv_id, normalize_doi
from app.papers.parsing import ParsedPaper


_PAPER_COLUMNS = """
paper_uuid, tenant_id, user_id, paper_id, source, title, authors, abstract,
published_at, normalized_doi, normalized_arxiv_id, canonical_url,
in_knowledge_base, ingestion_status, current_content_version, metadata,
created_at, updated_at
"""


class PaperRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, tenant_id: str, user_id: str, paper_id: str) -> PaperRecord | None:
        result = await self.session.execute(
            text(
                f"SELECT {_PAPER_COLUMNS} FROM papers "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_id=:paper_id "
                "AND deleted_at IS NULL"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "paper_id": paper_id},
        )
        row = result.mappings().first()
        return self._record(row) if row else None

    async def list(
        self, tenant_id: str, user_id: str, *, query: str = "", limit: int = 50
    ) -> list[PaperRecord]:
        result = await self.session.execute(
            text(
                f"SELECT {_PAPER_COLUMNS} FROM papers "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND deleted_at IS NULL "
                "AND (:query='' OR title ILIKE :pattern OR abstract ILIKE :pattern OR paper_id ILIKE :pattern) "
                "ORDER BY updated_at DESC LIMIT :limit"
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "query": query.strip(),
                "pattern": f"%{query.strip()}%",
                "limit": max(1, min(limit, 200)),
            },
        )
        return [self._record(row) for row in result.mappings().all()]

    async def get_document(
        self, tenant_id: str, user_id: str, paper_id: str
    ) -> dict[str, Any] | None:
        rows = await self.list_documents(tenant_id, user_id, query=paper_id, limit=10)
        return next((row for row in rows if row["paper_id"] == paper_id), None)

    async def list_documents(
        self, tenant_id: str, user_id: str, *, query: str = "", limit: int = 50
    ) -> list[dict[str, Any]]:
        result = await self.session.execute(
            text(
                """SELECT p.paper_id, p.source, p.title, p.authors, p.abstract,
                    pc.full_text, p.published_at, p.normalized_doi AS doi,
                    p.normalized_arxiv_id AS arxiv_id, p.canonical_url AS url,
                    p.in_knowledge_base, p.ingestion_status, p.metadata,
                    pc.parse_status, pc.parser_name, pc.parser_version,
                    pc.chunk_strategy, pc.chunker_version, pc.extraction_quality,
                    pc.parse_manifest,
                    COALESCE((SELECT COUNT(*) FROM paper_pages pp
                        WHERE pp.content_uuid=pc.content_uuid AND pp.tenant_id=pc.tenant_id
                          AND pp.user_id=pc.user_id), 0) AS page_count,
                    COALESCE((SELECT COUNT(*) FROM paper_sections ps
                        WHERE ps.content_uuid=pc.content_uuid AND ps.tenant_id=pc.tenant_id
                          AND ps.user_id=pc.user_id), 0) AS section_count,
                    asset.file_uri, asset.file_name, asset.mime_type, asset.file_size
                FROM papers p
                LEFT JOIN paper_contents pc ON pc.paper_uuid=p.paper_uuid
                    AND pc.tenant_id=p.tenant_id AND pc.user_id=p.user_id
                    AND pc.content_version=p.current_content_version
                LEFT JOIN LATERAL (
                    SELECT file_uri, file_name, mime_type, file_size FROM paper_assets a
                    WHERE a.paper_uuid=p.paper_uuid AND a.tenant_id=p.tenant_id AND a.user_id=p.user_id
                    ORDER BY a.created_at DESC LIMIT 1
                ) asset ON true
                WHERE p.tenant_id=:tenant_id AND p.user_id=:user_id AND p.deleted_at IS NULL
                    AND (:query='' OR p.paper_id ILIKE :pattern OR p.title ILIKE :pattern
                         OR p.abstract ILIKE :pattern)
                ORDER BY p.updated_at DESC LIMIT :limit"""
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "query": query.strip(),
                "pattern": f"%{query.strip()}%",
                "limit": max(1, min(limit, 200)),
            },
        )
        documents: list[dict[str, Any]] = []
        for row in result.mappings().all():
            authors = row.get("authors") or []
            metadata = dict(row.get("metadata") or {})
            if row.get("file_uri"):
                metadata.update(
                    {
                        "file_path": row["file_uri"],
                        "file_name": row.get("file_name"),
                        "content_type": row.get("mime_type"),
                        "content_length": row.get("file_size"),
                    }
                )
            published_at = row.get("published_at")
            documents.append(
                {
                    "paper_id": row["paper_id"],
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "source": row["source"],
                    "title": row["title"],
                    "authors": list(authors),
                    "abstract": row.get("abstract") or "",
                    "full_text": row.get("full_text") or "",
                    "published_at": published_at.isoformat() if hasattr(published_at, "isoformat") else published_at,
                    "doi": row.get("doi"),
                    "arxiv_id": row.get("arxiv_id"),
                    "url": row.get("url"),
                    "file_path": row.get("file_uri") or "",
                    "in_knowledge_base": bool(row.get("in_knowledge_base")),
                    "ingestion_status": row.get("ingestion_status"),
                    "parsing": {
                        "status": row.get("parse_status"),
                        "parser_name": row.get("parser_name"),
                        "parser_version": row.get("parser_version"),
                        "chunk_strategy": row.get("chunk_strategy"),
                        "chunker_version": row.get("chunker_version"),
                        "quality_score": row.get("extraction_quality"),
                        "page_count": int(row.get("page_count") or 0),
                        "section_count": int(row.get("section_count") or 0),
                        "manifest": dict(row.get("parse_manifest") or {}),
                    },
                    "metadata": metadata,
                }
            )
        return documents

    async def save(self, tenant_id: str, user_id: str, paper: PaperInput) -> PaperRecord:
        result = await self.session.execute(
            text(
                """INSERT INTO papers (
                    tenant_id, user_id, paper_id, source, source_identifier, normalized_doi,
                    normalized_arxiv_id, title, authors, abstract, published_at, canonical_url,
                    in_knowledge_base, ingestion_status, metadata
                ) VALUES (
                    :tenant_id, :user_id, :paper_id, :source, :source_identifier, :doi,
                    :arxiv_id, :title, CAST(:authors AS jsonb), :abstract, :published_at, :url,
                    :in_kb, :status, CAST(:metadata AS jsonb)
                ) ON CONFLICT (tenant_id, user_id, paper_id) DO UPDATE SET
                    source=EXCLUDED.source, source_identifier=EXCLUDED.source_identifier,
                    normalized_doi=EXCLUDED.normalized_doi,
                    normalized_arxiv_id=EXCLUDED.normalized_arxiv_id, title=EXCLUDED.title,
                    authors=EXCLUDED.authors, abstract=EXCLUDED.abstract,
                    published_at=EXCLUDED.published_at, canonical_url=EXCLUDED.canonical_url,
                    in_knowledge_base=EXCLUDED.in_knowledge_base, metadata=EXCLUDED.metadata,
                    updated_at=now(), deleted_at=NULL
                RETURNING """ + _PAPER_COLUMNS
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "paper_id": paper.paper_id.strip(),
                "source": paper.source.strip(),
                "source_identifier": normalize_doi(paper.doi) or normalize_arxiv_id(paper.arxiv_id),
                "doi": normalize_doi(paper.doi),
                "arxiv_id": normalize_arxiv_id(paper.arxiv_id),
                "title": paper.title.strip(),
                "authors": json.dumps(list(paper.authors), ensure_ascii=False),
                "abstract": paper.abstract,
                "published_at": paper.published_at,
                "url": paper.url,
                "in_kb": paper.in_knowledge_base,
                "status": "metadata_only",
                "metadata": json.dumps(dict(paper.metadata), ensure_ascii=False),
            },
        )
        row = result.mappings().first()
        if row is None:
            raise RuntimeError("paper upsert returned no row")
        return self._record(row)

    async def set_knowledge_base(
        self, tenant_id: str, user_id: str, paper_id: str, enabled: bool
    ) -> bool:
        result = await self.session.execute(
            text(
                "UPDATE papers SET in_knowledge_base=:enabled, updated_at=now() "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_id=:paper_id "
                "AND deleted_at IS NULL RETURNING paper_uuid"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "paper_id": paper_id, "enabled": enabled},
        )
        return result.first() is not None

    async def soft_delete(self, tenant_id: str, user_id: str, paper_id: str) -> bool:
        result = await self.session.execute(
            text(
                "UPDATE papers SET deleted_at=now(), in_knowledge_base=false, updated_at=now() "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_id=:paper_id "
                "AND deleted_at IS NULL RETURNING paper_uuid"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "paper_id": paper_id},
        )
        return result.first() is not None

    async def replace_content(
        self,
        tenant_id: str,
        user_id: str,
        paper_uuid: UUID,
        full_text: str,
        content_hash: str,
        chunks: Sequence[ChunkDraft],
        *,
        extraction_method: str,
        parsed: ParsedPaper | None = None,
        parser_name: str = "legacy_fixed",
        parser_version: str = "1",
        chunk_strategy: str = "legacy_fixed",
        chunker_version: str = "1",
    ) -> ContentVersion:
        locked = await self.session.execute(
            text(
                "SELECT current_content_version FROM papers WHERE tenant_id=:tenant_id "
                "AND user_id=:user_id AND paper_uuid=:paper_uuid AND deleted_at IS NULL FOR UPDATE"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "paper_uuid": paper_uuid},
        )
        row = locked.mappings().first()
        if row is None:
            raise KeyError("paper not found")
        version = int(row["current_content_version"]) + 1
        inserted = await self.session.execute(
            text(
                """INSERT INTO paper_contents (
                    tenant_id, user_id, paper_uuid, content_version, full_text, content_hash,
                    language, extraction_method, extraction_quality, parser_name, parser_version,
                    chunk_strategy, chunker_version, parse_status, parse_manifest
                ) VALUES (
                    :tenant_id, :user_id, :paper_uuid, :version, :full_text, :content_hash,
                    :language, :extraction_method, :extraction_quality, :parser_name, :parser_version,
                    :chunk_strategy, :chunker_version, :parse_status, CAST(:parse_manifest AS jsonb)
                ) RETURNING content_uuid"""
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "paper_uuid": paper_uuid,
                "version": version,
                "full_text": full_text,
                "content_hash": content_hash,
                "extraction_method": extraction_method,
                "language": str((parsed.metadata if parsed else {}).get("language") or "") or None,
                "extraction_quality": parsed.quality_score if parsed else None,
                "parser_name": parser_name,
                "parser_version": parser_version,
                "chunk_strategy": chunk_strategy,
                "chunker_version": chunker_version,
                "parse_status": parsed.status if parsed else "ready",
                "parse_manifest": json.dumps(parsed.to_manifest() if parsed else {}, ensure_ascii=False),
            },
        )
        content_uuid = inserted.scalar_one()
        if parsed is not None:
            for page in parsed.pages:
                await self.session.execute(
                    text(
                        """INSERT INTO paper_pages (
                            tenant_id, user_id, paper_uuid, content_uuid, content_version,
                            page_number, text, text_hash, extraction_method, quality_status,
                            searchable_chars, blocks
                        ) VALUES (
                            :tenant_id, :user_id, :paper_uuid, :content_uuid, :version,
                            :page_number, :text, :text_hash, :page_extraction_method,
                            :quality_status, :searchable_chars, CAST(:blocks AS jsonb))"""
                    ),
                    {
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "paper_uuid": paper_uuid,
                        "content_uuid": content_uuid,
                        "version": version,
                        "page_number": page.page_number,
                        "text": page.text,
                        "text_hash": page.text_hash,
                        "page_extraction_method": page.extraction_method,
                        "quality_status": page.quality_status,
                        "searchable_chars": page.searchable_chars,
                        "blocks": json.dumps([block.to_dict() for block in page.blocks], ensure_ascii=False),
                    },
                )
            for section in parsed.sections:
                await self.session.execute(
                    text(
                        """INSERT INTO paper_sections (
                            tenant_id, user_id, paper_uuid, content_uuid, content_version,
                            section_id, section_index, kind, title, page_start, page_end,
                            content, char_count, char_start, char_end, text_hash
                        ) VALUES (
                            :tenant_id, :user_id, :paper_uuid, :content_uuid, :version,
                            :section_id, :section_index, :kind, :title, :page_start, :page_end,
                            :content, :char_count, :char_start, :char_end, :text_hash)"""
                    ),
                    {
                        "tenant_id": tenant_id,
                        "user_id": user_id,
                        "paper_uuid": paper_uuid,
                        "content_uuid": content_uuid,
                        "version": version,
                        "section_id": section.section_id,
                        "section_index": section.index,
                        "kind": section.kind,
                        "title": section.title,
                        "page_start": section.page_start,
                        "page_end": section.page_end,
                        "content": section.text,
                        "char_count": len(section.text),
                        "char_start": section.char_start,
                        "char_end": section.char_end,
                        "text_hash": section.text_hash,
                    },
                )
        for chunk in chunks:
            await self.session.execute(
                text(
                    """INSERT INTO paper_chunks (
                        tenant_id, user_id, paper_uuid, content_uuid, content_version,
                        chunk_index, section_id, section_path, page_start, page_end,
                        char_start, char_end, content, content_hash, token_count
                    ) VALUES (
                        :tenant_id, :user_id, :paper_uuid, :content_uuid, :version,
                        :position, :section_id, :section_path, :page_start, :page_end,
                        :char_start, :char_end, :content, :content_hash, :token_count)"""
                ),
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "paper_uuid": paper_uuid,
                    "content_uuid": content_uuid,
                    "version": version,
                    "position": chunk.position,
                    "section_id": chunk.section_id,
                    "section_path": chunk.section_path,
                    "page_start": chunk.page_start,
                    "page_end": chunk.page_end,
                    "char_start": chunk.char_start,
                    "char_end": chunk.char_end,
                    "content": chunk.content,
                    "content_hash": chunk.content_hash,
                    "token_count": chunk.token_count,
                },
            )
        await self.session.execute(
            text(
                "UPDATE papers SET current_content_version=:version, ingestion_status='embedding', "
                "last_error=NULL, updated_at=now() WHERE tenant_id=:tenant_id AND user_id=:user_id "
                "AND paper_uuid=:paper_uuid"
            ),
            {"version": version, "tenant_id": tenant_id, "user_id": user_id, "paper_uuid": paper_uuid},
        )
        return ContentVersion(
            paper_uuid,
            content_uuid,
            version,
            len(chunks),
            parsed.status if parsed else "ready",
            parser_name,
            parser_version,
            chunk_strategy,
            chunker_version,
        )

    async def save_asset(self, tenant_id: str, user_id: str, paper_uuid: UUID, paper: PaperInput) -> None:
        if not paper.file_uri:
            return
        if not paper.file_sha256 or paper.file_size is None or not paper.mime_type:
            raise ValueError("file_uri requires file_sha256, file_size, and mime_type")
        await self.session.execute(
            text(
                """INSERT INTO paper_assets (
                    tenant_id, user_id, paper_uuid, file_uri, file_name, mime_type,
                    sha256, file_size, validation_status
                ) VALUES (
                    :tenant_id, :user_id, :paper_uuid, :file_uri, :file_name, :mime_type,
                    :sha256, :file_size, 'valid'
                ) ON CONFLICT (tenant_id, user_id, sha256) DO UPDATE SET
                    paper_uuid=EXCLUDED.paper_uuid, file_uri=EXCLUDED.file_uri,
                    file_name=EXCLUDED.file_name, mime_type=EXCLUDED.mime_type,
                    file_size=EXCLUDED.file_size, validation_status='valid', updated_at=now()"""
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "paper_uuid": paper_uuid,
                "file_uri": paper.file_uri,
                "file_name": paper.file_name or paper.file_uri.rsplit("/", 1)[-1],
                "mime_type": paper.mime_type,
                "sha256": paper.file_sha256,
                "file_size": paper.file_size,
            },
        )

    async def set_embeddings(
        self,
        tenant_id: str,
        user_id: str,
        paper_uuid: UUID,
        content_uuid: UUID,
        embeddings: Sequence[Sequence[float]],
        *,
        model: str,
    ) -> None:
        for position, embedding in enumerate(embeddings):
            vector = "[" + ",".join(format(float(value), ".9g") for value in embedding) + "]"
            await self.session.execute(
                text(
                    "UPDATE paper_chunks SET embedding=CAST(:embedding AS vector), "
                    "embedding_model=:model, embedding_status='ready', embedding_error=NULL, updated_at=now() "
                    "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_uuid=:paper_uuid "
                    "AND content_uuid=:content_uuid AND chunk_index=:position"
                ),
                {
                    "embedding": vector,
                    "model": model,
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "paper_uuid": paper_uuid,
                    "content_uuid": content_uuid,
                    "position": position,
                },
            )
        await self.session.execute(
            text(
                "UPDATE papers SET ingestion_status='ready', last_error=NULL, updated_at=now() "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_uuid=:paper_uuid"
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "paper_uuid": paper_uuid},
        )

    async def mark_embedding_failed(
        self, tenant_id: str, user_id: str, paper_uuid: UUID, content_uuid: UUID, error: str
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE paper_chunks SET embedding_status='failed', embedding_error=:error, updated_at=now() "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_uuid=:paper_uuid "
                "AND content_uuid=:content_uuid"
            ),
            {
                "error": error[:4000],
                "tenant_id": tenant_id,
                "user_id": user_id,
                "paper_uuid": paper_uuid,
                "content_uuid": content_uuid,
            },
        )
        await self.session.execute(
            text(
                "UPDATE papers SET ingestion_status='failed', last_error=:error, updated_at=now() "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_uuid=:paper_uuid"
            ),
            {"error": error[:4000], "tenant_id": tenant_id, "user_id": user_id, "paper_uuid": paper_uuid},
        )

    async def mark_parsing_failed(
        self, tenant_id: str, user_id: str, paper_uuid: UUID, error: str
    ) -> None:
        await self.session.execute(
            text(
                "UPDATE papers SET ingestion_status='failed', last_error=:error, updated_at=now() "
                "WHERE tenant_id=:tenant_id AND user_id=:user_id AND paper_uuid=:paper_uuid"
            ),
            {
                "error": error[:4000],
                "tenant_id": tenant_id,
                "user_id": user_id,
                "paper_uuid": paper_uuid,
            },
        )

    async def mark_embeddings_stale(
        self, tenant_id: str, user_id: str, active_model: str, *, force: bool = False
    ) -> int:
        result = await self.session.execute(
            text(
                """UPDATE paper_chunks c
                SET embedding=NULL, embedding_status='stale', embedding_error=NULL, updated_at=now()
                FROM papers p
                WHERE c.paper_uuid=p.paper_uuid
                  AND c.tenant_id=p.tenant_id AND c.user_id=p.user_id
                  AND c.tenant_id=:tenant_id AND c.user_id=:user_id
                  AND p.deleted_at IS NULL
                  AND c.content_version=p.current_content_version
                  AND c.embedding_status='ready'
                  AND (:force OR c.embedding_model IS DISTINCT FROM :active_model)"""
            ),
            {
                "tenant_id": tenant_id,
                "user_id": user_id,
                "active_model": active_model,
                "force": force,
            },
        )
        return int(getattr(result, "rowcount", 0) or 0)

    async def embedding_stats(
        self, tenant_id: str, user_id: str, active_model: str
    ) -> dict[str, int]:
        result = await self.session.execute(
            text(
                """SELECT
                    COUNT(*) FILTER (
                        WHERE c.embedding_status='ready'
                          AND c.embedding_model=:active_model
                          AND c.embedding IS NOT NULL
                    ) AS ready,
                    COUNT(*) FILTER (
                        WHERE c.embedding_status='stale'
                           OR (c.embedding_status='ready'
                               AND c.embedding_model IS DISTINCT FROM :active_model)
                    ) AS stale,
                    COUNT(*) FILTER (WHERE c.embedding_status='failed') AS failed,
                    COUNT(*) FILTER (WHERE c.embedding_status='pending') AS pending
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                  AND p.deleted_at IS NULL
                  AND c.content_version=p.current_content_version"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "active_model": active_model},
        )
        row = result.mappings().first() or {}
        return {
            key: int(row.get(key) or 0)
            for key in ("ready", "stale", "failed", "pending")
        }

    async def enqueue_reembedding_jobs(
        self, tenant_id: str, user_id: str
    ) -> dict[str, int]:
        existing_result = await self.session.execute(
            text(
                """SELECT COUNT(DISTINCT c.paper_uuid) AS existing
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                  AND p.deleted_at IS NULL
                  AND c.content_version=p.current_content_version
                  AND c.embedding_status IN ('stale','failed')
                  AND EXISTS (
                      SELECT 1 FROM paper_ingestion_jobs j
                      WHERE j.tenant_id=c.tenant_id AND j.user_id=c.user_id
                        AND j.paper_uuid=c.paper_uuid AND j.job_type='reembed'
                        AND j.status IN ('pending','running','retry')
                  )"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        existing_row = existing_result.mappings().first() or {}
        inserted = await self.session.execute(
            text(
                """INSERT INTO paper_ingestion_jobs (
                    tenant_id, user_id, paper_uuid, job_type, status, payload
                )
                SELECT DISTINCT c.tenant_id, c.user_id, c.paper_uuid,
                    'reembed', 'pending', '{}'::jsonb
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                  AND p.deleted_at IS NULL
                  AND c.content_version=p.current_content_version
                  AND c.embedding_status IN ('stale','failed')
                  AND NOT EXISTS (
                      SELECT 1 FROM paper_ingestion_jobs j
                      WHERE j.tenant_id=c.tenant_id AND j.user_id=c.user_id
                        AND j.paper_uuid=c.paper_uuid AND j.job_type='reembed'
                        AND j.status IN ('pending','running','retry')
                  )
                ON CONFLICT DO NOTHING
                RETURNING job_uuid"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        created = len(inserted.mappings().all())
        return {"created": created, "existing": int(existing_row.get("existing") or 0)}

    async def list_worker_scopes(self) -> list[tuple[str, str]]:
        result = await self.session.execute(
            text(
                "SELECT tenant_id, user_id FROM scholar_users "
                "WHERE status='active' ORDER BY tenant_id, user_id"
            )
        )
        return [
            (str(row["tenant_id"]), str(row["user_id"]))
            for row in result.mappings().all()
        ]

    async def claim_reembedding_job(
        self, worker_id: str, tenant_id: str, user_id: str
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                """WITH candidate AS (
                    SELECT job_uuid
                    FROM paper_ingestion_jobs
                    WHERE tenant_id=:tenant_id AND user_id=:user_id
                      AND job_type='reembed'
                      AND status IN ('pending','retry')
                      AND available_at <= now()
                    ORDER BY created_at
                    FOR UPDATE SKIP LOCKED
                    LIMIT 1
                )
                UPDATE paper_ingestion_jobs j
                SET status='running', locked_at=now(), locked_by=:worker_id,
                    attempt_count=j.attempt_count + 1, updated_at=now()
                FROM candidate
                WHERE j.job_uuid=candidate.job_uuid
                RETURNING j.job_uuid, j.tenant_id, j.user_id, j.paper_uuid,
                    j.attempt_count, j.max_attempts"""
            ),
            {"worker_id": worker_id, "tenant_id": tenant_id, "user_id": user_id},
        )
        row = result.mappings().first()
        return dict(row) if row else None

    async def current_embedding_batch(
        self, tenant_id: str, user_id: str, paper_uuid: UUID | str
    ) -> dict[str, Any] | None:
        result = await self.session.execute(
            text(
                """SELECT c.content_uuid, c.chunk_index, c.content
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                  AND c.paper_uuid=:paper_uuid AND p.deleted_at IS NULL
                  AND c.content_version=p.current_content_version
                ORDER BY c.chunk_index"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "paper_uuid": paper_uuid},
        )
        rows = result.mappings().all()
        if not rows:
            return None
        return {
            "content_uuid": rows[0]["content_uuid"],
            "chunks": [
                {"chunk_index": int(row["chunk_index"]), "content": str(row["content"])}
                for row in rows
            ],
        }

    async def complete_ingestion_job(
        self, tenant_id: str, user_id: str, job_uuid: UUID | str
    ) -> None:
        await self.session.execute(
            text(
                """UPDATE paper_ingestion_jobs
                SET status='completed', completed_at=now(), locked_at=NULL,
                    locked_by=NULL, last_error=NULL, updated_at=now()
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND job_uuid=:job_uuid"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "job_uuid": job_uuid},
        )

    async def fail_ingestion_job(
        self,
        tenant_id: str,
        user_id: str,
        job: Mapping[str, Any],
        error: str,
    ) -> None:
        retry = int(job.get("attempt_count") or 0) < int(job.get("max_attempts") or 1)
        await self.session.execute(
            text(
                """UPDATE paper_ingestion_jobs
                SET status=:status, last_error=:error, locked_at=NULL, locked_by=NULL,
                    available_at=CASE WHEN :retry THEN now() + interval '1 minute' ELSE available_at END,
                    completed_at=CASE WHEN :retry THEN NULL ELSE now() END,
                    updated_at=now()
                WHERE tenant_id=:tenant_id AND user_id=:user_id AND job_uuid=:job_uuid"""
            ),
            {
                "status": "retry" if retry else "failed",
                "retry": retry,
                "error": error[:4000],
                "tenant_id": tenant_id,
                "user_id": user_id,
                "job_uuid": job["job_uuid"],
            },
        )

    async def stats(self, tenant_id: str, user_id: str) -> dict[str, int]:
        result = await self.session.execute(
            text(
                """SELECT
                    COUNT(DISTINCT p.paper_uuid) FILTER (WHERE p.deleted_at IS NULL) AS paper_count,
                    COUNT(c.chunk_uuid) FILTER (
                        WHERE p.deleted_at IS NULL AND c.content_version=p.current_content_version
                    ) AS chunk_count,
                    COUNT(*) FILTER (WHERE p.ingestion_status='failed' AND p.deleted_at IS NULL) AS failed_papers
                FROM papers p LEFT JOIN paper_chunks c ON c.paper_uuid=p.paper_uuid
                    AND c.tenant_id=p.tenant_id AND c.user_id=p.user_id
                WHERE p.tenant_id=:tenant_id AND p.user_id=:user_id"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id},
        )
        row = result.mappings().first() or {}
        return {key: int(row.get(key) or 0) for key in ("paper_count", "chunk_count", "failed_papers")}

    @staticmethod
    def _record(row: Mapping[str, Any]) -> PaperRecord:
        authors = row.get("authors") or []
        metadata = row.get("metadata") or {}
        if isinstance(authors, str):
            authors = json.loads(authors)
        if isinstance(metadata, str):
            metadata = json.loads(metadata)
        return PaperRecord(
            paper_uuid=row["paper_uuid"],
            tenant_id=row["tenant_id"],
            user_id=row["user_id"],
            paper_id=row["paper_id"],
            source=row["source"],
            title=row["title"],
            authors=tuple(authors),
            abstract=row.get("abstract") or "",
            published_at=row.get("published_at"),
            doi=row.get("normalized_doi"),
            arxiv_id=row.get("normalized_arxiv_id"),
            url=row.get("canonical_url"),
            in_knowledge_base=bool(row.get("in_knowledge_base")),
            ingestion_status=row.get("ingestion_status") or "metadata_only",
            current_content_version=int(row.get("current_content_version") or 0),
            metadata=metadata,
            created_at=row["created_at"],
            updated_at=row["updated_at"],
        )
