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

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.user_id:
            raise ValueError("tenant_id and user_id are required")
        object.__setattr__(self, "query", self.query.strip())
        object.__setattr__(self, "limit", max(1, min(int(self.limit), 50)))
        object.__setattr__(self, "candidate_limit", max(self.limit, min(int(self.candidate_limit), 800)))


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
    can_cite: bool = True

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["authors"] = list(self.authors)
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

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "backend": self.backend,
            "retrieval_mode": self.mode,
            "local_hits": [hit.to_dict() for hit in self.local_hits],
            "external_candidates": [item.to_dict() for item in self.external_candidates],
            "warnings": list(self.warnings),
        }

    def to_legacy_dict(self) -> dict[str, Any]:
        value = self.to_dict()
        value["items"] = value["local_hits"]
        value["count"] = len(self.local_hits)
        return value
