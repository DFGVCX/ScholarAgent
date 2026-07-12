from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiohttp

from app.config import get_settings
from app.services import mysql_store


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-一-鿿]+")
_SENTENCE_END = re.compile(r"[。！？.!?]")


def _tokens(text: str) -> list[str]:
    value = text or ""
    tokens = [
        item.lower()
        for item in re.findall(r"[a-z0-9][a-z0-9._/+\-]*", value, re.IGNORECASE)
    ]
    for block in re.findall(r"[\u4e00-\u9fff]+", value):
        if len(block) == 1:
            tokens.append(block)
        else:
            tokens.extend(block[index:index + 2] for index in range(len(block) - 1))
    return tokens


def _bm25_scores(
    query_tokens: list[str], documents: list[list[str]], *, k1: float = 1.5, b: float = 0.75
) -> list[float]:
    """Compute Okapi BM25 scores over one tenant-scoped corpus."""
    if not query_tokens or not documents:
        return [0.0] * len(documents)
    document_count = len(documents)
    average_length = sum(len(document) for document in documents) / document_count
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
            frequency_in_documents = document_frequency.get(token, 0)
            inverse_document_frequency = math.log(
                1 + (document_count - frequency_in_documents + 0.5)
                / (frequency_in_documents + 0.5)
            )
            score += inverse_document_frequency * (
                frequency * (k1 + 1) / (frequency + k1 * normalization)
            )
        scores.append(score)
    return scores


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _temporal_decay(value: Any, half_life_days: float, now: datetime | None = None) -> float:
    published_at = _parse_date(value)
    if published_at is None:
        return 0.0
    current = now or datetime.now(timezone.utc)
    age_days = max(0.0, (current - published_at).total_seconds() / 86400)
    return math.exp(-math.log(2) * age_days / max(half_life_days, 1.0))


def _preference_score(document_tokens: list[str], preference_tokens: set[str]) -> float:
    if not document_tokens or not preference_tokens:
        return 0.0
    return len(set(document_tokens) & preference_tokens) / max(1, len(preference_tokens))


def _chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    settings = get_settings()
    size = max(200, int(size or settings.rag_chunk_size))
    overlap = min(max(0, int(overlap if overlap is not None else settings.rag_chunk_overlap)), size - 1)
    text = text or ""
    if not text.strip():
        return []
    strategy = settings.rag_chunk_strategy
    if strategy == "fixed":
        return _chunk_fixed(text, size, overlap)
    return _chunk_by_paragraph(text, size, overlap)


def _chunk_fixed(text: str, size: int, overlap: int) -> list[str]:
    """Original fixed-size sliding window chunking."""
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        return []
    chunks: list[str] = []
    cursor = 0
    while cursor < len(text):
        chunk = text[cursor : cursor + size].strip()
        if chunk:
            chunks.append(chunk)
        cursor += max(size - overlap, 1)
    return chunks


def _chunk_by_paragraph(text: str, size: int, overlap: int) -> list[str]:
    """Paragraph-aware chunking: split by paragraphs, then sentences, then fixed."""
    # Step 1: Split by double-newline (paragraphs)
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return [text.strip()[:size]]

    chunks: list[str] = []
    for paragraph in paragraphs:
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if len(paragraph) <= size:
            _append_chunk(chunks, paragraph, size, overlap)
        else:
            # Step 2: Split by sentence-ending punctuation
            sentences = _SENTENCE_END.split(paragraph)
            current = ""
            for sentence in sentences:
                sentence = sentence.strip()
                if not sentence:
                    continue
                if len(current) + len(sentence) + 1 <= size:
                    current = (current + " " + sentence).strip() if current else sentence
                else:
                    if len(sentence) > size:
                        # Step 3: Fixed-size fallback for very long sentences
                        if current:
                            _append_chunk(chunks, current, size, overlap)
                            current = ""
                        for i in range(0, len(sentence), max(size - overlap, 1)):
                            sub = sentence[i : i + size].strip()
                            if sub:
                                _append_chunk(chunks, sub, size, overlap)
                    else:
                        if current:
                            _append_chunk(chunks, current, size, overlap)
                        current = sentence
            if current:
                _append_chunk(chunks, current, size, overlap)
    return [c for c in chunks if c]


def _append_chunk(chunks: list[str], text: str, size: int, overlap: int) -> None:
    """Append text, adding overlap from previous chunk end if available."""
    if not chunks:
        chunks.append(text)
        return
    if overlap > 0 and len(chunks[-1]) > overlap:
        prefix = chunks[-1][-overlap:]
        chunks.append(prefix + " " + text)
    else:
        chunks.append(text)


def _hash_embedding(content: str, dimensions: int = 16) -> list[float]:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return [round(digest[i % len(digest)] / 255, 6) for i in range(dimensions)]


def _lexical_embedding(content: str, dimensions: int = 256) -> list[float]:
    """Build a deterministic local vector without loading a model."""
    dimensions = max(32, int(dimensions or 256))
    vector = [0.0] * dimensions
    terms = _tokens(content)
    if not terms and content.strip():
        terms = [content.strip().lower()]
    for term in terms:
        digest = hashlib.blake2b(term.encode("utf-8"), digest_size=8).digest()
        index = int.from_bytes(digest[:4], "big") % dimensions
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        vector[index] += sign
    norm = math.sqrt(sum(value * value for value in vector))
    if norm:
        return [round(value / norm, 8) for value in vector]
    return vector


def _public_embedding(value: Any) -> list[float]:
    settings = get_settings()
    if settings.rag_embedding_provider == "mock-hash" and settings.allow_mock_data:
        return list(value or [])
    if settings.rag_retrieval_mode in {"vector", "hybrid"} and value:
        return list(value or [])[:8]
    return []


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    provider = settings.rag_embedding_provider
    if provider in {"", "lexical", "hybrid"}:
        dimensions = settings.rag_embedding_dimensions or 256
        return [_lexical_embedding(text, dimensions) for text in texts]
    dimensions = settings.rag_embedding_dimensions or 16
    if provider == "mock-hash" and settings.allow_mock_data:
        return [_hash_embedding(text, dimensions) for text in texts]
    if provider not in {"openai-compatible", "bge", "jina", "cohere"}:
        raise RuntimeError(f"RAG embedding provider {provider!r} is not supported")
    base_url = (settings.rag_embedding_base_url or settings.llm_base_url).rstrip("/")
    api_key = settings.rag_embedding_api_key or settings.llm_api_key
    model = settings.rag_embedding_model
    if not base_url or not model:
        raise RuntimeError("RAG embedding requires Base URL and model name")
    if not api_key:
        raise RuntimeError("RAG embedding API Key is required for remote embedding calls")
    payload = {"model": model, "input": texts}
    if settings.rag_embedding_dimensions:
        payload["dimensions"] = settings.rag_embedding_dimensions
    timeout = aiohttp.ClientTimeout(total=60)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(
            f"{base_url}/v1/embeddings",
            json=payload,
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        ) as response:
            data = await response.json(content_type=None)
            if response.status >= 400:
                raise RuntimeError(f"Embedding provider returned HTTP {response.status}: {data}")
    try:
        rows = sorted(data["data"], key=lambda item: item.get("index", 0))
        return [list(map(float, row.get("embedding") or [])) for row in rows]
    except (KeyError, TypeError, ValueError) as exc:
        raise RuntimeError(f"Embedding provider response did not include vectors: {data}") from exc


def _chunk_id(tenant_id: str, user_id: str, paper_id: str, chunk_index: int, content: str) -> str:
    raw = f"{tenant_id}:{user_id}:{paper_id}:{chunk_index}:{content}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def build_chunks(paper: dict[str, Any]) -> list[dict[str, Any]]:
    content = "\n".join(
        item
        for item in (
            paper.get("title", ""),
            paper.get("abstract", ""),
            paper.get("full_text", ""),
        )
        if item
    )
    chunks = _chunk_text(content)
    if not chunks and paper.get("title"):
        chunks = [paper["title"]]
    result: list[dict[str, Any]] = []
    for index, chunk in enumerate(chunks):
        token_values = _tokens(chunk)
        keywords = sorted(set(token_values), key=token_values.index)[:20]
        result.append(
            {
                "chunk_id": _chunk_id(paper["tenant_id"], paper["user_id"], paper["paper_id"], index, chunk),
                "tenant_id": paper["tenant_id"],
                "user_id": paper["user_id"],
                "paper_id": paper["paper_id"],
                "chunk_index": index,
                "content_hash": hashlib.sha256(chunk.encode("utf-8")).hexdigest(),
                "content": chunk,
                "token_count": len(token_values),
                "keywords": keywords,
                "published_at": paper.get("published_at") or "",
                "indexed_at": datetime.now(timezone.utc).isoformat(),
                "embedding": [],
            }
        )
    return result


class RagService:
    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.storage_dir / "rag_chunks.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def backend(self) -> str:
        return "chromadb"

    def _read_sync(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_sync(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    async def index_paper(self, paper: dict[str, Any]) -> list[dict[str, Any]]:
        chunks = build_chunks(paper)
        embeddings = await _embed_texts([chunk["content"] for chunk in chunks])
        for chunk, embedding in zip(chunks, embeddings, strict=False):
            chunk["embedding"] = embedding

        # ChromaDB primary storage
        from app.services.chroma_store import chroma_store

        chroma_store.index_paper(
            paper["tenant_id"], paper["user_id"], paper["paper_id"],
            chunks, embeddings)

        # JSON fallback
        async with self._lock:
            data = self._read_sync()
            data = {
                key: value
                for key, value in data.items()
                if not (
                    value.get("tenant_id") == paper["tenant_id"]
                    and value.get("user_id") == paper["user_id"]
                    and value.get("paper_id") == paper["paper_id"]
                )
            }
            for chunk in chunks:
                data[chunk["chunk_id"]] = chunk
            self._write_sync(data)
        return chunks

    async def delete_paper(self, tenant_id: str, user_id: str, paper_id: str) -> None:
        from app.services.chroma_store import chroma_store

        chroma_store.delete_paper(tenant_id, user_id, paper_id)
        async with self._lock:
            data = self._read_sync()
            data = {
                key: value
                for key, value in data.items()
                if not (
                    value.get("tenant_id") == tenant_id
                    and value.get("user_id") == user_id
                    and value.get("paper_id") == paper_id
                )
            }
            self._write_sync(data)

    async def search(self, tenant_id: str, user_id: str, query: str, limit: int = 10) -> dict[str, Any]:
        from app.services.chroma_store import chroma_store

        settings = get_settings()
        candidate_limit = min(
            max(limit * 4, 20), max(int(settings.rag_candidate_limit or 0), limit)
        )
        query_embedding: list[float] = []
        if query.strip():
            try:
                embeddings = await _embed_texts([query])
                query_embedding = embeddings[0] if embeddings else []
            except Exception:
                pass
        vector_result = chroma_store.search(
            tenant_id, user_id, query, query_embedding, candidate_limit
        )

        async with self._lock:
            corpus = [
                dict(chunk)
                for chunk in self._read_sync().values()
                if chunk.get("tenant_id") == tenant_id and chunk.get("user_id") == user_id
            ]
        query_tokens = _tokens(query)
        document_tokens = [_tokens(str(chunk.get("content") or "")) for chunk in corpus]
        bm25_scores = _bm25_scores(
            query_tokens,
            document_tokens,
            k1=settings.rag_bm25_k1,
            b=settings.rag_bm25_b,
        )
        max_bm25 = max(bm25_scores, default=0.0)

        preference_tokens: set[str] = set()
        memories: list[Any] = []
        try:
            from app.schemas import UserContext
            from app.services.memory_service import user_memory_service

            memories = user_memory_service.recall(UserContext(tenant_id, user_id), query, limit=8)
            for memory in memories:
                if memory.memory_type in {"preference", "profile", "constraint", "instruction"}:
                    preference_tokens.update(_tokens(memory.content))
        except Exception:
            # Retrieval must remain available when the optional durable-memory store is down.
            memories = []

        vector_ranks: dict[tuple[str, int], int] = {}
        vector_items: dict[tuple[str, int], dict[str, Any]] = {}
        for rank, item in enumerate(vector_result.get("items", [])[:candidate_limit], start=1):
            key = (str(item.get("paper_id") or ""), int(item.get("chunk_index") or 0))
            vector_ranks[key] = rank
            vector_items[key] = dict(item)

        fused: list[dict[str, Any]] = []
        for chunk, tokens, bm25 in zip(corpus, document_tokens, bm25_scores, strict=False):
            key = (str(chunk.get("paper_id") or ""), int(chunk.get("chunk_index") or 0))
            rank = vector_ranks.get(key)
            if bm25 <= 0 and rank is None:
                continue
            vector_score = 1.0 / rank if rank else 0.0
            lexical_score = bm25 / max_bm25 if max_bm25 else 0.0
            temporal_score = _temporal_decay(
                chunk.get("published_at") or chunk.get("indexed_at"),
                settings.rag_recency_half_life_days,
            )
            preference_score = _preference_score(tokens, preference_tokens)
            weighted_score = (
                settings.rag_vector_weight * vector_score
                + settings.rag_bm25_weight * lexical_score
                + settings.rag_recency_weight * temporal_score
                + settings.rag_preference_weight * preference_score
            )
            item = dict(vector_items.get(key) or chunk)
            item["score"] = round(weighted_score, 6)
            item["_sort_score"] = weighted_score
            item["score_breakdown"] = {
                "vector": round(vector_score, 6),
                "bm25": round(lexical_score, 6),
                "temporal": round(temporal_score, 6),
                "preference": round(preference_score, 6),
            }
            fused.append(item)
        items = sorted(
            fused,
            key=lambda item: (float(item.get("_sort_score") or 0.0), str(item.get("indexed_at") or "")),
            reverse=True,
        )[:limit]
        for item in items:
            item.pop("_sort_score", None)
        result = {
            "backend": "chromadb",
            "retrieval_mode": "hybrid_bm25_temporal_preference",
            "preference_memories_used": len(memories),
            "items": items,
        }
        # Supplement with SQLite paper metadata
        for item in result["items"]:
            if mysql_store.is_available():
                paper = mysql_store.fetch_one(
                    "SELECT title, source, abstract FROM scholar_knowledge_papers "
                    "WHERE paper_id=? AND tenant_id=? AND user_id=?",
                    (item["paper_id"], tenant_id, user_id))
                if paper:
                    item["title"] = paper.get("title") or ""
                    item["source"] = paper.get("source") or ""
                    item["abstract"] = paper.get("abstract") or ""
        return result

    async def stats(self, tenant_id: str, user_id: str) -> dict[str, Any]:
        settings = get_settings()
        from app.services.chroma_store import chroma_store

        try:
            chroma_result = chroma_store.stats(tenant_id, user_id)
            chunk_count = chroma_result.get("chunk_count", 0)
            paper_count = chroma_result.get("paper_count", 0)
        except Exception:
            chunk_count = 0
            paper_count = 0
        return {
            "backend": "chromadb",
            "chunk_count": chunk_count,
            "paper_count": paper_count,
            "index_backend": settings.rag_index_backend,
            "retrieval_mode": settings.rag_retrieval_mode,
            "embedding_provider": settings.rag_embedding_provider,
            "embedding_model": settings.rag_embedding_model,
            "chunk_size": settings.rag_chunk_size,
            "chunk_overlap": settings.rag_chunk_overlap,
            "top_k": settings.rag_top_k,
            "candidate_limit": settings.rag_candidate_limit,
            "bm25": {"k1": settings.rag_bm25_k1, "b": settings.rag_bm25_b},
            "recency_half_life_days": settings.rag_recency_half_life_days,
            "fusion_weights": {
                "vector": settings.rag_vector_weight,
                "bm25": settings.rag_bm25_weight,
                "temporal": settings.rag_recency_weight,
                "preference": settings.rag_preference_weight,
            },
        }


rag_service = RagService()
