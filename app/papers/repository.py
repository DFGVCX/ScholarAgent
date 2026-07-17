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

    async def mark_embeddings_stale(
        self, tenant_id: str, user_id: str, active_model: str
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
                  AND c.embedding_model IS DISTINCT FROM :active_model"""
            ),
            {"tenant_id": tenant_id, "user_id": user_id, "active_model": active_model},
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
