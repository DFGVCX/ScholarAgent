from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.models import RetrievalCandidate, RetrievalRequest


class PostgresRetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def lexical_candidates(self, request: RetrievalRequest) -> list[RetrievalCandidate]:
        result = await self.session.execute(
            text(
                """SELECT c.chunk_uuid::text AS chunk_id, p.paper_uuid::text AS paper_uuid,
                    p.paper_id, p.title, p.authors, c.content, p.source,
                    p.normalized_doi AS doi, p.normalized_arxiv_id AS arxiv_id,
                    p.canonical_url, p.published_at,
                    (CASE WHEN p.title ILIKE :pattern THEN 2.0 ELSE 0.0 END
                     + CASE WHEN c.content ILIKE :pattern THEN 1.0 ELSE 0.0 END
                     + ts_rank_cd(c.search_vector, plainto_tsquery('simple', :query))) AS score
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.content_version=p.current_content_version
                    AND (:query='' OR c.search_vector @@ plainto_tsquery('simple', :query)
                         OR c.content ILIKE :pattern OR p.title ILIKE :pattern
                         OR p.paper_id ILIKE :pattern
                         OR p.abstract ILIKE :pattern)
                ORDER BY score DESC, p.updated_at DESC, c.chunk_index
                LIMIT :candidate_limit"""
            ),
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "query": request.query,
                "pattern": f"%{request.query}%",
                "candidate_limit": request.candidate_limit,
            },
        )
        return [self._candidate(row) for row in result.mappings().all()]

    async def vector_candidates(
        self,
        request: RetrievalRequest,
        embedding: Sequence[float],
        embedding_model: str,
    ) -> list[RetrievalCandidate]:
        await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        vector = "[" + ",".join(format(float(value), ".9g") for value in embedding) + "]"
        result = await self.session.execute(
            text(
                """SELECT c.chunk_uuid::text AS chunk_id, p.paper_uuid::text AS paper_uuid,
                    p.paper_id, p.title, p.authors, c.content, p.source,
                    p.normalized_doi AS doi, p.normalized_arxiv_id AS arxiv_id,
                    p.canonical_url, p.published_at,
                    1.0 - (c.embedding <=> CAST(:embedding AS vector)) AS score
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.content_version=p.current_content_version
                    AND c.embedding_status='ready' AND c.embedding IS NOT NULL
                    AND c.embedding_model=:embedding_model
                ORDER BY c.embedding <=> CAST(:embedding AS vector)
                LIMIT :candidate_limit"""
            ),
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "embedding": vector,
                "embedding_model": embedding_model,
                "candidate_limit": request.candidate_limit,
            },
        )
        return [self._candidate(row) for row in result.mappings().all()]

    @staticmethod
    def _candidate(row: Mapping[str, Any]) -> RetrievalCandidate:
        authors = row.get("authors") or []
        if isinstance(authors, str):
            authors = json.loads(authors)
        return RetrievalCandidate(
            chunk_id=str(row["chunk_id"]),
            paper_uuid=str(row["paper_uuid"]),
            paper_id=row["paper_id"],
            title=row["title"],
            authors=tuple(authors),
            content=row.get("content") or "",
            source=row.get("source") or "local",
            doi=row.get("doi"),
            arxiv_id=row.get("arxiv_id"),
            canonical_url=row.get("canonical_url"),
            published_at=row.get("published_at"),
            score=float(row.get("score") or 0.0),
        )
