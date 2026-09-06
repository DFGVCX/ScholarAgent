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
        self.assertEqual(settings.rag_embedding_model, "qwen3.7-text-embedding")
        self.assertEqual(settings.rag_embedding_dimensions, 1024)
        self.assertEqual(settings.pdf_parse_strategy, "multimodal_aware_v3")
        self.assertEqual(settings.rag_chunk_strategy, "multimodal_aware_v3")
        self.assertEqual(settings.rag_semantic_timeout_seconds, 8.0)
        self.assertEqual(settings.rag_max_chunks_per_paper, 3)

    def test_semantic_timeout_is_configurable_and_positive(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_RAG_SEMANTIC_TIMEOUT_SECONDS": "2.5",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.rag_semantic_timeout_seconds, 2.5)

    def test_retrieval_diversity_cap_is_configurable(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_RAG_MAX_CHUNKS_PER_PAPER": "5",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.rag_max_chunks_per_paper, 5)

    def test_embedding_cost_is_opt_in_and_non_negative(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_RAG_EMBEDDING_COST_CNY_PER_MILLION_TOKENS": "0.7",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.rag_embedding_cost_cny_per_million_tokens, 0.7)

    def test_previous_parser_strategies_remain_selectable(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_PDF_PARSE_STRATEGY": "structure_aware_v1",
            "SCHOLAR_RAG_CHUNK_STRATEGY": "legacy_fixed",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.pdf_parse_strategy, "structure_aware_v1")
        self.assertEqual(settings.rag_chunk_strategy, "legacy_fixed")

    def test_formula_aware_strategy_remains_selectable(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_PDF_PARSE_STRATEGY": "formula_aware_v2",
            "SCHOLAR_RAG_CHUNK_STRATEGY": "formula_aware_v2",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.pdf_parse_strategy, "formula_aware_v2")
        self.assertEqual(settings.rag_chunk_strategy, "formula_aware_v2")

    def test_hierarchical_v4_strategy_is_selectable(self) -> None:
        env = {
            "SCHOLAR_DATABASE_URL": "postgresql+psycopg://u:p@db/scholar",
            "SCHOLAR_PDF_PARSE_STRATEGY": "scholar_hierarchical_v4",
            "SCHOLAR_RAG_CHUNK_STRATEGY": "scholar_hierarchical_v4",
        }
        with patch.dict(os.environ, env, clear=False), patch(
            "app.config.read_runtime_config", return_value={}
        ):
            settings = get_settings()

        self.assertEqual(settings.pdf_parse_strategy, "scholar_hierarchical_v4")
        self.assertEqual(settings.rag_chunk_strategy, "scholar_hierarchical_v4")

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
