from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from agents.factory import ModelResponse
from agents.task_graph import (
    LIFECYCLE_CAPABILITIES,
    DynamicTaskPlanner,
    TaskGraphExecutor,
    TaskGraphPlan,
    TaskNode,
)


class DynamicTaskGraphTests(unittest.IsolatedAsyncioTestCase):
    def test_writing_plan_is_dependency_aware(self) -> None:
        plan = DynamicTaskPlanner().plan_writing(
            "Compare recent retrieval methods and produce a cited survey",
            {"citation_style": "IEEE", "max_papers": 20},
        )
        nodes = {item.node_id: item for item in plan.nodes}
        self.assertEqual(nodes["outline"].depends_on, ("retrieval",))
        self.assertEqual(nodes["section_writing"].depends_on, ("outline",))
        self.assertEqual(nodes["quality_review"].depends_on, ("section_writing",))
        self.assertEqual({node.capability for node in plan.nodes}, set(LIFECYCLE_CAPABILITIES))

    async def test_model_planner_returns_structured_dynamic_plan(self) -> None:
        response = ModelResponse(
            content='{"rationale":["history-aware"],"nodes":['
            '{"node_id":"find_evidence","capability":"literature_retrieval","depends_on":[]},'
            '{"node_id":"shape_outline","capability":"outline_generation","depends_on":["find_evidence"]},'
            '{"node_id":"draft_sections","capability":"section_writing","depends_on":["shape_outline"]},'
            '{"node_id":"review_quality","capability":"quality_review","depends_on":["draft_sections"]}'
            ']}',
            provider="test",
            model="planner",
        )
        with patch("agents.task_graph.model_factory.generate_text", AsyncMock(return_value=response)):
            plan = await DynamicTaskPlanner().plan_writing_with_model(
                "goal", {"memory_context": {"style": "concise"}}, [{"name": "survey_generation"}]
            )
        self.assertEqual(plan.planner, "model:test/planner")
        self.assertEqual(plan.nodes[0].node_id, "find_evidence")

    async def test_executor_runs_dependency_waves(self) -> None:
        plan = TaskGraphPlan("goal", (
            TaskNode("a", "a", "research_subagent", "a"),
            TaskNode("b", "b", "critic_subagent", "b", depends_on=("a",)),
        ))
        seen = []

        async def runner(node, dependencies):
            seen.append((node.node_id, tuple(dependencies)))
            return node.node_id

        self.assertEqual(await TaskGraphExecutor().execute(plan, runner), ["a", "b"])
        self.assertEqual(seen, [("a", ()), ("b", ("a",))])

    async def test_cycle_is_rejected(self) -> None:
        plan = TaskGraphPlan("goal", (
            TaskNode("a", "a", "research_subagent", "a", depends_on=("b",)),
        ))

        async def runner(*_args):
            return None

        with self.assertRaises(ValueError):
            await TaskGraphExecutor().execute(plan, runner)


if __name__ == "__main__":
    unittest.main()
