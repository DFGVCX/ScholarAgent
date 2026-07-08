from __future__ import annotations

import unittest
from uuid import uuid4

from mcp_server.scholar_mcp.models import PaperRecord
from mcp_server.scholar_mcp.store import knowledge_store
from app.services.rag_service import rag_service


class KnowledgeBaseToggleTest(unittest.IsolatedAsyncioTestCase):
    """Phase 2: toggle_knowledge_base MCP tool tests.

    These tests exercise the store and MCP tool directly (not the route
    layer), because KnowledgePaperDTO will not get the ``in_knowledge_base``
    field until Task 4.
    """

    async def test_upload_without_indexing_and_toggle_on(self):
        title = f"KB opt-out test {uuid4()}"

        # 1. Save paper but DO NOT index (in_knowledge_base=False)
        record = PaperRecord(
            paper_id=f"paper:manual:{uuid4().hex[:12]}",
            tenant_id="tenant_demo",
            user_id="user_demo",
            source="manual",
            title=title,
            authors=["Test"],
            abstract="Testing in_knowledge_base flag.",
            in_knowledge_base=False,
        )
        saved = await knowledge_store.save_paper(record)
        paper_id = saved["paper_id"]
        self.assertFalse(saved.get("in_knowledge_base", True))

        # 2. Verify the paper is NOT in ChromaDB
        rag_result = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertFalse(
            any(item["paper_id"] == paper_id for item in rag_result["items"])
        )

        # 3. Toggle on via the MCP tool
        from mcp_server.scholar_mcp.tools import toggle_knowledge_base

        result = await toggle_knowledge_base(
            tenant_id="tenant_demo",
            user_id="user_demo",
            paper_id=paper_id,
            in_knowledge_base=True,
        )
        self.assertTrue(result["in_knowledge_base"])

        # 4. Verify the paper IS now in ChromaDB
        rag_result2 = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertTrue(
            any(item["paper_id"] == paper_id for item in rag_result2["items"])
        )

    async def test_toggle_off_removes_from_index(self):
        title = f"KB toggle-off test {uuid4()}"

        # 1. Save paper with DEFAULT indexing (in_knowledge_base=True)
        record = PaperRecord(
            paper_id=f"paper:manual:{uuid4().hex[:12]}",
            tenant_id="tenant_demo",
            user_id="user_demo",
            source="manual",
            title=title,
            authors=["Test"],
            abstract="Will be toggled off.",
            in_knowledge_base=True,
        )
        saved = await knowledge_store.save_paper(record)
        paper_id = saved["paper_id"]

        # 2. Verify the paper IS in the index
        rag_result = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertTrue(
            any(item["paper_id"] == paper_id for item in rag_result["items"])
        )

        # 3. Toggle off via the MCP tool
        from mcp_server.scholar_mcp.tools import toggle_knowledge_base

        result = await toggle_knowledge_base(
            tenant_id="tenant_demo",
            user_id="user_demo",
            paper_id=paper_id,
            in_knowledge_base=False,
        )
        self.assertFalse(result["in_knowledge_base"])

        # 4. Verify the paper is REMOVED from the index
        rag_result2 = await rag_service.search("tenant_demo", "user_demo", title, 5)
        self.assertFalse(
            any(item["paper_id"] == paper_id for item in rag_result2["items"])
        )


if __name__ == "__main__":
    unittest.main()
