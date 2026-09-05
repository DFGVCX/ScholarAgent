from __future__ import annotations

import unittest
import uuid

from agents.specialized.writing_lifecycle import QualitySkillAgent
from agents.task_graph import DynamicTaskPlanner
from app.services.node_run_store import node_run_store


class NodeRunStoreTests(unittest.TestCase):
    def test_cache_and_section_invalidation_are_scoped(self) -> None:
        task_id = f"node-store-{uuid.uuid4().hex}"
        state = {"tenant_id": "tenant-test", "user_id": "user-test", "task_id": task_id}
        plan = DynamicTaskPlanner().plan_writing("goal", {})
        payload = {"section": "section_3"}
        dependencies = {"outline": {"version": "1"}}
        fingerprint = node_run_store.fingerprint(payload, dependencies)
        section_run = node_run_store.start(
            **state,
            node_id="section:section_3",
            capability="section_writing",
            version="1.0.0",
            fingerprint=fingerprint,
            payload=payload,
            dependencies=dependencies,
        )
        node_run_store.complete(section_run, {"content": "draft"}, {"passed": False})
        retrieval_run = node_run_store.start(
            **state,
            node_id="retrieval",
            capability="literature_retrieval",
            version="1.0.0",
            fingerprint="retrieval-fingerprint",
            payload={},
            dependencies={},
        )
        node_run_store.complete(retrieval_run, {"papers": [1]}, {"passed": True})

        invalidated = node_run_store.invalidate(plan, state, "section:section_3")

        self.assertIn("section:section_3", invalidated)
        self.assertIn("section_writing", invalidated)
        self.assertNotIn("retrieval", invalidated)
        self.assertIsNone(
            node_run_store.latest_completed(
                **state, node_id="section:section_3", fingerprint=fingerprint
            )
        )
        self.assertIsNotNone(
            node_run_store.latest_completed(
                **state, node_id="retrieval", fingerprint="retrieval-fingerprint"
            )
        )


class QualitySkillAgentTests(unittest.IsolatedAsyncioTestCase):
    async def test_failed_section_returns_concrete_retry_target(self) -> None:
        output, quality = await QualitySkillAgent().execute(
            {
                "papers": [{"paper_id": "paper:test:1"}],
                "outline": [{"section_id": "section_3", "title": "Risks"}],
                "sections": [{
                    "section_id": "section_3",
                    "title": "Risks",
                    "content": "Unsupported draft [paper:test:1]",
                }],
                "section_reviews": [{
                    "section_id": "section_3",
                    "passed": False,
                    "findings": ["insufficient evidence"],
                }],
            },
            DynamicTaskPlanner().plan_writing("goal", {}).node_for_capability("quality_review"),
        )
        self.assertFalse(quality["passed"])
        self.assertEqual(quality["retry_target"], "section:section_3")
        self.assertEqual(output["retry_target"], "section:section_3")


if __name__ == "__main__":
    unittest.main()
