import asyncio
import os
from pathlib import Path
import unittest
import uuid

from agents.graph import run_global_workflow
from app.services.outline_approval import outline_approval_registry


class SurveyWorkflowTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self._old_env = {
            key: os.environ.get(key)
            for key in (
                "SCHOLAR_ALLOW_MOCK_DATA",
                "SCHOLAR_EXTERNAL_SOURCE_PROVIDER",
                "SCHOLAR_PRIMARY_MODEL_PROVIDER",
                "SCHOLAR_RAG_EMBEDDING_PROVIDER",
                "SCHOLAR_RUNTIME_CONFIG_PATH",
            )
        }
        self._runtime_config_path = Path("storage/runtime") / f"test_runtime_config_{uuid.uuid4().hex}.json"
        self._runtime_config_path.parent.mkdir(parents=True, exist_ok=True)
        os.environ["SCHOLAR_RUNTIME_CONFIG_PATH"] = str(self._runtime_config_path)
        os.environ["SCHOLAR_ALLOW_MOCK_DATA"] = "true"
        os.environ["SCHOLAR_EXTERNAL_SOURCE_PROVIDER"] = "mock"
        os.environ["SCHOLAR_PRIMARY_MODEL_PROVIDER"] = "deterministic"
        os.environ["SCHOLAR_RAG_EMBEDDING_PROVIDER"] = "mock-hash"

    async def asyncTearDown(self):
        for key, value in self._old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        self._runtime_config_path.unlink(missing_ok=True)

    async def test_workflow_produces_completed_event(self):
        events = []
        async for event in run_global_workflow(
            {
                "task_id": "test-task",
                "tenant_id": "tenant_demo",
                "user_id": "user_demo",
                "trace_id": "trace-test",
                "topic": "LLM evaluation",
                "input_type": "arxiv",
                "input_value": "2301.00001",
                "citation_style": "IEEE",
                "max_papers": 5,
            }
        ):
            events.append(event)

        self.assertEqual(events[0]["payload"]["active_skill"], "survey_generation")
        self.assertEqual(events[-1]["event"], "completed")
        result = events[-1]["payload"]
        self.assertIn("# Survey on LLM evaluation", result["markdown"])
        self.assertTrue(result["citation_audit"]["is_valid"])

    async def test_unknown_skill_fails_before_execution(self):
        events = []
        with self.assertRaises(KeyError):
            async for event in run_global_workflow({"skill_name": "missing_skill"}):
                events.append(event)

        self.assertEqual(events[0]["payload"]["active_skill"], "missing_skill")

    async def test_workflow_waits_for_outline_confirmation(self):
        events = []

        async def run_workflow():
            async for event in run_global_workflow(
                {
                    "task_id": "confirm-task",
                    "tenant_id": "tenant_demo",
                    "user_id": "user_demo",
                    "trace_id": "trace-confirm",
                    "topic": "LLM safety",
                    "input_type": "arxiv",
                    "input_value": "2301.00001",
                    "citation_style": "IEEE",
                    "max_papers": 5,
                    "require_outline_confirmation": True,
                }
            ):
                events.append(event)

        task = asyncio.create_task(run_workflow())
        for _ in range(40):
            if any(event["event"] == "outline_required" for event in events):
                break
            await asyncio.sleep(0.05)

        self.assertTrue(any(event["event"] == "outline_required" for event in events))
        self.assertFalse(task.done())
        edited_outline = (
            "# Survey Outline: LLM safety\n"
            "## 1. Human-in-the-loop safety protocol\n"
            "## 2. Evidence calibration and deployment review"
        )
        self.assertTrue(outline_approval_registry.approve("confirm-task", "test approval", edited_outline))
        await asyncio.wait_for(task, timeout=10)
        self.assertEqual(events[-1]["event"], "completed")
        result = events[-1]["payload"]
        self.assertEqual(result["outline"][0]["title"], "Human-in-the-loop safety protocol")
        self.assertIn("Human-in-the-loop safety protocol", result["markdown"])


if __name__ == "__main__":
    unittest.main()
