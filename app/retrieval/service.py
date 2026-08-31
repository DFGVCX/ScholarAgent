from __future__ import annotations

from collections.abc import Awaitable, Callable, Sequence
from typing import Any, Protocol

from app.retrieval.embedding import EmbeddingUnavailable, QwenEmbeddingClient
from app.retrieval.models import (
    ContextChunk,
    ContextWindowRequest,
    ContextWindowResponse,
    ExternalCandidate,
    LocalHit,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResponse,
)


class RetrievalRepository(Protocol):
    async def context_window(self, request: ContextWindowRequest) -> list[ContextChunk]: ...

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

    async def expand_context(self, request: ContextWindowRequest) -> ContextWindowResponse:
        chunks = await self.repository.context_window(request)
        if not any(chunk.chunk_id == request.chunk_id for chunk in chunks):
            raise LookupError("chunk not found in the current knowledge-base version")
        return self._budget_context(request, chunks)

    @staticmethod
    def _budget_context(
        request: ContextWindowRequest, chunks: Sequence[ContextChunk]
    ) -> ContextWindowResponse:
        center = next(
            (chunk for chunk in chunks if chunk.chunk_id == request.chunk_id),
            None,
        )
        if center is None:
            raise LookupError("center chunk is missing from the context window")

        before = [chunk for chunk in chunks if chunk.chunk_index < center.chunk_index]
        after = [chunk for chunk in chunks if chunk.chunk_index > center.chunk_index]
        before = before[-request.before :] if request.before else []
        after = after[: request.after] if request.after else []
        selected = [center]
        total_tokens = max(1, center.token_count)
        before_nearest = list(reversed(before))
        before_blocked = False
        after_blocked = False
        for distance in range(max(len(before_nearest), len(after))):
            if distance < len(before_nearest) and not before_blocked:
                chunk = before_nearest[distance]
                chunk_tokens = max(1, chunk.token_count)
                if total_tokens + chunk_tokens <= request.token_budget:
                    selected.append(chunk)
                    total_tokens += chunk_tokens
                else:
                    before_blocked = True
            if distance < len(after) and not after_blocked:
                chunk = after[distance]
                chunk_tokens = max(1, chunk.token_count)
                if total_tokens + chunk_tokens <= request.token_budget:
                    selected.append(chunk)
                    total_tokens += chunk_tokens
                else:
                    after_blocked = True
        selected.sort(key=lambda chunk: chunk.chunk_index)
        requested_count = 1 + len(before) + len(after)
        return ContextWindowResponse(
            center_chunk_id=request.chunk_id,
            chunks=tuple(selected),
            token_budget=request.token_budget,
            total_tokens=total_tokens,
            budget_exceeded=total_tokens > request.token_budget,
            truncated=len(selected) < requested_count,
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
        seen_evidence: set[tuple[str, str]] = set()
        for chunk_id, score in fused:
            candidate = candidates[chunk_id]
            normalized = " ".join(candidate.content.casefold().split())
            evidence_key = (
                candidate.paper_uuid,
                normalized if normalized else f"<chunk:{candidate.chunk_id}>",
            )
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)
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
                    chunk_type=candidate.chunk_type,
                    parent_section_id=candidate.parent_section_id,
                    source_block_ids=candidate.source_block_ids,
                    chunk_metadata=candidate.chunk_metadata,
                    context_before=candidate.context_before,
                    context_after=candidate.context_after,
                    previous_chunk_id=candidate.previous_chunk_id,
                    next_chunk_id=candidate.next_chunk_id,
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
