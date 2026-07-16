from __future__ import annotations

import hashlib
import math
import mimetypes
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.db.session import tenant_transaction
from app.papers.ingestion import paper_ingestion_service
from app.papers.chunking import chunk_text
from app.papers.models import PaperInput
from app.papers.repository import PaperRepository
from app.retrieval.embedding import QwenEmbeddingClient
from app.retrieval.models import RetrievalRequest
from app.retrieval.repository import PostgresRetrievalRepository
from app.retrieval.service import RetrievalService


def _lexical_embedding(content: str, dimensions: int = 256) -> list[float]:
    """Deprecated deterministic helper retained only for old offline tests; never persisted."""
    vector = [0.0] * dimensions
    for token in content.lower().split():
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        vector[int.from_bytes(digest[:4], "big") % dimensions] += 1.0
    norm = math.sqrt(sum(value * value for value in vector)) or 1.0
    return [value / norm for value in vector]


def _tokens(text: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9][a-z0-9._/+\-]*", text.lower())
    for block in re.findall(r"[\u4e00-\u9fff]+", text):
        tokens.extend(block if len(block) == 1 else (block[i : i + 2] for i in range(len(block) - 1)))
    return tokens


def _bm25_scores(
    query_tokens: list[str], documents: list[list[str]], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    if not query_tokens or not documents:
        return [0.0] * len(documents)
    average_length = sum(map(len, documents)) / len(documents)
    document_frequency = Counter(token for document in documents for token in set(document))
    scores: list[float] = []
    for document in documents:
        frequencies = Counter(document)
        normalization = 1 - b + b * len(document) / max(average_length, 1.0)
        score = 0.0
        for token in query_tokens:
            frequency = frequencies.get(token, 0)
            if not frequency:
                continue
            frequency_in_documents = document_frequency[token]
            inverse_document_frequency = math.log(
                1 + (len(documents) - frequency_in_documents + 0.5) / (frequency_in_documents + 0.5)
            )
            score += inverse_document_frequency * frequency * (k1 + 1) / (
                frequency + k1 * normalization
            )
        scores.append(score)
    return scores


def _temporal_decay(value: Any, half_life_days: float, now: datetime | None = None) -> float:
    try:
        published_at = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if published_at.tzinfo is None:
            published_at = published_at.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return 0.0
    age_days = max(0.0, ((now or datetime.now(timezone.utc)) - published_at).total_seconds() / 86400)
    return math.exp(-math.log(2) * age_days / max(half_life_days, 1.0))


def _preference_score(document_tokens: list[str], preference_tokens: set[str]) -> float:
    return len(set(document_tokens) & preference_tokens) / max(1, len(preference_tokens))


def _chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    settings = get_settings()
    max_chars = int(size or settings.rag_chunk_size)
    overlap_chars = int(overlap if overlap is not None else settings.rag_chunk_overlap)
    if settings.rag_chunk_strategy == "fixed":
        return [item.content for item in chunk_text(text, max_chars, overlap_chars)]
    chunks: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        chunks.extend(item.content for item in chunk_text(paragraph, max_chars, overlap_chars))
    return chunks


def _paper_input(paper: dict[str, Any]) -> PaperInput:
    metadata = dict(paper.get("metadata") or {})
    file_uri = str(paper.get("file_path") or metadata.get("file_path") or "") or None
    file_sha256 = None
    file_size = None
    mime_type = None
    file_name = None
    if file_uri:
        path = Path(file_uri)
        file_name = path.name
        if path.exists() and path.is_file():
            file_sha256 = hashlib.sha256(path.read_bytes()).hexdigest()
            file_size = path.stat().st_size
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    return PaperInput(
        paper_id=str(paper["paper_id"]),
        source=str(paper.get("source") or "manual"),
        title=str(paper.get("title") or paper["paper_id"]),
        authors=tuple(paper.get("authors") or ()),
        abstract=str(paper.get("abstract") or ""),
        full_text=str(paper.get("full_text") or ""),
        published_at=paper.get("published_at"),
        doi=paper.get("doi"),
        arxiv_id=paper.get("arxiv_id"),
        url=paper.get("url"),
        file_uri=file_uri,
        file_name=file_name,
        mime_type=mime_type,
        file_sha256=file_sha256,
        file_size=file_size,
        in_knowledge_base=bool(paper.get("in_knowledge_base", True)),
        metadata=metadata,
    )


class RagService:
    def backend(self) -> str:
        return "pgvector"

    async def index_paper(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        result = await paper_ingestion_service.ingest(
            str(paper["tenant_id"]), str(paper["user_id"]), _paper_input(paper)
        )
        return [
            {
                "paper_id": result.paper.paper_id,
                "chunk_count": result.chunk_count,
                "embedding_status": result.embedding_status,
                "warning": result.warning,
            }
        ]

    async def delete_paper(self, tenant_id: str, user_id: str, paper_id: str) -> None:
        async with tenant_transaction(tenant_id, user_id) as session:
            await PaperRepository(session).soft_delete(tenant_id, user_id, paper_id)

    async def search(
        self, tenant_id: str, user_id: str, query: str, limit: int = 10
    ) -> dict[str, Any]:
        settings = get_settings()
        async with tenant_transaction(tenant_id, user_id) as session:
            retrieval = RetrievalService(
                PostgresRetrievalRepository(session), QwenEmbeddingClient.from_settings()
            )
            response = await retrieval.search(
                RetrievalRequest(
                    tenant_id=tenant_id,
                    user_id=user_id,
                    query=query,
                    limit=limit,
                    candidate_limit=settings.rag_candidate_limit,
                )
            )
        return response.to_legacy_dict()

    async def stats(self, tenant_id: str, user_id: str) -> dict[str, Any]:
        settings = get_settings()
        async with tenant_transaction(tenant_id, user_id) as session:
            counts = await PaperRepository(session).stats(tenant_id, user_id)
        return {
            "backend": "pgvector",
            **counts,
            "index_backend": "pgvector",
            "retrieval_mode": "hybrid_rrf",
            "embedding_provider": "qwen",
            "embedding_model": QwenEmbeddingClient.MODEL,
            "embedding_dimensions": QwenEmbeddingClient.DIMENSIONS,
            "chunk_size": settings.rag_chunk_size,
            "chunk_overlap": settings.rag_chunk_overlap,
            "top_k": settings.rag_top_k,
            "candidate_limit": settings.rag_candidate_limit,
        }


rag_service = RagService()
