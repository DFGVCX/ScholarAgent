from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from app.routes.tasks import SurveyTaskRequestDTO
from app.schemas import RetrievalStrategy, SurveyTaskRequest
from mcp_server.scholar_mcp.tools import search_papers


class RetrievalStrategySchemaTests(unittest.TestCase):
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
        local_search = AsyncMock(return_value=[])
        with patch("mcp_server.scholar_mcp.tools.knowledge_store.search", local_search), patch(
            "mcp_server.scholar_mcp.tools._mock_external_sources_enabled", return_value=True
        ):
            result = await search_papers("tenant-a", "user-a", "点云", source="external", limit=2)

        local_search.assert_not_awaited()
        self.assertEqual(len(result["items"]), 2)

    async def test_local_scope_never_calls_external_sources(self) -> None:
        local_item = {
            "paper_id": "paper:local:1",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "title": "本地论文",
        }
        local_search = AsyncMock(return_value=[local_item])
        with patch("mcp_server.scholar_mcp.tools.knowledge_store.search", local_search), patch(
            "mcp_server.scholar_mcp.tools._mock_external_sources_enabled", return_value=True
        ):
            result = await search_papers("tenant-a", "user-a", "点云", source="local", limit=2)

        local_search.assert_awaited_once()
        self.assertEqual(result["items"], [local_item])

    async def test_hybrid_scope_combines_local_and_external_results(self) -> None:
        local_item = {
            "paper_id": "paper:local:1",
            "tenant_id": "tenant-a",
            "user_id": "user-a",
            "title": "本地论文",
        }
        with patch(
            "mcp_server.scholar_mcp.tools.knowledge_store.search",
            AsyncMock(return_value=[local_item]),
        ), patch("mcp_server.scholar_mcp.tools._mock_external_sources_enabled", return_value=True):
            result = await search_papers("tenant-a", "user-a", "点云", source="all", limit=2)

        self.assertEqual(len(result["items"]), 2)
        self.assertEqual(result["items"][0]["paper_id"], "paper:local:1")


if __name__ == "__main__":
    unittest.main()
