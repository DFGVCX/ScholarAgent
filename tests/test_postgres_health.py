from __future__ import annotations

import unittest
from unittest.mock import patch

from app.routes.health import health


class PostgreSQLHealthTest(unittest.IsolatedAsyncioTestCase):
    async def test_health_reports_postgres_pgvector_and_qwen(self) -> None:
        with patch("app.routes.health.mysql_store.is_available", return_value=True):
            data = await health()

        self.assertEqual(data["status"], "ok")
        self.assertEqual(data["database"]["engine"], "postgresql")
        self.assertTrue(data["database"]["pgvector"])
        self.assertEqual(data["retrieval"]["embedding_model"], "Qwen3-Embedding-0.6B")


if __name__ == "__main__":
    unittest.main()
