from __future__ import annotations

import json
import re
from typing import Any, Mapping, Sequence

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.retrieval.models import (
    ContextChunk,
    ContextWindowRequest,
    RetrievalCandidate,
    RetrievalRequest,
)


_CHINESE_ACADEMIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("联邦学习", ("federated learning",)),
    ("机器学习", ("machine learning",)),
    ("深度学习", ("deep learning",)),
    ("区块链", ("blockchain",)),
    ("全同态加密", ("fully homomorphic encryption",)),
    ("同态加密", ("homomorphic encryption",)),
    ("差分隐私", ("differential privacy",)),
    ("隐私保护", ("privacy-preserving", "privacy preserving")),
    ("投毒攻击", ("poisoning attack",)),
    ("拜占庭鲁棒", ("byzantine-robust", "byzantine robust")),
    ("恶意客户端", ("malicious client",)),
    ("聚合规则", ("aggregation rule",)),
    ("余弦相似度", ("cosine similarity",)),
    ("根数据集", ("root dataset",)),
    ("安全多方计算", ("secure multi-party computation",)),
    ("模型更新", ("model update",)),
)


def _lexical_aliases(query: str) -> tuple[str, ...]:
    compact = re.sub(r"\s+", "", query).lower()
    aliases: list[str] = []
    for chinese, english_terms in _CHINESE_ACADEMIC_ALIASES:
        if chinese in compact:
            aliases.extend(english_terms)
    return tuple(dict.fromkeys(aliases))


class PostgresRetrievalRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

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
        aliases = _lexical_aliases(request.query)
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
        }
        result = await self.session.execute(
            text(
                f"""SELECT c.chunk_uuid::text AS chunk_id, p.paper_uuid::text AS paper_uuid,
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
        await self.session.execute(text("SET LOCAL hnsw.iterative_scan = strict_order"))
        vector = "[" + ",".join(format(float(value), ".9g") for value in embedding) + "]"
        result = await self.session.execute(
            text(
                """SELECT c.chunk_uuid::text AS chunk_id, p.paper_uuid::text AS paper_uuid,
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
        )
