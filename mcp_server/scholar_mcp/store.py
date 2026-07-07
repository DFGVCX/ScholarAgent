from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.services import mysql_store
from app.services.rag_service import rag_service
from mcp_server.scholar_mcp.models import PaperRecord


class KnowledgeStore:
    def __init__(self, path: Path | None = None) -> None:
        settings = get_settings()
        self.path = path or settings.storage_dir / "knowledge.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = asyncio.Lock()

    def _read_sync(self) -> dict[str, dict[str, Any]]:
        if not self.path.exists():
            return {}
        return json.loads(self.path.read_text(encoding="utf-8"))

    def _write_sync(self, data: dict[str, dict[str, Any]]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

    def _key(self, tenant_id: str, user_id: str, paper_id: str) -> str:
        return f"{tenant_id}:{user_id}:{paper_id}"

    async def save_paper(self, paper: PaperRecord) -> dict[str, Any]:
        if mysql_store.is_available():
            mysql_store.execute(
                """
                INSERT INTO scholar_knowledge_papers
                    (paper_id, tenant_id, user_id, source, title, authors_json, abstract, full_text,
                     published_at, doi, arxiv_id, url, metadata_json)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    source = VALUES(source),
                    title = VALUES(title),
                    authors_json = VALUES(authors_json),
                    abstract = VALUES(abstract),
                    full_text = VALUES(full_text),
                    published_at = VALUES(published_at),
                    doi = VALUES(doi),
                    arxiv_id = VALUES(arxiv_id),
                    url = VALUES(url),
                    metadata_json = VALUES(metadata_json)
                """,
                (
                    paper.paper_id,
                    paper.tenant_id,
                    paper.user_id,
                    paper.source,
                    paper.title,
                    mysql_store.encode_json(paper.authors),
                    paper.abstract,
                    paper.full_text,
                    paper.published_at,
                    paper.doi,
                    paper.arxiv_id,
                    paper.url,
                    mysql_store.encode_json(paper.metadata),
                ),
            )
            data = paper.to_dict()
            await rag_service.index_paper(data)
            return data
        async with self._lock:
            data = self._read_sync()
            data[self._key(paper.tenant_id, paper.user_id, paper.paper_id)] = paper.to_dict()
            self._write_sync(data)
        result = paper.to_dict()
        await rag_service.index_paper(result)
        return result

    async def search(self, tenant_id: str, user_id: str, query: str, limit: int) -> list[dict[str, Any]]:
        if mysql_store.is_available():
            query_l = query.lower()
            like = f"%{query_l}%"
            rows = mysql_store.fetch_all(
                """
                SELECT *
                FROM scholar_knowledge_papers
                WHERE tenant_id = %s
                  AND user_id = %s
                  AND (
                    %s = ''
                    OR LOWER(title) LIKE %s
                    OR LOWER(COALESCE(abstract, '')) LIKE %s
                    OR LOWER(paper_id) LIKE %s
                  )
                ORDER BY updated_at DESC
                LIMIT %s
                """,
                (tenant_id, user_id, query_l, like, like, like, limit),
            )
            return [self._from_mysql_row(row) for row in rows]
        query_l = query.lower()
        async with self._lock:
            values = list(self._read_sync().values())
        filtered = [
            item
            for item in values
            if item.get("tenant_id") == tenant_id
            and item.get("user_id") == user_id
            and (
                not query_l
                or query_l in item.get("title", "").lower()
                or query_l in item.get("abstract", "").lower()
            )
        ]
        return filtered[:limit]

    async def delete(self, tenant_id: str, user_id: str, paper_id: str) -> bool:
        if mysql_store.is_available():
            affected = mysql_store.execute(
                """
                DELETE FROM scholar_knowledge_papers
                WHERE tenant_id = %s AND user_id = %s AND paper_id = %s
                """,
                (tenant_id, user_id, paper_id),
            )
            return bool(affected)
        async with self._lock:
            data = self._read_sync()
            key = self._key(tenant_id, user_id, paper_id)
            item = data.get(key)
            if item is None:
                key = next(
                    (
                        data_key
                        for data_key, value in data.items()
                        if value.get("tenant_id") == tenant_id
                        and value.get("user_id") == user_id
                        and value.get("paper_id") == paper_id
                    ),
                    "",
                )
                item = data.get(key)
            if not item:
                return False
            del data[key]
            self._write_sync(data)
        await rag_service.delete_paper(tenant_id, user_id, paper_id)
        return True

    def _from_mysql_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "paper_id": row["paper_id"],
            "tenant_id": row["tenant_id"],
            "user_id": row["user_id"],
            "source": row["source"],
            "title": row["title"],
            "authors": mysql_store.decode_json(row.get("authors_json"), []),
            "abstract": row.get("abstract") or "",
            "full_text": row.get("full_text") or "",
            "published_at": row.get("published_at"),
            "doi": row.get("doi"),
            "arxiv_id": row.get("arxiv_id"),
            "url": row.get("url"),
            "metadata": mysql_store.decode_json(row.get("metadata_json"), {}),
        }


knowledge_store = KnowledgeStore()
