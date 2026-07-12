from __future__ import annotations

import os
import unittest
from unittest.mock import AsyncMock, patch

from app.services.task_queue import TaskQueue


class TaskQueueTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.previous = {
            "SCHOLAR_TASK_EXECUTION_MODE": os.environ.get("SCHOLAR_TASK_EXECUTION_MODE"),
            "SCHOLAR_REDIS_URL": os.environ.get("SCHOLAR_REDIS_URL"),
        }

    def tearDown(self) -> None:
        for key, value in self.previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_queue_is_opt_in_for_local_runtime(self) -> None:
        os.environ["SCHOLAR_TASK_EXECUTION_MODE"] = "inline"
        os.environ["SCHOLAR_REDIS_URL"] = "redis://localhost:6379/0"
        self.assertFalse(TaskQueue().enabled())

    async def test_enqueue_is_tenant_scoped(self) -> None:
        os.environ["SCHOLAR_TASK_EXECUTION_MODE"] = "queue"
        os.environ["SCHOLAR_REDIS_URL"] = "redis://localhost:6379/0"
        queue = TaskQueue()
        client = AsyncMock()
        with patch.object(queue, "_redis", return_value=client):
            await queue.enqueue("tenant-a", "user-a", "task-a")
        raw = client.lpush.await_args.args[1]
        self.assertIn('"tenant_id": "tenant-a"', raw)
        self.assertIn('"user_id": "user-a"', raw)

    async def test_processing_jobs_are_recovered(self) -> None:
        os.environ["SCHOLAR_TASK_EXECUTION_MODE"] = "queue"
        os.environ["SCHOLAR_REDIS_URL"] = "redis://localhost:6379/0"
        queue = TaskQueue()
        client = AsyncMock()
        client.rpoplpush = AsyncMock(side_effect=["job-a", None])
        with patch.object(queue, "_redis", return_value=client):
            recovered = await queue.recover_processing()
        self.assertEqual(recovered, 1)


if __name__ == "__main__":
    unittest.main()
