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
                    extraction_method
                ) VALUES (
                    :tenant_id, :user_id, :paper_uuid, :version, :full_text, :content_hash,
                    :extraction_method
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
            },
        )
        content_uuid = inserted.scalar_one()
        for chunk in chunks:
            await self.session.execute(
                text(
                    """INSERT INTO paper_chunks (
                        tenant_id, user_id, paper_uuid, content_uuid, content_version,
                        chunk_index, content, content_hash, token_count
                    ) VALUES (
                        :tenant_id, :user_id, :paper_uuid, :content_uuid, :version,
                        :position, :content, :content_hash, :token_count)"""
                ),
                {
                    "tenant_id": tenant_id,
                    "user_id": user_id,
                    "paper_uuid": paper_uuid,
                    "content_uuid": content_uuid,
                    "version": version,
                    "position": chunk.position,
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
        return ContentVersion(paper_uuid, content_uuid, version, len(chunks))

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
