from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from agents.delegation import DelegationResult, DelegationService
from agents.graph import run_global_workflow
from agents.orchestrator import general_orchestrator
from agents.registry import agent_registry
from app.schemas import UserContext


class AgentRoutingTests(unittest.TestCase):
    def test_simple_writing_uses_skill(self) -> None:
        decision = general_orchestrator.decide("写一篇异常检测综述", "survey_review")
        self.assertEqual(decision.target_agent, "writing_agent")
        self.assertEqual(decision.execution_mode, "skill")

    def test_complex_request_uses_delegation(self) -> None:
        content = (
            "请系统分析异常检测研究：第一，比较三类方法；第二，规划检索范围；"
            "第三，分别评估数据、模型与部署风险；最后给出完整方案和引用依据。"
        )
        decision = general_orchestrator.decide(content, "general_assistant")
        self.assertEqual(decision.execution_mode, "delegation")
        self.assertGreaterEqual(decision.complexity, 4)

    def test_leaf_agents_cannot_delegate(self) -> None:
        self.assertTrue(agent_registry.get("general_orchestrator").can_delegate)
        self.assertTrue(agent_registry.get("writing_agent").can_delegate)
        self.assertFalse(agent_registry.get("research_subagent").can_delegate)


class DelegationTests(unittest.IsolatedAsyncioTestCase):
    async def test_batch_is_bounded_and_returns_child_results(self) -> None:
        service = DelegationService(max_children=2)
        child = AsyncMock(side_effect=[
            DelegationResult("1", "research_subagent", "succeeded", "research", 3),
            DelegationResult("2", "critic_subagent", "succeeded", "critic", 4),
        ])
        service._run_child = child  # type: ignore[method-assign]
        results = await service.run_batch(
            UserContext("tenant-a", "user-a"), parent_run_id="parent", goal="goal",
            assignments=[
                {"agent_name": "research_subagent", "instruction": "a"},
                {"agent_name": "critic_subagent", "instruction": "b"},
                {"agent_name": "citation_subagent", "instruction": "c"},
            ],
        )
        self.assertEqual(len(results), 2)
        self.assertEqual(child.await_count, 2)

    async def test_forced_multi_agent_routes_writing_agent(self) -> None:
        state = {
            "task_id": "task-a", "tenant_id": "tenant-a", "user_id": "user-a",
            "topic": "短主题", "skill_name": "survey_generation", "agent_mode": "multi_agent",
        }

        async def fake_run(_state, *, complex_task):
            self.assertTrue(complex_task)
            yield {"event": "skill_result", "phase": "survey_generation", "payload": {"ok": True}}

        with patch("agents.graph.writing_agent.run", side_effect=fake_run), patch(
            "agents.graph.GlobalEvaluator.evaluate", return_value={"passed": True, "findings": []}
        ):
            events = [event async for event in run_global_workflow(state)]
        self.assertEqual(events[0]["payload"]["execution_mode"], "delegation")

    async def test_forced_skill_disables_delegation(self) -> None:
        state = {
            "task_id": "task-b", "tenant_id": "tenant-a", "user_id": "user-a",
            "topic": "请完整系统分析多个方向并分别评估方案", "skill_name": "survey_generation",
            "agent_mode": "skill",
        }

        async def fake_run(_state, *, complex_task):
            self.assertFalse(complex_task)
            yield {"event": "skill_result", "phase": "survey_generation", "payload": {"ok": True}}

        with patch("agents.graph.writing_agent.run", side_effect=fake_run), patch(
            "agents.graph.GlobalEvaluator.evaluate", return_value={"passed": True, "findings": []}
        ):
            events = [event async for event in run_global_workflow(state)]
        self.assertEqual(events[0]["payload"]["execution_mode"], "skill")


if __name__ == "__main__":
    unittest.main()
