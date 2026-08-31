from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import replace
from difflib import SequenceMatcher
import math
import re
from typing import Any, Protocol

from app.retrieval.embedding import EmbeddingUnavailable, QwenEmbeddingClient
from app.retrieval.models import (
    ContextChunk,
    ContextWindowRequest,
    ContextWindowResponse,
    ExternalCandidate,
    LocalHit,
    MergedContext,
    ParentContextRequest,
    ParentSectionContext,
    RetrievalCandidate,
    RetrievalRequest,
    RetrievalResponse,
)
from app.retrieval.query_expansion import academic_query_aliases


class RetrievalRepository(Protocol):
    async def parent_context(
        self, request: ParentContextRequest
    ) -> ParentSectionContext | None: ...

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


def _normalized_evidence(text: str) -> str:
    return " ".join(text.casefold().split())


_DEFAULT_CANDIDATE_LIMIT = 80
_STRUCTURED_QUERY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("formula", re.compile(r"\b(?:eq(?:uation)?|formula)\b|公式|方程|等式", re.IGNORECASE)),
    ("table", re.compile(r"\btable\b|表格|表\s*[A-Z]?\d+", re.IGNORECASE)),
    ("figure", re.compile(r"\bfig(?:ure)?\b|架构图|示意图|图\s*[A-Z]?\d+", re.IGNORECASE)),
    ("algorithm", re.compile(r"\b(?:algorithm|pseudocode)\b|算法|伪代码", re.IGNORECASE)),
    ("code", re.compile(r"\b(?:code|implementation|repository)\b|代码|实现仓库", re.IGNORECASE)),
)


def _query_type(request: RetrievalRequest) -> str:
    selected_types = [
        value for value in request.chunk_types if value in {"equation", "table", "figure", "algorithm", "code"}
    ]
    if len(selected_types) == 1:
        return "formula" if selected_types[0] == "equation" else selected_types[0]
    if len(selected_types) > 1:
        return "structured_object"
    for query_type, pattern in _STRUCTURED_QUERY_PATTERNS:
        if pattern.search(request.query):
            return query_type
    compact = re.sub(r"[^A-Za-z0-9-]", "", request.query)
    if 2 <= len(compact) <= 10 and compact.upper() == compact and re.search(r"[A-Z]", compact):
        return "abbreviation"
    return "concept"


def _adapt_candidate_pool(request: RetrievalRequest) -> tuple[RetrievalRequest, str]:
    query_type = _query_type(request)
    if request.candidate_limit != _DEFAULT_CANDIDATE_LIMIT:
        return request, query_type
    target = {
        "formula": 160,
        "table": 160,
        "figure": 160,
        "algorithm": 160,
        "code": 120,
        "structured_object": 180,
        "abbreviation": 120,
        "concept": _DEFAULT_CANDIDATE_LIMIT,
    }[query_type]
    return replace(request, candidate_limit=max(request.limit, target)), query_type


def _character_ngrams(text: str, width: int = 5) -> set[str]:
    compact = _normalized_evidence(text)
    if len(compact) <= width:
        return {compact} if compact else set()
    return {compact[index : index + width] for index in range(len(compact) - width + 1)}


def _is_near_duplicate(
    candidate: RetrievalCandidate, accepted: RetrievalCandidate
) -> bool:
    if candidate.paper_uuid != accepted.paper_uuid:
        return False
    if candidate.chunk_type != "prose" or accepted.chunk_type != "prose":
        return False
    shared_source = bool(
        set(candidate.source_block_ids).intersection(accepted.source_block_ids)
    )
    adjacent_in_section = bool(
        candidate.section_id
        and candidate.section_id == accepted.section_id
        and abs(candidate.chunk_index - accepted.chunk_index) == 1
    )
    if not shared_source and not adjacent_in_section:
        return False
    left = _normalized_evidence(candidate.content)
    right = _normalized_evidence(accepted.content)
    if min(len(left), len(right)) < 120:
        return False
    shorter, longer = sorted((left, right), key=len)
    if shorter in longer and len(shorter) / len(longer) >= 0.85:
        return True
    if SequenceMatcher(None, left, right, autojunk=False).ratio() >= 0.92:
        return True
    left_grams = _character_ngrams(left)
    right_grams = _character_ngrams(right)
    union = left_grams | right_grams
    return bool(union) and len(left_grams & right_grams) / len(union) >= 0.90


class RetrievalService:
    def __init__(
        self,
        repository: RetrievalRepository,
        embedding: QwenEmbeddingClient,
        external_search: Callable[[str, int], Awaitable[Sequence[ExternalCandidate | dict[str, Any]]]] | None = None,
        *,
        semantic_timeout_seconds: float = 8.0,
    ) -> None:
        self.repository = repository
        self.embedding = embedding
        self.external_search = external_search
        self.semantic_timeout_seconds = max(0.001, float(semantic_timeout_seconds))

    async def search(self, request: RetrievalRequest) -> RetrievalResponse:
        request, query_type = _adapt_candidate_pool(request)
        requested_mode = request.retrieval_mode
        use_lexical = requested_mode in {"lexical", "hybrid"}
        use_vector = bool(request.query) and requested_mode in {"vector", "hybrid"}
        lexical = (
            await self.repository.lexical_candidates(request) if use_lexical else []
        )
        vector: list[RetrievalCandidate] = []
        embedding_debug: dict[str, Any] = {"status": "not_requested"}
        warnings: list[str] = []
        mode = "metadata" if not request.query else requested_mode
        if use_vector:
            try:
                vector, embedding_debug = await asyncio.wait_for(
                    self._semantic_candidates(request),
                    timeout=self.semantic_timeout_seconds,
                )
            except TimeoutError:
                embedding_debug = {
                    "status": "timeout",
                    "model": self.embedding.model,
                    "timeout_seconds": self.semantic_timeout_seconds,
                }
                suffix = (
                    "lexical results were preserved"
                    if requested_mode == "hybrid"
                    else "no lexical fallback was requested"
                )
                warnings.append(
                    "semantic retrieval timed out after "
                    f"{self.semantic_timeout_seconds:g}s; {suffix}"
                )
                if requested_mode == "hybrid":
                    mode = "lexical"
            except EmbeddingUnavailable as exc:
                embedding_debug = {
                    "status": "unavailable",
                    "model": self.embedding.model,
                    "reason": str(exc),
                }
                warnings.append(f"semantic retrieval unavailable: {exc}")
                if requested_mode == "hybrid":
                    mode = "lexical"

        hits = self._fuse(
            lexical,
            vector,
            request.limit,
            max_chunks_per_paper=request.max_chunks_per_paper,
        )
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
            filters=request.filters_dict(),
            query_expansions=academic_query_aliases(request.query),
            ranking_policy={
                "fusion": "rrf" if requested_mode == "hybrid" else "single_source",
                "requested_mode": requested_mode,
                "max_chunks_per_paper": request.max_chunks_per_paper,
                "backfill_when_insufficient": True,
                "exact_duplicate_scope": "same_paper",
                "near_duplicate_prose_threshold": 0.92,
                "near_duplicate_requires": "shared_source_or_adjacent_section",
                "adjacent_context_scope": "same_paper_version_section_top_k",
                "query_type": query_type,
                "candidate_limit": request.candidate_limit,
            },
            merged_contexts=tuple(self._merge_adjacent_hits(hits)),
            debug={
                "query_embedding": embedding_debug,
                "candidate_pools": {
                    "lexical": self._candidate_pool_debug(lexical),
                    "vector": self._candidate_pool_debug(vector),
                },
                "ranking": [
                    {
                        "chunk_id": hit.chunk_id,
                        "lexical_rank": hit.lexical_rank,
                        "vector_rank": hit.vector_rank,
                        "rrf_score": hit.rrf_score,
                        "rerank_score": hit.rerank_score,
                        "final_rank": hit.final_rank,
                    }
                    for hit in hits
                ],
            },
        )

    async def _semantic_candidates(
        self, request: RetrievalRequest
    ) -> tuple[list[RetrievalCandidate], dict[str, Any]]:
        embeddings = await self.embedding.embed([request.query])
        vector = embeddings[0]
        candidates = await self.repository.vector_candidates(
            request, vector, self.embedding.model
        )
        return candidates, {
            "status": "ready",
            "model": self.embedding.model,
            "dimensions": len(vector),
            "l2_norm": round(math.sqrt(sum(float(value) ** 2 for value in vector)), 6),
            "head": [round(float(value), 6) for value in vector[:8]],
        }

    @staticmethod
    def _candidate_pool_debug(
        candidates: Sequence[RetrievalCandidate],
    ) -> dict[str, Any]:
        return {
            "count": len(candidates),
            "top": [
                {
                    "chunk_id": candidate.chunk_id,
                    "rank": rank,
                    "source_score": candidate.score,
                }
                for rank, candidate in enumerate(candidates[:20], start=1)
            ],
        }

    async def expand_context(self, request: ContextWindowRequest) -> ContextWindowResponse:
        chunks = await self.repository.context_window(request)
        if not any(chunk.chunk_id == request.chunk_id for chunk in chunks):
            raise LookupError("chunk not found in the current knowledge-base version")
        return self._budget_context(request, chunks)

    async def parent_context(self, request: ParentContextRequest) -> ParentSectionContext:
        parent = await self.repository.parent_context(request)
        if parent is None:
            raise LookupError("parent section not found in the current knowledge-base version")
        return parent

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
        *,
        max_chunks_per_paper: int = 3,
    ) -> list[LocalHit]:
        rankings = [[item.chunk_id for item in lexical]]
        if vector:
            rankings.append([item.chunk_id for item in vector])
        fused = reciprocal_rank_fusion(rankings)
        candidates = {item.chunk_id: item for item in (*lexical, *vector)}
        lexical_rank = {item.chunk_id: rank for rank, item in enumerate(lexical, start=1)}
        vector_rank = {item.chunk_id: rank for rank, item in enumerate(vector, start=1)}
        ranked_evidence: list[tuple[str, float]] = []
        accepted_by_source: dict[tuple[str, str], list[RetrievalCandidate]] = {}
        accepted_by_position: dict[
            tuple[str, str, int], list[RetrievalCandidate]
        ] = {}
        seen_evidence: set[tuple[str, str]] = set()
        for chunk_id, score in fused:
            candidate = candidates[chunk_id]
            normalized = _normalized_evidence(candidate.content)
            evidence_key = (
                candidate.paper_uuid,
                normalized if normalized else f"<chunk:{candidate.chunk_id}>",
            )
            if evidence_key in seen_evidence:
                continue
            seen_evidence.add(evidence_key)
            comparable: dict[str, RetrievalCandidate] = {}
            for source_id in candidate.source_block_ids:
                for accepted in accepted_by_source.get(
                    (candidate.paper_uuid, source_id), ()
                ):
                    comparable[accepted.chunk_id] = accepted
            if candidate.section_id:
                for adjacent_index in (
                    candidate.chunk_index - 1,
                    candidate.chunk_index + 1,
                ):
                    for accepted in accepted_by_position.get(
                        (candidate.paper_uuid, candidate.section_id, adjacent_index),
                        (),
                    ):
                        comparable[accepted.chunk_id] = accepted
            if any(
                _is_near_duplicate(candidate, accepted)
                for accepted in comparable.values()
            ):
                continue
            for source_id in candidate.source_block_ids:
                accepted_by_source.setdefault(
                    (candidate.paper_uuid, source_id), []
                ).append(candidate)
            if candidate.section_id:
                accepted_by_position.setdefault(
                    (
                        candidate.paper_uuid,
                        candidate.section_id,
                        candidate.chunk_index,
                    ),
                    [],
                ).append(candidate)
            ranked_evidence.append((chunk_id, score))

        selected: list[tuple[str, float]] = []
        deferred: list[tuple[str, float]] = []
        paper_counts: dict[str, int] = {}
        cap = max(0, int(max_chunks_per_paper))
        for item in ranked_evidence:
            candidate = candidates[item[0]]
            count = paper_counts.get(candidate.paper_uuid, 0)
            if cap and count >= cap:
                deferred.append(item)
                continue
            selected.append(item)
            paper_counts[candidate.paper_uuid] = count + 1
            if len(selected) >= limit:
                break
        if len(selected) < limit:
            selected.extend(deferred[: limit - len(selected)])

        hits: list[LocalHit] = []
        for final_rank, (chunk_id, score) in enumerate(selected, start=1):
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
                    rrf_score=score,
                    final_rank=final_rank,
                    rerank_score=None,
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
                    content_version=candidate.content_version,
                )
            )
        return hits

    @staticmethod
    def _merge_adjacent_hits(hits: Sequence[LocalHit]) -> list[MergedContext]:
        grouped: dict[tuple[str, int, str], list[LocalHit]] = {}
        for hit in hits:
            if not hit.section_id:
                continue
            grouped.setdefault(
                (hit.paper_id, hit.content_version, hit.section_id), []
            ).append(hit)

        merged: list[MergedContext] = []
        for (paper_id, content_version, section_id), candidates in grouped.items():
            ordered = sorted(candidates, key=lambda hit: (hit.chunk_index, hit.final_rank))
            run: list[LocalHit] = []
            for candidate in ordered:
                if run and candidate.chunk_index != run[-1].chunk_index + 1:
                    if len(run) > 1:
                        merged.append(RetrievalService._merged_context(run))
                    run = []
                run.append(candidate)
            if len(run) > 1:
                merged.append(RetrievalService._merged_context(run))
        return sorted(merged, key=lambda context: (context.best_rank, context.context_id))

    @staticmethod
    def _merged_context(hits: Sequence[LocalHit]) -> MergedContext:
        first, last = hits[0], hits[-1]
        page_starts = [hit.page_start for hit in hits if hit.page_start is not None]
        page_ends = [hit.page_end for hit in hits if hit.page_end is not None]
        return MergedContext(
            context_id=(
                f"{first.paper_id}@v{first.content_version}#"
                f"{first.chunk_id}..{last.chunk_id}"
            ),
            paper_id=first.paper_id,
            content_version=first.content_version,
            section_id=first.section_id or "",
            section_path=first.section_path or "",
            chunk_ids=tuple(hit.chunk_id for hit in hits),
            chunk_types=tuple(hit.chunk_type for hit in hits),
            content="\n\n".join(hit.snippet for hit in hits),
            page_start=min(page_starts) if page_starts else None,
            page_end=max(page_ends) if page_ends else None,
            best_rank=min(hit.final_rank for hit in hits),
            citation_keys=tuple(
                f"{hit.paper_id}@v{hit.content_version}#{hit.chunk_id}" for hit in hits
            ),
        )

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
