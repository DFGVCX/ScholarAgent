from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class RetrievalRequest:
    tenant_id: str
    user_id: str
    query: str
    limit: int = 8
    candidate_limit: int = 80
    include_external: bool = False
    paper_ids: tuple[str, ...] = ()
    year_from: int | None = None
    year_to: int | None = None
    author: str = ""
    venue: str = ""
    section_ids: tuple[str, ...] = ()
    chunk_types: tuple[str, ...] = ()
    max_chunks_per_paper: int = 3

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id:
            raise ValueError("tenant_id and user_id are required")
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "limit", max(1, min(int(self.limit), 50)))
        object.__setattr__(self, "candidate_limit", max(self.limit, min(int(self.candidate_limit), 800)))
        object.__setattr__(self, "paper_ids", self._terms(self.paper_ids, limit=100))
        object.__setattr__(self, "section_ids", self._terms(self.section_ids, limit=50))
        chunk_types = tuple(value.lower() for value in self._terms(self.chunk_types, limit=6))
        allowed_types = {"prose", "equation", "table", "figure", "algorithm", "code"}
        invalid_types = [value for value in chunk_types if value not in allowed_types]
        if invalid_types:
            raise ValueError(f"unsupported chunk type: {invalid_types[0]}")
        object.__setattr__(self, "chunk_types", chunk_types)
        object.__setattr__(self, "author", self.author.strip())
        object.__setattr__(self, "venue", self.venue.strip())
        object.__setattr__(
            self,
            "max_chunks_per_paper",
            max(0, min(int(self.max_chunks_per_paper), 50)),
        )
        if self.year_from is not None:
            object.__setattr__(self, "year_from", int(self.year_from))
        if self.year_to is not None:
            object.__setattr__(self, "year_to", int(self.year_to))
        if (
            self.year_from is not None
            and self.year_to is not None
            and self.year_from > self.year_to
        ):
            raise ValueError("year_from cannot be greater than year_to")

    @staticmethod
    def _terms(values: tuple[str, ...], *, limit: int) -> tuple[str, ...]:
        normalized = (str(value).strip() for value in values)
        return tuple(dict.fromkeys(value for value in normalized if value))[:limit]

    def filters_dict(self) -> dict[str, Any]:
        return {
            "paper_ids": list(self.paper_ids),
            "year_from": self.year_from,
            "year_to": self.year_to,
            "author": self.author,
            "venue": self.venue,
            "section_ids": list(self.section_ids),
            "chunk_types": list(self.chunk_types),
        }


@dataclass(frozen=True)
class ContextWindowRequest:
    tenant_id: str
    user_id: str
    chunk_id: str
    before: int = 1
    after: int = 1
    token_budget: int = 2048

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id or not self.chunk_id:
            raise ValueError("tenant_id, user_id and chunk_id are required")
        object.__setattr__(self, "before", max(0, min(int(self.before), 8)))
        object.__setattr__(self, "after", max(0, min(int(self.after), 8)))
        object.__setattr__(self, "token_budget", max(1, min(int(self.token_budget), 32768)))


@dataclass(frozen=True)
class ParentContextRequest:
    tenant_id: str
    user_id: str
    chunk_id: str

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id or not self.chunk_id:
            raise ValueError("tenant_id, user_id and chunk_id are required")


@dataclass(frozen=True)
class ParentSectionContext:
    center_chunk_id: str
    section_id: str
    title: str
    kind: str
    section_path: str
    page_start: int
    page_end: int
    content: str
    character_count: int
    estimated_tokens: int
    paper_id: str = ""
    content_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ContextChunk:
    chunk_id: str
    chunk_index: int
    content: str
    token_count: int
    section_id: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_type: str = "prose"
    parent_section_id: str | None = None
    source_block_ids: tuple[str, ...] = ()
    chunk_metadata: dict[str, Any] | None = None
    truncated: bool = False
    paper_id: str = ""
    content_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["source_block_ids"] = list(self.source_block_ids)
        return value


@dataclass(frozen=True)
class ContextWindowResponse:
    center_chunk_id: str
    chunks: tuple[ContextChunk, ...]
    token_budget: int
    total_tokens: int
    budget_exceeded: bool
    truncated: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "center_chunk_id": self.center_chunk_id,
            "chunks": [chunk.to_dict() for chunk in self.chunks],
            "token_budget": self.token_budget,
            "total_tokens": self.total_tokens,
            "budget_exceeded": self.budget_exceeded,
            "truncated": self.truncated,
        }


@dataclass(frozen=True)
class RetrievalCandidate:
    chunk_id: str
    chunk_index: int
    paper_uuid: str
    paper_id: str
    title: str
    authors: tuple[str, ...]
    content: str
    source: str
    doi: str | None
    arxiv_id: str | None
    canonical_url: str | None
    published_at: datetime | str | None
    score: float
    section_id: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_type: str = "prose"
    parent_section_id: str | None = None
    source_block_ids: tuple[str, ...] = ()
    chunk_metadata: dict[str, Any] | None = None
    context_before: str = ""
    context_after: str = ""
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    content_version: int = 0


@dataclass(frozen=True)
class LocalHit:
    chunk_id: str
    chunk_index: int
    paper_id: str
    title: str
    authors: tuple[str, ...]
    snippet: str
    source: str
    doi: str | None
    arxiv_id: str | None
    url: str | None
    published_at: datetime | str | None
    score: float
    lexical_rank: int | None
    vector_rank: int | None
    rrf_score: float
    final_rank: int
    rerank_score: float | None = None
    can_cite: bool = True
    section_id: str | None = None
    section_path: str | None = None
    page_start: int | None = None
    page_end: int | None = None
    chunk_type: str = "prose"
    parent_section_id: str | None = None
    source_block_ids: tuple[str, ...] = ()
    chunk_metadata: dict[str, Any] | None = None
    context_before: str = ""
    context_after: str = ""
    previous_chunk_id: str | None = None
    next_chunk_id: str | None = None
    content_version: int = 0

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["authors"] = list(self.authors)
        value["source_block_ids"] = list(self.source_block_ids)
        metadata = self.chunk_metadata or {}
        value["provenance"] = metadata.get("provenance", metadata)
        value["citation"] = {
            "key": f"{self.paper_id}@v{self.content_version}#{self.chunk_id}",
            "paper_id": self.paper_id,
            "content_version": self.content_version,
            "chunk_id": self.chunk_id,
            "page_start": self.page_start,
            "page_end": self.page_end,
            "section_id": self.section_id,
            "section_path": self.section_path,
        }
        if isinstance(self.published_at, datetime):
            value["published_at"] = self.published_at.isoformat()
        return value


@dataclass(frozen=True)
class ExternalCandidate:
    source: str
    external_id: str
    title: str
    authors: tuple[str, ...] = ()
    abstract: str = ""
    doi: str | None = None
    arxiv_id: str | None = None
    url: str | None = None
    can_cite: bool = False

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["authors"] = list(self.authors)
        return value


@dataclass(frozen=True)
class RetrievalResponse:
    query: str
    mode: str
    local_hits: tuple[LocalHit, ...]
    external_candidates: tuple[ExternalCandidate, ...] = ()
    warnings: tuple[str, ...] = ()
    backend: str = "postgresql+pgvector"
    filters: dict[str, Any] | None = None
    query_expansions: tuple[str, ...] = ()
    ranking_policy: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "backend": self.backend,
            "retrieval_mode": self.mode,
            "local_hits": [hit.to_dict() for hit in self.local_hits],
            "external_candidates": [item.to_dict() for item in self.external_candidates],
            "warnings": list(self.warnings),
            "filters": dict(self.filters or {}),
            "query_expansions": list(self.query_expansions),
            "ranking_policy": dict(self.ranking_policy or {}),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value["items"] = value["local_hits"]
        value["count"] = len(self.local_hits)
        return value
