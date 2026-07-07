from __future__ import annotations

import asyncio
import hashlib
import json
import math
import re
from pathlib import Path
from typing import Any

import aiohttp

from app.config import get_settings
from app.services import mysql_store


TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_\-\u4e00-\u9fff]+")


def _tokens(text: str) -> list[str]:
    return [item.lower() for item in TOKEN_PATTERN.findall(text or "") if len(item.strip()) > 1]


def _chunk_text(text: str, size: int | None = None, overlap: int | None = None) -> list[str]:
    settings = get_settings()
    size = max(200, int(size or settings.rag_chunk_size))
    overlap = min(max(0, int(overlap if overlap is not None else settings.rag_chunk_overlap)), size - 1)
    text = re.sub(r"\s+", " ", text or "").strip()
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


def _hash_embedding(content: str, dimensions: int = 16) -> list[float]:
    digest = hashlib.sha256(content.encode("utf-8")).digest()
    return [round(digest[i % len(digest)] / 255, 6) for i in range(dimensions)]


def _public_embedding(value: Any) -> list[float]:
    settings = get_settings()
    if settings.rag_embedding_provider == "mock-hash" and settings.allow_mock_data:
        return list(value or [])
    if settings.rag_retrieval_mode in {"vector", "hybrid"} and value:
        return list(value or [])[:8]
    return []


def _cosine(left: list[float], right: list[float]) -> float:
    if not left or not right:
        return 0.0
    length = min(len(left), len(right))
    if length <= 0:
        return 0.0
    dot = sum(float(left[i]) * float(right[i]) for i in range(length))
    left_norm = math.sqrt(sum(float(left[i]) ** 2 for i in range(length)))
    right_norm = math.sqrt(sum(float(right[i]) ** 2 for i in range(length)))
    if not left_norm or not right_norm:
        return 0.0
    return dot / (left_norm * right_norm)


async def _embed_texts(texts: list[str]) -> list[list[float]]:
    settings = get_settings()
    provider = settings.rag_embedding_provider
    if provider in {"", "lexical", "hybrid"}:
        return [[] for _ in texts]
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
        settings = get_settings()
        if settings.rag_index_backend == "json":
            return "json"
        if settings.rag_index_backend == "mysql" and not mysql_store.is_available():
            return "json"
        return "mysql" if mysql_store.is_available() else "json"

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
        if self.backend() == "mysql":
            mysql_store.execute(
                """
                DELETE FROM scholar_rag_chunks
                WHERE tenant_id = %s AND user_id = %s AND paper_id = %s
                """,
                (paper["tenant_id"], paper["user_id"], paper["paper_id"]),
            )
            for chunk in chunks:
                mysql_store.execute(
                    """
                    INSERT INTO scholar_rag_chunks
                        (chunk_id, tenant_id, user_id, paper_id, chunk_index, content_hash,
                         content, token_count, keywords_json, embedding_json)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON DUPLICATE KEY UPDATE
                        content_hash = VALUES(content_hash),
                        content = VALUES(content),
                        token_count = VALUES(token_count),
                        keywords_json = VALUES(keywords_json),
                        embedding_json = VALUES(embedding_json)
                    """,
                    (
                        chunk["chunk_id"],
                        chunk["tenant_id"],
                        chunk["user_id"],
                        chunk["paper_id"],
                        chunk["chunk_index"],
                        chunk["content_hash"],
                        chunk["content"],
                        chunk["token_count"],
                        mysql_store.encode_json(chunk["keywords"]),
                        mysql_store.encode_json(chunk["embedding"]),
                    ),
                )
            return chunks
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
        if self.backend() == "mysql":
            return
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
        settings = get_settings()
        limit = max(1, min(int(limit or settings.rag_top_k), 100))
        query_l = query.lower().strip()
        query_tokens = set(_tokens(query_l))
        query_embedding = [[]]
        if query_l and settings.rag_retrieval_mode in {"vector", "hybrid"}:
            query_embedding = await _embed_texts([query])
        if self.backend() == "mysql":
            like = f"%{query_l}%"
            rows = mysql_store.fetch_all(
                """
                SELECT
                    c.chunk_id, c.paper_id, c.chunk_index, c.content, c.token_count,
                    c.keywords_json, c.embedding_json,
                    p.title, p.source, p.abstract
                FROM scholar_rag_chunks c
                JOIN scholar_knowledge_papers p
                  ON p.tenant_id = c.tenant_id
                 AND p.user_id = c.user_id
                 AND p.paper_id = c.paper_id
                WHERE c.tenant_id = %s
                  AND c.user_id = %s
                  AND (
                    %s IN ('vector', 'hybrid')
                    OR
                    %s = ''
                    OR LOWER(c.content) LIKE %s
                    OR LOWER(p.title) LIKE %s
                    OR LOWER(COALESCE(p.abstract, '')) LIKE %s
                  )
                ORDER BY c.created_at DESC
                LIMIT %s
                """,
                (
                    tenant_id,
                    user_id,
                    settings.rag_retrieval_mode,
                    query_l,
                    like,
                    like,
                    like,
                    max(settings.rag_candidate_limit, limit),
                ),
            )
            items = [self._score_result(self._row_to_result(row), query_l, query_tokens, query_embedding[0]) for row in rows]
            items.sort(key=lambda item: item.get("score", 0), reverse=True)
            return {
                "backend": "mysql",
                "retrieval_mode": settings.rag_retrieval_mode,
                "items": items[:limit],
            }
        async with self._lock:
            rows = list(self._read_sync().values())
        scored: list[dict[str, Any]] = []
        for row in rows:
            if row.get("tenant_id") != tenant_id or row.get("user_id") != user_id:
                continue
            content = row.get("content", "")
            keywords = set(row.get("keywords", []))
            lexical_score = len(query_tokens & keywords) + (3 if query_l and query_l in content.lower() else 0)
            vector_score = _cosine(query_embedding[0], list(row.get("embedding") or []))
            score = self._combined_score(lexical_score, vector_score)
            if settings.rag_retrieval_mode in {"vector", "hybrid"} or not query_l or query_l in content.lower() or lexical_score:
                item = dict(row)
                item["score"] = round(score, 6)
                item["lexical_score"] = lexical_score
                item["vector_score"] = round(vector_score, 6)
                item["embedding"] = _public_embedding(item.get("embedding"))
                scored.append(item)
        scored.sort(key=lambda item: item.get("score", 0), reverse=True)
        return {"backend": "json", "retrieval_mode": settings.rag_retrieval_mode, "items": scored[:limit]}

    def _combined_score(self, lexical_score: float, vector_score: float) -> float:
        mode = get_settings().rag_retrieval_mode
        if mode == "vector":
            return vector_score
        if mode == "hybrid":
            return float(lexical_score) + vector_score * 4
        return float(lexical_score)

    def _score_result(
        self,
        item: dict[str, Any],
        query_l: str,
        query_tokens: set[str],
        query_embedding: list[float],
    ) -> dict[str, Any]:
        haystack = " ".join(
            str(item.get(key) or "")
            for key in ("title", "content", "abstract")
        ).lower()
        keywords = set(item.get("keywords") or [])
        lexical_score = len(query_tokens & keywords) + (3 if query_l and query_l in haystack else 0)
        vector_score = _cosine(query_embedding, list(item.get("raw_embedding") or []))
        item["score"] = round(self._combined_score(lexical_score, vector_score), 6)
        item["lexical_score"] = lexical_score
        item["vector_score"] = round(vector_score, 6)
        item.pop("raw_embedding", None)
        return item

    def _row_to_result(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "chunk_id": row["chunk_id"],
            "paper_id": row["paper_id"],
            "chunk_index": row["chunk_index"],
            "title": row["title"],
            "source": row["source"],
            "content": row["content"],
            "token_count": int(row.get("token_count") or 0),
            "score": 0,
            "keywords": mysql_store.decode_json(row.get("keywords_json"), []),
            "abstract": row.get("abstract") or "",
            "raw_embedding": mysql_store.decode_json(row.get("embedding_json"), []),
            "embedding": _public_embedding(mysql_store.decode_json(row.get("embedding_json"), [])),
        }

    async def stats(self, tenant_id: str, user_id: str) -> dict[str, Any]:
        settings = get_settings()
        if self.backend() == "mysql":
            row = mysql_store.fetch_one(
                """
                SELECT COUNT(*) AS chunk_count, COUNT(DISTINCT paper_id) AS paper_count
                FROM scholar_rag_chunks
                WHERE tenant_id = %s AND user_id = %s
                """,
                (tenant_id, user_id),
            ) or {}
            return {
                "backend": "mysql",
                "chunk_count": int(row.get("chunk_count") or 0),
                "paper_count": int(row.get("paper_count") or 0),
                "index_backend": settings.rag_index_backend,
                "retrieval_mode": settings.rag_retrieval_mode,
                "embedding_provider": settings.rag_embedding_provider,
                "embedding_model": settings.rag_embedding_model,
                "chunk_size": settings.rag_chunk_size,
                "chunk_overlap": settings.rag_chunk_overlap,
                "top_k": settings.rag_top_k,
                "candidate_limit": settings.rag_candidate_limit,
            }
        async with self._lock:
            rows = [
                value
                for value in self._read_sync().values()
                if value.get("tenant_id") == tenant_id and value.get("user_id") == user_id
            ]
        return {
            "backend": "json",
            "chunk_count": len(rows),
            "paper_count": len({row.get("paper_id") for row in rows}),
            "index_backend": settings.rag_index_backend,
            "retrieval_mode": settings.rag_retrieval_mode,
            "embedding_provider": settings.rag_embedding_provider,
            "embedding_model": settings.rag_embedding_model,
            "chunk_size": settings.rag_chunk_size,
            "chunk_overlap": settings.rag_chunk_overlap,
            "top_k": settings.rag_top_k,
            "candidate_limit": settings.rag_candidate_limit,
        }


rag_service = RagService()
