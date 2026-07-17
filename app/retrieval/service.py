from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from app.retrieval.embedding import EmbeddingUnavailable, QwenEmbeddingClient
from app.retrieval.models import (
    ExternalCandidate,
    LocalHit,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResponse,
)


class RetrievalRepository(Protocol):
    async def lexical_candidates(self, request: RetrievalRequest) -> list[RetrievalCandidate]: ...

    async def vector_candidates(
        self, request: RetrievalRequest, embedding: Sequence[float], embedding_model: str
    ) -> list[RetrievalCandidate]: ...


def reciprocal_rank_fusion(
    ranked_ids: Sequence[Sequence[str]], *, k: int = 60
) -> list[tuple[str, float]]:
    scores: dict[str, float] = {}
    for ranking in ranked_ids:
        for rank, item_id in enumerate(ranking, start=1):
            scores[item_id] = scores.get(item_id, 0.0) + 1.0 / (k + rank)
    return sorted(scores.items(), key=lambda item: (-item[1], item[0]))


class RetrievalService:
    def __init__(
        self,
        repository: RetrievalRepository,
        embedding: QwenEmbeddingClient,
        external_search: Callable[[str, int], Awaitable[Sequence[ExternalCandidate | dict[str, Any]]]] | None = None,
    ) -> None:
        self.repository = repository
        self.embedding = embedding
        self.external_search = external_search

    async def search(self, request: RetrievalRequest) -> RetrievalResponse:
        lexical = await self.repository.lexical_candidates(request)
        vector: list[RetrievalCandidate] = []
        warnings: list[str] = []
        mode = "metadata" if not request.query else "lexical"
        if request.query:
            try:
                embeddings = await self.embedding.embed([request.query])
                vector = await self.repository.vector_candidates(
                    request, embeddings[0], self.embedding.model
                )
                mode = "hybrid"
            except EmbeddingUnavailable as exc:
                warnings.append(f"semantic retrieval unavailable: {exc}")

        hits = self._fuse(lexical, vector, request.limit)
        external: tuple[ExternalCandidate, ...] = ()
        if request.include_external and self.external_search and request.query:
            raw_external = await self.external_search(request.query, request.limit)
            external = tuple(self._external(item) for item in raw_external)
        return RetrievalResponse(
            query=request.query,
            mode=mode,
            local_hits=tuple(hits),
            external_candidates=external,
            warnings=tuple(warnings),
        )

    @staticmethod
    def _fuse(
        lexical: Sequence[RetrievalCandidate],
        vector: Sequence[RetrievalCandidate],
        limit: int,
    ) -> list[LocalHit]:
        rankings = [[item.chunk_id for item in lexical]]
        if vector:
            rankings.append([item.chunk_id for item in vector])
        fused = reciprocal_rank_fusion(rankings)
        candidates = {item.chunk_id: item for item in (*lexical, *vector)}
        lexical_rank = {item.chunk_id: rank for rank, item in enumerate(lexical, start=1)}
        vector_rank = {item.chunk_id: rank for rank, item in enumerate(vector, start=1)}
        hits: list[LocalHit] = []
        for chunk_id, score in fused:
            candidate = candidates[chunk_id]
            hits.append(
                LocalHit(
                    chunk_id=candidate.chunk_id,
                    chunk_index=candidate.chunk_index,
                    paper_id=candidate.paper_id,
                    title=candidate.title,
                    authors=candidate.authors,
                    snippet=candidate.content,
                    source=candidate.source,
                    doi=candidate.doi,
                    arxiv_id=candidate.arxiv_id,
                    url=candidate.canonical_url,
                    published_at=candidate.published_at,
                    score=score,
                    lexical_rank=lexical_rank.get(chunk_id),
                    vector_rank=vector_rank.get(chunk_id),
                    section_id=candidate.section_id,
                    section_path=candidate.section_path,
                    page_start=candidate.page_start,
                    page_end=candidate.page_end,
                )
            )
            if len(hits) >= limit:
                break
        return hits

    @staticmethod
    def _external(item: ExternalCandidate | dict[str, Any]) -> ExternalCandidate:
        if isinstance(item, ExternalCandidate):
            return item
        return ExternalCandidate(
            source=str(item.get("source") or "external"),
            external_id=str(item.get("external_id") or item.get("paper_id") or item.get("id") or ""),
            title=str(item.get("title") or ""),
            authors=tuple(item.get("authors") or ()),
            abstract=str(item.get("abstract") or ""),
            doi=item.get("doi"),
            arxiv_id=item.get("arxiv_id"),
            url=item.get("url"),
            can_cite=False,
        )
