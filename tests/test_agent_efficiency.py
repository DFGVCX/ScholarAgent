from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from agents.evolution.service import SkillEvolutionService
from agents.factory import ModelFactory, ModelResponse
from agents.runtime.token_policy import TokenPolicy
from app.schemas import UserContext


class TokenPolicyTests(unittest.TestCase):
    def test_intent_prompt_is_trimmed_to_purpose_budget(self) -> None:
        policy = TokenPolicy()
        prompt, context, budget, estimated = policy.prepare(
            "intent_planning",
            "历史对话 " * 4000 + "当前请求：检索点云论文",
            {"conversation_state": {"active_source": "cnki"}},
        )

        self.assertLessEqual(estimated, budget.max_input_tokens)
        self.assertIn("当前请求：检索点云论文", prompt)
        self.assertEqual(context["conversation_state"]["active_source"], "cnki")


class ModelCacheTests(unittest.IsolatedAsyncioTestCase):
    async def test_identical_tenant_scoped_planning_call_is_reused(self) -> None:
        factory = ModelFactory()
        settings = SimpleNamespace(
            primary_model_provider="qwen",
            secondary_model_provider="none",
            model_response_cache_enabled=True,
            model_response_cache_max_entries=32,
        )
        generated = ModelResponse("{\"action\":\"none\"}", "qwen", "qwen-flash", 120, 8)
        with patch("agents.factory.get_settings", return_value=settings), patch.object(
            factory, "_generate_with_provider", new=AsyncMock(return_value=generated)
        ) as call:
            first = await factory.generate_text(
                "intent_planning", "同一请求", {"tenant_id": "t1", "user_id": "u1"}
            )
            second = await factory.generate_text(
                "intent_planning", "同一请求", {"tenant_id": "t1", "user_id": "u1"}
            )

        self.assertEqual(call.await_count, 1)
        self.assertFalse(first.cached)
        self.assertTrue(second.cached)

    async def test_cache_does_not_cross_tenants(self) -> None:
        factory = ModelFactory()
        settings = SimpleNamespace(
            primary_model_provider="qwen",
            secondary_model_provider="none",
            model_response_cache_enabled=True,
            model_response_cache_max_entries=32,
        )
        generated = ModelResponse("ok", "qwen", "qwen-flash")
        with patch("agents.factory.get_settings", return_value=settings), patch.object(
            factory, "_generate_with_provider", new=AsyncMock(return_value=generated)
        ) as call:
            await factory.generate_text("intent_planning", "同一请求", {"tenant_id": "t1", "user_id": "u1"})
            await factory.generate_text("intent_planning", "同一请求", {"tenant_id": "t2", "user_id": "u1"})

        self.assertEqual(call.await_count, 2)


class SkillEvolutionTests(unittest.TestCase):
    def test_successful_pattern_creates_disabled_review_candidate(self) -> None:
        service = SkillEvolutionService(minimum_evidence=1)
        user = UserContext("tenant-a", "user-a")
        with patch(
            "agents.evolution.service.mysql_store.fetch_one", side_effect=[None, None]
        ), patch("agents.evolution.service.mysql_store.execute") as execute:
            candidate = service.record_tool_outcome(
                user,
                "conversation-a",
                "search_papers",
                {
                    "tenant_id": "tenant-a",
                    "user_id": "user-a",
                    "query": "点云",
                    "source": "all",
                    "api_key": "must-not-leak",
                },
                "succeeded",
            )

        self.assertGreaterEqual(execute.call_count, 2)
        self.assertEqual(candidate["status"], "draft")
        self.assertFalse(candidate["enabled"])
        arguments = candidate["recipe"]["steps"][0]["arguments"]
        self.assertEqual(arguments["query"], "{{query}}")
        self.assertNotIn("api_key", arguments)
        self.assertNotIn("tenant_id", arguments)


if __name__ == "__main__":
    unittest.main()
