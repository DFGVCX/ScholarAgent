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


class ChunkingTest(unittest.TestCase):
    def test_paragraph_chunking_preserves_boundaries(self):
        from app.services.rag_service import _chunk_text
        text = "第一段内容。\n\n第二段内容。\n\n第三段很长" + "的内容。" * 100
        import os
        os.environ["SCHOLAR_RAG_CHUNK_STRATEGY"] = "paragraph"
        os.environ["SCHOLAR_RAG_CHUNK_SIZE"] = "50"
        os.environ["SCHOLAR_RAG_CHUNK_OVERLAP"] = "10"
        chunks = _chunk_text(text, size=50, overlap=10)
        # 段落边界不应被切断："第一段内容。" 和 "第二段内容。" 应各在一个 chunk 中
        self.assertTrue(any("第一段内容" in c for c in chunks))
        self.assertTrue(any("第二段内容" in c for c in chunks))
        # 不应该有跨段落边界的 chunk 同时包含两段开头
        for chunk in chunks:
            self.assertFalse("第一段内容" in chunk and "第二段内容" in chunk)

    def test_fixed_mode_unchanged(self):
        from app.services.rag_service import _chunk_text
        text = "A" * 100 + " " + "B" * 100
        import os
        os.environ["SCHOLAR_RAG_CHUNK_STRATEGY"] = "fixed"
        os.environ["SCHOLAR_RAG_CHUNK_SIZE"] = "30"
        os.environ["SCHOLAR_RAG_CHUNK_OVERLAP"] = "5"
        chunks = _chunk_text(text, size=30, overlap=5)
        self.assertGreater(len(chunks), 1)


class LexicalEmbeddingTest(unittest.TestCase):
    def test_lexical_embedding_is_local_and_deterministic(self):
        from app.services.rag_service import _lexical_embedding

        first = _lexical_embedding("citation audit retrieval", 128)
        second = _lexical_embedding("citation audit retrieval", 128)

        self.assertEqual(first, second)
        self.assertEqual(len(first), 128)
        self.assertTrue(any(value != 0 for value in first))

    def test_related_text_has_higher_similarity(self):
        from app.services.rag_service import _lexical_embedding

        query = _lexical_embedding("citation audit", 256)
        related = _lexical_embedding("citation audit for academic papers", 256)
        unrelated = _lexical_embedding("database deployment pipeline", 256)

        related_score = sum(a * b for a, b in zip(query, related, strict=True))
        unrelated_score = sum(a * b for a, b in zip(query, unrelated, strict=True))
        self.assertGreater(related_score, unrelated_score)

if __name__ == "__main__":
    unittest.main()
