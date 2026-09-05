from __future__ import annotations

import json
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.models import (
    ContextChunk,
    ContextWindowRequest,
    ParentContextRequest,
    ParentSectionContext,
    RetrievalCandidate,
    RetrievalRequest,
)
from app.retrieval.query_expansion import academic_query_aliases


def _structured_filters(request: RetrievalRequest) -> tuple[str, dict[str, Any]]:
    clauses: list[str] = []
    params: dict[str, Any] = {}
    if request.paper_ids:
        clauses.append("p.paper_id = ANY(CAST(:paper_ids AS text[]))")
        params["paper_ids"] = list(request.paper_ids)
    if request.year_from is not None:
        clauses.append("EXTRACT(YEAR FROM p.published_at) >= :year_from")
        params["year_from"] = request.year_from
    if request.year_to is not None:
        clauses.append("EXTRACT(YEAR FROM p.published_at) <= :year_to")
        params["year_to"] = request.year_to
    if request.author:
        clauses.append("p.authors::text ILIKE :author_pattern")
        params["author_pattern"] = f"%{request.author}%"
    if request.venue:
        clauses.append(
            "(p.metadata->>'venue' ILIKE :venue_pattern "
            "OR p.metadata->>'publication_venue' ILIKE :venue_pattern "
            "OR p.source ILIKE :venue_pattern)"
        )
        params["venue_pattern"] = f"%{request.venue}%"
    if request.section_ids:
        clauses.append(
            "(c.section_id = ANY(CAST(:section_ids AS text[])) "
            "OR c.section_path ILIKE ANY(CAST(:section_patterns AS text[])))"
        )
        params["section_ids"] = list(request.section_ids)
        params["section_patterns"] = [f"%{value}%" for value in request.section_ids]
    if request.chunk_types:
        clauses.append("c.chunk_type = ANY(CAST(:chunk_types AS text[]))")
        params["chunk_types"] = list(request.chunk_types)
    sql = "".join(f"\n                    AND {clause}" for clause in clauses)
    return sql, params


class PostgresRetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def parent_context(
        self, request: ParentContextRequest
    ) -> ParentSectionContext | None:
        result = await self.session.execute(
            text(
                """SELECT c.chunk_uuid::text AS center_chunk_id,
                    p.paper_id, c.content_version,
                    s.section_id, s.title, s.kind, c.section_path,
                    s.page_start, s.page_end, s.content, s.char_count
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                JOIN LATERAL (
                    SELECT candidate.section_id, candidate.title, candidate.kind,
                        candidate.page_start, candidate.page_end,
                        candidate.content, candidate.char_count
                    FROM paper_sections candidate
                    WHERE candidate.tenant_id=c.tenant_id
                        AND candidate.user_id=c.user_id
                        AND candidate.content_uuid=c.content_uuid
                        AND (candidate.section_id=c.parent_section_id
                            OR candidate.section_id=c.section_id)
                    ORDER BY CASE
                        WHEN c.parent_section_id IS NOT NULL
                            AND candidate.section_id=c.parent_section_id THEN 0
                        ELSE 1 END
                    LIMIT 1
                ) s ON true
                WHERE p.tenant_id=:tenant_id AND p.user_id=:user_id
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.content_version=p.current_content_version
                    AND c.chunk_uuid=CAST(:chunk_id AS uuid)"""
            ),
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "chunk_id": request.chunk_id,
            },
        )
        row = result.mappings().first()
        return self._parent_context(row) if row else None

    async def context_window(self, request: ContextWindowRequest) -> list[ContextChunk]:
        result = await self.session.execute(
            text(
                """WITH center AS (
                    SELECT c.content_uuid, c.chunk_index
                    FROM paper_chunks c
                    JOIN papers p ON p.paper_uuid=c.paper_uuid
                        AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                    WHERE p.tenant_id=:tenant_id AND p.user_id=:user_id
                        AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                        AND c.content_version=p.current_content_version
                        AND c.chunk_uuid=CAST(:chunk_id AS uuid)
                )
                SELECT c.chunk_uuid::text AS chunk_id, c.chunk_index,
                    p.paper_id, c.content_version,
                    c.content, c.token_count, c.section_id, c.section_path,
                    c.page_start, c.page_end, c.chunk_type, c.parent_section_id,
                    c.source_block_ids, c.chunk_metadata
                FROM center
                JOIN paper_chunks c ON c.content_uuid=center.content_uuid
                    AND c.tenant_id=:tenant_id AND c.user_id=:user_id
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.content_version=p.current_content_version
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.chunk_index BETWEEN center.chunk_index - :before
                        AND center.chunk_index + :after
                ORDER BY c.chunk_index"""
            ),
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "chunk_id": request.chunk_id,
                "before": request.before,
                "after": request.after,
            },
        )
        return [self._context_chunk(row) for row in result.mappings().all()]

    async def lexical_candidates(self, request: RetrievalRequest) -> list[RetrievalCandidate]:
        aliases = academic_query_aliases(request.query)
        filter_sql, filter_params = _structured_filters(request)
        alias_score_sql = "".join(
            f" + CASE WHEN p.title ILIKE :alias_pattern_{index} THEN 1.5 "
            f"WHEN c.content ILIKE :alias_pattern_{index} THEN 1.0 "
            f"WHEN p.abstract ILIKE :alias_pattern_{index} THEN 0.75 ELSE 0.0 END"
            for index in range(len(aliases))
        )
        alias_filter_sql = "".join(
            f" OR c.content ILIKE :alias_pattern_{index}"
            f" OR p.title ILIKE :alias_pattern_{index}"
            f" OR p.abstract ILIKE :alias_pattern_{index}"
            for index in range(len(aliases))
        )
        params = {
            "tenant_id": request.tenant_id,
            "user_id": request.user_id,
            "query": request.query,
            "pattern": f"%{request.query}%",
            "candidate_limit": request.candidate_limit,
            **{
                f"alias_pattern_{index}": f"%{alias}%"
                for index, alias in enumerate(aliases)
            },
            **filter_params,
        }
        result = await self.session.execute(
            text(
                f"""SELECT c.chunk_uuid::text AS chunk_id, p.paper_uuid::text AS paper_uuid,
                    c.content_version AS content_version,
                    c.chunk_index AS chunk_index, c.section_id, c.section_path,
                    c.page_start, c.page_end, c.chunk_type, c.parent_section_id,
                    c.source_block_ids, c.chunk_metadata, c.context_before, c.context_after,
                    (SELECT adjacent.chunk_uuid::text FROM paper_chunks adjacent
                        WHERE adjacent.tenant_id=c.tenant_id AND adjacent.user_id=c.user_id
                            AND adjacent.content_uuid=c.content_uuid
                            AND adjacent.chunk_index=c.chunk_index - 1
                        LIMIT 1) AS previous_chunk_id,
                    (SELECT adjacent.chunk_uuid::text FROM paper_chunks adjacent
                        WHERE adjacent.tenant_id=c.tenant_id AND adjacent.user_id=c.user_id
                            AND adjacent.content_uuid=c.content_uuid
                            AND adjacent.chunk_index=c.chunk_index + 1
                        LIMIT 1) AS next_chunk_id,
                    p.paper_id, p.title, p.authors, c.content, p.source,
                    p.normalized_doi AS doi, p.normalized_arxiv_id AS arxiv_id,
                    p.canonical_url, p.published_at,
                    (CASE WHEN p.title ILIKE :pattern THEN 2.0 ELSE 0.0 END
                     + CASE WHEN c.content ILIKE :pattern THEN 1.0 ELSE 0.0 END
                     + ts_rank_cd(c.search_vector, plainto_tsquery('simple', :query))
                     {alias_score_sql}) AS score
                FROM paper_chunks c
                JOIN papers p ON p.paper_uuid=c.paper_uuid
                    AND p.tenant_id=c.tenant_id AND p.user_id=c.user_id
                WHERE c.tenant_id=:tenant_id AND c.user_id=:user_id
                    AND p.deleted_at IS NULL AND p.in_knowledge_base=true
                    AND c.content_version=p.current_content_version
                    {filter_sql}
                    AND (:query='' OR c.search_vector @@ plainto_tsquery('simple', :query)
                         OR c.content ILIKE :pattern OR p.title ILIKE :pattern
                         OR p.paper_id ILIKE :pattern
                         OR p.abstract ILIKE :pattern
                         {alias_filter_sql})
                ORDER BY score DESC, p.updated_at DESC, c.chunk_index
                LIMIT :candidate_limit"""
            ),
            params,
        )
        return [self._candidate(row) for row in result.mappings().all()]

    async def vector_candidates(
        self,
        request: RetrievalRequest,
        embedding: Sequence[float],
        embedding_model: str,
    ) -> list[RetrievalCandidate]:
        filter_sql, filter_params = _structured_filters(request)
        await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        vector = "[" + ",".join(format(float(value), ".9g") for value in embedding) + "]"
        result = await self.session.execute(
            text(
                f"""SELECT c.chunk_uuid::text AS chunk_id, p.paper_uuid::text AS paper_uuid,
                    c.content_version AS content_version,
                    c.chunk_index AS chunk_index, c.section_id, c.section_path,
                    c.page_start, c.page_end, c.chunk_type, c.parent_section_id,
                    c.source_block_ids, c.chunk_metadata, c.context_before, c.context_after,
                    (SELECT adjacent.chunk_uuid::text FROM paper_chunks adjacent
                        WHERE adjacent.tenant_id=c.tenant_id AND adjacent.user_id=c.user_id
                            AND adjacent.content_uuid=c.content_uuid
                            AND adjacent.chunk_index=c.chunk_index - 1
                        LIMIT 1) AS previous_chunk_id,
                    (SELECT adjacent.chunk_uuid::text FROM paper_chunks adjacent
                        WHERE adjacent.tenant_id=c.tenant_id AND adjacent.user_id=c.user_id
                            AND adjacent.content_uuid=c.content_uuid
                            AND adjacent.chunk_index=c.chunk_index + 1
                        LIMIT 1) AS next_chunk_id,
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
                    {filter_sql}
                ORDER BY c.embedding <=> CAST(:embedding AS vector)
                LIMIT :candidate_limit"""
            ),
            {
                "tenant_id": request.tenant_id,
                "user_id": request.user_id,
                "embedding": vector,
                "embedding_model": embedding_model,
                "candidate_limit": request.candidate_limit,
                **filter_params,
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
            chunk_index=int(row["chunk_index"]),
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
            section_id=row.get("section_id"),
            section_path=row.get("section_path"),
            page_start=int(row["page_start"]) if row.get("page_start") is not None else None,
            page_end=int(row["page_end"]) if row.get("page_end") is not None else None,
            chunk_type=str(row.get("chunk_type") or "prose"),
            parent_section_id=row.get("parent_section_id"),
            source_block_ids=tuple(row.get("source_block_ids") or ()),
            chunk_metadata=dict(row.get("chunk_metadata") or {}),
            context_before=str(row.get("context_before") or ""),
            context_after=str(row.get("context_after") or ""),
            previous_chunk_id=(
                str(row["previous_chunk_id"]) if row.get("previous_chunk_id") else None
            ),
            next_chunk_id=str(row["next_chunk_id"]) if row.get("next_chunk_id") else None,
            content_version=int(row.get("content_version") or 0),
        )

    @staticmethod
    def _context_chunk(row: Mapping[str, Any]) -> ContextChunk:
        return ContextChunk(
            chunk_id=str(row["chunk_id"]),
            chunk_index=int(row["chunk_index"]),
            content=str(row.get("content") or ""),
            token_count=max(1, int(row.get("token_count") or 0)),
            section_id=row.get("section_id"),
            section_path=row.get("section_path"),
            page_start=int(row["page_start"]) if row.get("page_start") is not None else None,
            page_end=int(row["page_end"]) if row.get("page_end") is not None else None,
            chunk_type=str(row.get("chunk_type") or "prose"),
            parent_section_id=row.get("parent_section_id"),
            source_block_ids=tuple(row.get("source_block_ids") or ()),
            chunk_metadata=dict(row.get("chunk_metadata") or {}),
            paper_id=str(row.get("paper_id") or ""),
            content_version=int(row.get("content_version") or 0),
        )

    @staticmethod
    def _parent_context(row: Mapping[str, Any]) -> ParentSectionContext:
        content = str(row.get("content") or "")
        character_count = int(row.get("char_count") or len(content))
        return ParentSectionContext(
            center_chunk_id=str(row["center_chunk_id"]),
            section_id=str(row.get("section_id") or ""),
            title=str(row.get("title") or ""),
            kind=str(row.get("kind") or ""),
            section_path=str(row.get("section_path") or row.get("title") or ""),
            page_start=int(row.get("page_start") or 1),
            page_end=int(row.get("page_end") or row.get("page_start") or 1),
            content=content,
            character_count=character_count,
            estimated_tokens=max(1, (character_count + 3) // 4),
            paper_id=str(row.get("paper_id") or ""),
            content_version=int(row.get("content_version") or 0),
        )
