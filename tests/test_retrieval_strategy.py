from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.routes.tasks import SurveyTaskRequestDTO
from app.schemas import RetrievalStrategy, SurveyTaskRequest
from agents.conversation_tool_loop import ConversationToolLoop
from mcp_server.scholar_mcp.tools import search_papers


class RetrievalStrategySchemaTests(unittest.TestCase):
    def test_agent_search_summary_counts_chunks_and_source_papers_separately(self) -> None:
        message = ConversationToolLoop._result_message(
            "search_papers",
            {
                "local_hits": [
                    {"paper_id": "paper-1", "title": "Paper", "can_cite": True},
                    {"paper_id": "paper-1", "title": "Paper", "can_cite": True},
                ],
                "external_candidates": [],
            },
        )

        self.assertIn("2 个本地可引用证据 Chunk", message)
        self.assertIn("来自 1 篇论文", message)

    def test_public_survey_request_uses_retrieval_strategy(self) -> None:
        request = SurveyTaskRequestDTO.model_validate(
            {
                "topic": "点云修复",
                "retrieval_strategy": "hybrid",
                "retrieval_constraints": "优先近三年并包含公开数据集",
                "agent_mode": "multi_agent",
            }
        )

        self.assertEqual(request.retrieval_strategy, RetrievalStrategy.HYBRID)
        self.assertEqual(request.retrieval_constraints, "优先近三年并包含公开数据集")
        self.assertFalse(hasattr(request, "agent_mode"))

    def test_domain_request_defaults_to_online_and_auto_routing(self) -> None:
        request = SurveyTaskRequest.from_mapping({"topic": "异常检测"})

        self.assertEqual(request.retrieval_strategy, RetrievalStrategy.ONLINE)
        self.assertEqual(request.agent_mode.value, "auto")


class RetrievalScopeTests(unittest.IsolatedAsyncioTestCase):
    async def test_online_scope_does_not_query_tenant_knowledge(self) -> None:
        local_search = AsyncMock(return_value={"local_hits": [], "items": []})
        with patch("mcp_server.scholar_mcp.tools.rag_service.search", local_search), patch(
            "mcp_server.scholar_mcp.tools._mock_external_sources_enabled", return_value=True
        ):
            result = await search_papers("tenant-a", "user-a", "点云", source="external", limit=2)

        local_search.assert_not_awaited()
        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["local_hits"], [])
        self.assertTrue(all(not item["can_cite"] for item in result["external_candidates"]))

    async def test_local_scope_never_calls_external_sources(self) -> None:
        local_item = {
            "paper_id": "paper:local:1",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "title": "本地论文",
        }
        local_search = AsyncMock(return_value={"local_hits": [{**local_item, "can_cite": True}], "items": []})
        with patch("mcp_server.scholar_mcp.tools.rag_service.search", local_search), patch(
            "mcp_server.scholar_mcp.tools.knowledge_store.get", AsyncMock(return_value=local_item)
        ), patch(
            "mcp_server.scholar_mcp.tools._mock_external_sources_enabled", return_value=True
        ):
            result = await search_papers("tenant-a", "user-a", "点云", source="local", limit=2)

        local_search.assert_awaited_once()
        self.assertEqual(result["items"][0]["paper_id"], local_item["paper_id"])
        self.assertTrue(result["items"][0]["can_cite"])
        self.assertEqual(result["external_candidates"], [])

    async def test_local_scope_preserves_top_k_chunks_and_merged_contexts(self) -> None:
        paper = {
            "paper_id": "paper:local:1",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "title": "本地论文",
        }
        hits = [
            {**paper, "chunk_id": "chunk-1", "snippet": "first", "can_cite": True},
            {**paper, "chunk_id": "chunk-2", "snippet": "second", "can_cite": True},
        ]
        contexts = [
            {
                "context_id": "paper:local:1@v1#chunk-1..chunk-2",
                "chunk_ids": ["chunk-1", "chunk-2"],
                "content": "first\n\nsecond",
            }
        ]
        local_search = AsyncMock(
            return_value={
                "local_hits": hits,
                "items": hits,
                "merged_contexts": contexts,
                "retrieval_mode": "hybrid",
                "debug": {"candidate_pools": {"lexical": {"count": 2}}},
            }
        )
        with patch(
            "mcp_server.scholar_mcp.tools.rag_service.search", local_search
        ), patch(
            "mcp_server.scholar_mcp.tools.knowledge_store.get",
            AsyncMock(return_value=paper),
        ):
            result = await search_papers(
                "tenant-a", "user-a", "边界证据", source="local", limit=2
            )

        self.assertEqual(
            [item["chunk_id"] for item in result["local_hits"]],
            ["chunk-1", "chunk-2"],
        )
        self.assertEqual(result["merged_contexts"], contexts)
        self.assertEqual(result["retrieval_mode"], "hybrid")
        self.assertEqual(result["debug"]["candidate_pools"]["lexical"]["count"], 2)

    async def test_hybrid_scope_combines_local_and_external_results(self) -> None:
        local_item = {
            "paper_id": "paper:local:1",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "title": "本地论文",
        }
        with patch(
            "mcp_server.scholar_mcp.tools.rag_service.search",
            AsyncMock(return_value={"local_hits": [{**local_item, "can_cite": True}], "items": []}),
        ), patch(
            "mcp_server.scholar_mcp.tools.knowledge_store.get", AsyncMock(return_value=local_item)
        ), patch("mcp_server.scholar_mcp.tools._mock_external_sources_enabled", return_value=True):
            result = await search_papers("tenant-a", "user-a", "点云", source="all", limit=2)

        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["paper_id"], "paper:local:1")
        self.assertEqual(len(result["local_hits"]), 1)
        self.assertEqual(len(result["external_candidates"]), 1)


if __name__ == "__main__":
    unittest.main()
