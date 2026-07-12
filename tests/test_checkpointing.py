from __future__ import annotations

import os
from pathlib import Path
import unittest
from uuid import uuid4

from agents.checkpointing import checkpoint_provider


class CheckpointingTests(unittest.IsolatedAsyncioTestCase):
    async def test_sqlite_checkpointer_is_durable_backend(self) -> None:
        path = Path("storage/runtime/test-artifacts") / f"checkpoint-{uuid4().hex}.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        old_backend = os.environ.get("SCHOLAR_CHECKPOINT_BACKEND")
        old_path = os.environ.get("SCHOLAR_CHECKPOINT_SQLITE_PATH")
        try:
            os.environ["SCHOLAR_CHECKPOINT_BACKEND"] = "sqlite"
            os.environ["SCHOLAR_CHECKPOINT_SQLITE_PATH"] = str(path)
            saver = await checkpoint_provider.get()
            self.assertEqual(type(saver).__name__, "AsyncSqliteSaver")
            self.assertTrue(path.exists())
        finally:
            await checkpoint_provider.close()
            if old_backend is None:
                os.environ.pop("SCHOLAR_CHECKPOINT_BACKEND", None)
            else:
                os.environ["SCHOLAR_CHECKPOINT_BACKEND"] = old_backend
            if old_path is None:
                os.environ.pop("SCHOLAR_CHECKPOINT_SQLITE_PATH", None)
            else:
                os.environ["SCHOLAR_CHECKPOINT_SQLITE_PATH"] = old_path
            path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
