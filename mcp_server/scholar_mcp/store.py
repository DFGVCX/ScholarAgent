from __future__ import annotations

from pathlib import Path
from typing import Any

from app.db.session import tenant_transaction
from app.papers.repository import PaperRepository
from app.services.rag_service import rag_service
from mcp_server.scholar_mcp.models import PaperRecord


def _paper_input(paper: PaperRecord) -> dict[str, Any]:
    return paper.to_dict()


class KnowledgeStore:
    """PostgreSQL-only paper store; `path` remains accepted for API compatibility."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path

    async def save_paper(self, paper: PaperRecord) -> dict[str, Any]:
        payload = _paper_input(paper)
        await rag_service.index_paper(payload)
        saved = await self.get(paper.tenant_id, paper.user_id, paper.paper_id)
        if saved is None:
            raise RuntimeError("paper was not visible after PostgreSQL ingestion")
        return saved

    async def get(self, tenant_id: str, user_id: str, paper_id: str) -> dict[str, Any] | None:
        async with tenant_transaction(tenant_id, user_id) as session:
            return await PaperRepository(session).get_document(tenant_id, user_id, paper_id)

    async def toggle_kb(
        self, tenant_id: str, user_id: str, paper_id: str, in_knowledge_base: bool
    ) -> bool:
        async with tenant_transaction(tenant_id, user_id) as session:
            changed = await PaperRepository(session).set_knowledge_base(
                tenant_id, user_id, paper_id, in_knowledge_base
            )
        return in_knowledge_base if changed else False

    async def search(
        self, tenant_id: str, user_id: str, query: str, limit: int
    ) -> list[dict[str, Any]]:
        async with tenant_transaction(tenant_id, user_id) as session:
            return await PaperRepository(session).list_documents(
                tenant_id, user_id, query=query, limit=limit
            )

    async def delete(self, tenant_id: str, user_id: str, paper_id: str) -> bool:
        async with tenant_transaction(tenant_id, user_id) as session:
            return await PaperRepository(session).soft_delete(tenant_id, user_id, paper_id)


knowledge_store = KnowledgeStore()
