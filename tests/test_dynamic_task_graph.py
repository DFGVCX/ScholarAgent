from __future__ import annotations

import unittest

from agents.task_graph import DynamicTaskPlanner, TaskGraphExecutor, TaskGraphPlan, TaskNode


class DynamicTaskGraphTests(unittest.IsolatedAsyncioTestCase):
    def test_writing_plan_is_dependency_aware(self) -> None:
        plan = DynamicTaskPlanner().plan_writing(
            "Compare recent retrieval methods and produce a cited survey",
            {"citation_style": "IEEE", "max_papers": 20},
        )
        nodes = {item.node_id: item for item in plan.nodes}
        self.assertEqual(nodes["argument_structure"].depends_on, ("research_scope",))
        self.assertIn("citation_policy", nodes)

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
