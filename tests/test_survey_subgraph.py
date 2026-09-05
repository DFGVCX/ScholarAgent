from __future__ import annotations

import unittest
import uuid
from unittest.mock import AsyncMock, patch

from agents.specialized.writing_lifecycle import CAPABILITY_EXECUTORS
from agents.task_graph import DynamicTaskPlanner
from skills.survey_generation.subgraph import build_lifecycle_graph, survey_subgraph


class SurveySubgraphTests(unittest.TestCase):
    def test_outer_subgraph_plans_then_executes_dynamic_lifecycle(self) -> None:
        graph = survey_subgraph.get_graph()
        self.assertTrue({"plan_task_graph", "execute_lifecycle"}.issubset(graph.nodes))

    def test_dynamic_lifecycle_has_real_skill_nodes_and_quality_routes(self) -> None:
        plan = DynamicTaskPlanner().plan_writing("goal", {})
        graph = build_lifecycle_graph(plan).get_graph()
        self.assertTrue(
            {"retrieval", "outline", "section_writing", "quality_review", "finish_lifecycle"}
            .issubset(graph.nodes)
        )
        edges = {(edge.source, edge.target) for edge in graph.edges}
        self.assertIn(("retrieval", "outline"), edges)
        self.assertIn(("outline", "section_writing"), edges)
        self.assertTrue(any(source == "quality_review" for source, _ in edges))


class LifecycleRetryTests(unittest.IsolatedAsyncioTestCase):
    async def test_quality_edge_retries_only_target_and_downstream(self) -> None:
        plan = DynamicTaskPlanner().plan_writing("goal", {})
        retrieval = AsyncMock(return_value=(
            {"papers": [{"paper_id": "paper:test:1", "title": "Paper", "authors": ["A"]}], "chunks": []},
            {"passed": True},
        ))
        outline = AsyncMock(return_value=(
            {
                "outline": [{"section_id": "section_3", "title": "Risks", "paper_ids": ["paper:test:1"]}],
                "outline_markdown": "# Goal\n## 1. Risks",
            },
            {"passed": True},
        ))
        writing = AsyncMock(side_effect=[
            (
                {
                    "sections": [{"section_id": "section_3", "title": "Risks", "content": "draft [paper:test:1]"}],
                    "section_reviews": [{"section_id": "section_3", "passed": False}],
                },
                {"passed": False},
            ),
            (
                {
                    "sections": [{"section_id": "section_3", "title": "Risks", "content": "revised [paper:test:1]"}],
                    "section_reviews": [{"section_id": "section_3", "passed": True}],
                },
                {"passed": True},
            ),
        ])
        quality = AsyncMock(side_effect=[
            (
                {
                    "quality_decision": {"passed": False, "retry_target": "section:section_3", "findings": ["weak"]},
                    "citation_audit": {"is_valid": True, "found_ids": ["paper:test:1"]},
                    "retry_target": "section:section_3",
                    "quality_retry_count": 1,
                    "retry_history": [{"retry_target": "section:section_3"}],
                },
                {"passed": False, "retry_target": "section:section_3"},
            ),
            (
                {
                    "quality_decision": {"passed": True, "retry_target": "", "findings": []},
                    "citation_audit": {"is_valid": True, "found_ids": ["paper:test:1"]},
                    "retry_target": "",
                    "quality_retry_count": 1,
                    "retry_history": [{"retry_target": "section:section_3"}],
                },
                {"passed": True, "retry_target": ""},
            ),
        ])
        replacements = {
            "literature_retrieval": type("Executor", (), {"execute": retrieval})(),
            "outline_generation": type("Executor", (), {"execute": outline})(),
            "section_writing": type("Executor", (), {"execute": writing})(),
            "quality_review": type("Executor", (), {"execute": quality})(),
        }
        with patch.dict(CAPABILITY_EXECUTORS, replacements, clear=True):
            result = await build_lifecycle_graph(plan).ainvoke({
                "task_id": f"retry-{uuid.uuid4().hex}",
                "tenant_id": "tenant-test",
                "user_id": "user-test",
                "topic": "goal",
                "citation_style": "IEEE",
            })

        self.assertEqual(retrieval.await_count, 1)
        self.assertEqual(outline.await_count, 1)
        self.assertEqual(writing.await_count, 2)
        self.assertEqual(quality.await_count, 2)
        self.assertIn("revised", result["skill_result"]["markdown"])


if __name__ == "__main__":
    unittest.main()
