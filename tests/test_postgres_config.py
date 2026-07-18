from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.config import get_settings


class PostgreSQLConfigTest(unittest.TestCase):
    def test_postgres_and_qwen_defaults(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_RAG_EMBEDDING_BASE_URL": "https://dashscope.aliyuncs.com/compatible-mode",
            "SCHOLAR_RAG_EMBEDDING_API_KEY": "test-key",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.database_url, env["SCHOLAR_DATABASE_URL"])
        self.assertEqual(settings.rag_embedding_provider, "qwen")
        self.assertEqual(settings.rag_embedding_model, "Qwen3-Embedding-0.6B")
        self.assertEqual(settings.rag_embedding_dimensions, 1024)
        self.assertEqual(settings.pdf_parse_strategy, "structure_aware_v1")
        self.assertEqual(settings.rag_chunk_strategy, "structure_aware_v1")

    def test_database_url_must_be_postgresql(self) -> None:
        with patch.dict(
            os.environ,
            {"SCHOLAR_DATABASE_URL": "sqlite:///storage/runtime/scholar.db"},
            clear=False,
        ), patch("app.config.read_runtime_config", return_value={}):
            with self.assertRaisesRegex(ValueError, "PostgreSQL"):
                get_settings()


if __name__ == "__main__":
    unittest.main()
